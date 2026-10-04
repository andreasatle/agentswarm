"""The review benchmark: do a setup's reviewers catch planted bugs? (Phase 8)

For each task, bugs are planted in its reference solution: mutants of the lines the reference changed that
the task's hidden tests catch, so each is a real bug by construction. A scripted executor writes the buggy
reference with a test that passes, so the gate merges it; then the setup's reviewers (its analyzer teams)
examine it. A bug counts as caught when a reviewer reports a finding, and a finding always comes with a
failing test as evidence. Control runs merge the clean reference: a finding there is a false alarm.
"""

import dataclasses
import random
import shutil
import statistics
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import ClassVar, Self

from pydantic import BaseModel, ConfigDict

from agentswarm.core.config import ProjectConfig, TeamConfig
from agentswarm.core.domain import (
    ArtifactContext,
    CommitPushed,
    Event,
    FindingAdded,
    Level,
    TeamName,
    Usage,
)
from agentswarm.evaluation.compare import Experiment, ServicesFor, TokenCount
from agentswarm.evaluation.mutation import ChangedLines, MutantGenerator
from agentswarm.evaluation.suite import EvalTask, HiddenTests, TaskSuite
from agentswarm.gating.checks import CheckStatus
from agentswarm.judging.tuning import Wilson
from agentswarm.project import Project, ProjectServices, StillBusy
from agentswarm.teams.catalog import TeamCatalog, TeamResources
from agentswarm.teams.executor import Executor
from agentswarm.teams.runner import Relevance, Team
from agentswarm.workspace.git import Worktree
from agentswarm.workspace.sandbox import Sandbox, Sandboxes


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class PlantedBug(Frozen):
    """One real bug: the reference with one changed line mutated, which the hidden tests catch."""

    path: str
    line: int
    description: str  # e.g. "'==' -> '!='"
    source: str  # the whole buggy file

    @property
    def label(self) -> str:
        return f"{self.path}:{self.line} {self.description}"


class BugPlanter:
    """Finds mutants of a task's reference that its hidden tests catch, in the sandbox."""

    def __init__(self, suite: TaskSuite, sandbox: Sandbox, scratch: Path, seed: int = 0) -> None:
        self._suite = suite
        self._sandbox = sandbox
        self._scratch = scratch
        self._generator = MutantGenerator()
        self._random = random.Random(seed)

    def plant(self, task: EvalTask, project_seed: Path | None, count: int) -> list[PlantedBug]:
        """Up to `count` bugs, chosen reproducibly among the mutants the hidden tests catch."""
        candidates: list[PlantedBug] = []
        for target, source in sorted(task.reference.items()):
            if not target.endswith(".py") or ChangedLines.is_test(target):
                continue
            reference = source.read_text()
            original_path = project_seed / target if project_seed is not None else None
            original = (
                original_path.read_text() if original_path is not None and original_path.is_file() else ""
            )
            changed = ChangedLines.between(original, reference)
            mutants = self._generator.mutants(target, reference, changed)
            for mutant in mutants:
                bug = PlantedBug(
                    path=mutant.path, line=mutant.line, description=mutant.description, source=mutant.source
                )
                candidates.append(bug)
        self._random.shuffle(candidates)
        bugs: list[PlantedBug] = []
        for bug in candidates:
            if len(bugs) == count:
                break
            if self.caught_by_hidden_tests(task, project_seed, bug):
                bugs.append(bug)
        return bugs

    def caught_by_hidden_tests(self, task: EvalTask, project_seed: Path | None, bug: PlantedBug) -> bool:
        """Whether the hidden tests fail on the buggy reference; an error running them doesn't count."""
        folder = self._scratch / task.id
        shutil.rmtree(folder, ignore_errors=True)
        if project_seed is not None:
            shutil.copytree(project_seed, folder, ignore=shutil.ignore_patterns(".git"))
        folder.mkdir(parents=True, exist_ok=True)
        for target, source in task.reference.items():
            text = bug.source if target == bug.path else source.read_text()
            path = folder / target
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        hidden = folder / HiddenTests.DIRECTORY
        hidden.mkdir(exist_ok=True)
        for test in task.hidden_tests:
            text = test.read_text()
            (hidden / test.name).write_text(text)
        command = (*self._suite.test_command, HiddenTests.DIRECTORY)
        result = self._sandbox.run(command, folder, self._suite.test_timeout_s)
        status = CheckStatus.of(result, self._suite.test_failure_exit_codes)
        shutil.rmtree(folder, ignore_errors=True)
        return status is CheckStatus.FAILED


class PlantingExecutor(Executor):
    """Writes a task's reference, with a planted bug or clean, and a test that passes. No model.

    It leaves fix artifacts alone: the benchmark measures whether reviewers *find* the bug, and a fix the
    router creates for a finding would only be rewritten by this script.
    """

    PASSING_TEST = "def test_it_runs():\n    assert True\n"

    def __init__(self, watches: Level, task: EvalTask, bug: PlantedBug | None) -> None:
        super().__init__(watches, TeamName("executor"))
        self._task = task
        self._bug = bug

    def relevant(self, ctx: ArtifactContext) -> Relevance:
        if ctx.artifact.fixes is not None:
            return Relevance.NO
        return super().relevant(ctx)

    async def act(self, ctx: ArtifactContext, worktree: Worktree) -> list[Event]:
        for target, source in self._task.reference.items():
            buggy = self._bug is not None and target == self._bug.path
            text = self._bug.source if buggy and self._bug is not None else source.read_text()
            worktree.write(target, text)
        test_file = f"tests/test_{self._task.id.replace('-', '_')}.py"
        worktree.write(test_file, self.PASSING_TEST)
        what = self._bug.label if self._bug is not None else "clean"
        sha = worktree.commit(f"{ctx.artifact.id}: reference for {self._task.id} ({what})")
        return [CommitPushed(artifact=ctx.artifact.id, commit=sha)]


class ReviewMeasure(Frozen):
    """One review run: a planted bug (or a clean control) under one setup."""

    setup: str
    task: str
    bug: str | None  # the planted bug's label; None for a control run
    finders: tuple[str, ...]  # reviewer teams that reported a finding
    findings: int
    model_runs: int
    tokens: dict[str, Usage]  # per model, as "<provider>:<model>"
    seconds: float
    escalations: int
    note: str = ""

    @property
    def caught(self) -> bool:
        return bool(self.finders)

    @property
    def total(self) -> Usage:
        usage = Usage()
        for part in self.tokens.values():
            usage = usage + part
        return usage


class ReviewSummary(Frozen):
    setup: str
    bugs: int
    caught: int
    catch_low: float  # 95% Wilson interval of the catch rate
    catch_high: float
    controls: int
    false_alarms: int  # control runs with a finding
    tokens_per_run: float
    median_seconds: float
    by_team: dict[str, int]  # bugs each reviewer team caught

    @classmethod
    def of(cls, setup: str, measures: Sequence[ReviewMeasure]) -> Self:
        bug_runs = [m for m in measures if m.bug is not None]
        control_runs = [m for m in measures if m.bug is None]
        caught = [m for m in bug_runs if m.caught]
        by_team: dict[str, int] = {}
        for measure in caught:
            for team in measure.finders:
                by_team[team] = by_team.get(team, 0) + 1
        totals = [m.total.input_tokens + m.total.output_tokens for m in measures]
        seconds = [m.seconds for m in measures]
        return cls(
            setup=setup,
            bugs=len(bug_runs),
            caught=len(caught),
            catch_low=Wilson.lower(len(caught), len(bug_runs)),
            catch_high=Wilson.upper(len(caught), len(bug_runs)) if bug_runs else 0.0,
            controls=len(control_runs),
            false_alarms=sum(m.caught for m in control_runs),
            tokens_per_run=sum(totals) / len(measures) if measures else 0.0,
            median_seconds=statistics.median(seconds) if seconds else 0.0,
            by_team=dict(sorted(by_team.items())),
        )

    def row(self) -> str:
        rate = f"{self.caught}/{self.bugs}" if self.bugs else "-"
        interval = f"{self.catch_low:.0%}-{self.catch_high:.0%}"
        alarms = f"{self.false_alarms}/{self.controls}" if self.controls else "-"
        teams = ", ".join(f"{team} {n}" for team, n in self.by_team.items()) or "-"
        return (
            f"{self.setup:<16} {rate:>7} {interval:>9} {alarms:>12} {self.tokens_per_run:>12,.0f} "
            f"{self.median_seconds:>8.0f}  {teams}"
        )


class ReviewReport(Frozen):
    measures: tuple[ReviewMeasure, ...]
    summaries: tuple[ReviewSummary, ...]

    HEADER: ClassVar[str] = (
        f"{'setup':<16} {'caught':>7} {'95% CI':>9} {'false alarms':>12} {'tokens/run':>12} "
        f"{'median s':>8}  bugs caught per team"
    )

    @classmethod
    def of(cls, measures: Sequence[ReviewMeasure], setups: Sequence[str]) -> Self:
        summaries = tuple(ReviewSummary.of(s, [m for m in measures if m.setup == s]) for s in setups)
        return cls(measures=tuple(measures), summaries=summaries)

    @staticmethod
    def line(measure: ReviewMeasure) -> str:
        planted = measure.bug or "control (clean)"
        outcome = f"found by {', '.join(measure.finders)}" if measure.finders else "nothing found"
        total = measure.total
        note = f"  ({measure.note[:80]})" if measure.note else ""
        return (
            f"{measure.setup:<16} {measure.task:<18} {planted:<40} {outcome}; "
            f"tokens={total.input_tokens}+{total.output_tokens} {measure.seconds:.0f}s{note}"
        )

    def render(self) -> str:
        rows = [s.row() for s in self.summaries]
        return "\n".join([self.HEADER, *rows])

    def write(self, path: Path) -> None:
        text = self.model_dump_json(indent=2)
        path.write_text(text + "\n")


class ReviewRun:
    """Every selected task under every setup: `bugs_per_task` planted bugs, plus one clean control each."""

    MAX_ROUNDS = 40

    def __init__(
        self, experiment: Experiment, workdir: Path, services_for: ServicesFor | None = None
    ) -> None:
        self._experiment = experiment
        self._workdir = workdir
        self._services_for = services_for or self.real_services
        self._bugs_per_task = experiment.bugs_per_task
        self._controls = experiment.controls
        self._suite = TaskSuite.load(experiment.suite)
        self._tasks = experiment.selected(self._suite)
        self._configs = {name: ProjectConfig.load(path) for name, path in experiment.setups.items()}
        for name, config in self._configs.items():
            if not self.reviewers(config):
                raise ValueError(f"setup {name!r} has no reviewers: no team watches a level above the lowest")
        missing = [t.id for t in self._tasks if not t.reference]
        if missing:
            raise ValueError(f"planting bugs needs reference solutions; tasks without: {missing}")
        self._bugs: dict[str, list[PlantedBug]] = {}

    @staticmethod
    def real_services(setup: str, task: EvalTask) -> ProjectServices:
        return ProjectServices()

    @staticmethod
    def reviewers(config: ProjectConfig) -> list[TeamConfig]:
        lowest = config.levels.lowest
        return [t for t in config.teams if t.watches != lowest]

    @property
    def setups(self) -> list[str]:
        return list(self._configs)

    def bugs_for(self, task: EvalTask) -> list[PlantedBug]:
        """The task's planted bugs, found once and shared by every setup (from the first setup's seed)."""
        if task.id not in self._bugs:
            first = next(iter(self._configs.values()))
            sandbox = Sandboxes.build(first.sandbox)
            scratch = self._workdir / "_planting"
            planter = BugPlanter(self._suite, sandbox, scratch)
            self._bugs[task.id] = planter.plant(task, first.seed, self._bugs_per_task)
        return self._bugs[task.id]

    def schedule(self) -> list[tuple[str, EvalTask, PlantedBug | None]]:
        """Grouped by setup, so each setup's models load once; per task, the control first."""
        runs: list[tuple[str, EvalTask, PlantedBug | None]] = []
        for setup in self._configs:
            for task in self._tasks:
                if self._controls:
                    runs.append((setup, task, None))
                for bug in self.bugs_for(task):
                    runs.append((setup, task, bug))
        return runs

    async def run(self, report: Callable[[ReviewMeasure], None] | None = None) -> ReviewReport:
        measures: list[ReviewMeasure] = []
        for setup, task, bug in self.schedule():
            measure = await self.run_one(setup, task, bug)
            measures.append(measure)
            if report is not None:
                report(measure)
        return ReviewReport.of(measures, self.setups)

    async def run_one(self, setup: str, task: EvalTask, bug: PlantedBug | None) -> ReviewMeasure:
        number = self.bugs_for(task).index(bug) + 1 if bug is not None else 0
        name = "control" if bug is None else f"bug-{number}"
        root = self._workdir / setup / task.id / name
        if root.exists():
            raise FileExistsError(f"{root} exists; use a new workdir for a new review benchmark")
        config = self._configs[setup]
        base = self._services_for(setup, task)

        def planting(team: TeamConfig, resources: TeamResources) -> Team:
            return PlantingExecutor(team.watches, task, bug)

        catalog: TeamCatalog = base.teams.with_team("executor", planting)
        services = dataclasses.replace(base, teams=catalog)
        project = Project.open(config, root, services)
        try:
            project.create_artifact(task.task)
            note = ""
            started = time.monotonic()
            try:
                await project.run_until_idle(self.MAX_ROUNDS)
            except StillBusy as error:
                note = str(error)
            seconds = time.monotonic() - started
            return self._measure(project, config, setup, task, bug, seconds, note)
        finally:
            project.close()

    def _measure(
        self,
        project: Project,
        config: ProjectConfig,
        setup: str,
        task: EvalTask,
        bug: PlantedBug | None,
        seconds: float,
        note: str,
    ) -> ReviewMeasure:
        reviewer_names = {t.use for t in self.reviewers(config)}
        envelopes = project.board.log.read()
        events = [e.event for e in envelopes]
        findings = [e for e in events if isinstance(e, FindingAdded) and e.finding.team in reviewer_names]
        finders = sorted({f.finding.team for f in findings})
        model_runs, tokens = TokenCount.of(events, config)
        views = project.views()
        open_escalations = views.open_escalations()
        return ReviewMeasure(
            setup=setup,
            task=task.id,
            bug=bug.label if bug is not None else None,
            finders=tuple(finders),
            findings=len(findings),
            model_runs=model_runs,
            tokens=tokens,
            seconds=round(seconds, 1),
            escalations=len(open_escalations),
            note=note,
        )
