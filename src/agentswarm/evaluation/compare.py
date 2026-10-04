"""Comparing setups on the same tasks: do small swarms deliver as often as bigger models? (Phase 8)

An experiment names a task suite and several *setups*, each an ordinary project config. Every task runs
under every setup in a fresh project. A run is scored on what it delivered: after the project is idle, the
task's hidden tests run against the tip of the first shared level, where merged work and routed fixes land.
Tokens come from the `Spent` events on the board, per model, so any price list can be applied later.
"""

import statistics
import time
from collections.abc import Callable, Sequence
from enum import StrEnum
from pathlib import Path
from typing import ClassVar, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field

from agentswarm.core.config import ProjectConfig
from agentswarm.core.domain import Event, Spent, Usage
from agentswarm.evaluation.suite import EvalTask, HiddenTests, TaskSuite
from agentswarm.gating.checks import CheckStatus
from agentswarm.judging.tuning import Wilson
from agentswarm.project import Project, ProjectServices, StillBusy
from agentswarm.workspace.sandbox import Sandboxes


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class RunOrder(StrEnum):
    BY_SETUP = "by_setup"  # every task under one setup, then the next: each model loads once (limited memory)
    BY_TASK = "by_task"  # every setup on one task, then the next: an interrupted run stays balanced


class ExperimentKind(StrEnum):
    BUILD = "build"  # do the setups deliver working code? (hidden tests on what they merged)
    REVIEW = "review"  # do the setups' reviewers catch planted bugs? (see review.py)


class Experiment(Frozen):
    """A suite, the setups to compare on it, and optionally which of its tasks to run."""

    suite: Path
    setups: dict[str, Path] = Field(min_length=1)  # name -> project config
    tasks: tuple[str, ...] = ()  # task ids to run; empty: all of them
    order: RunOrder = RunOrder.BY_SETUP  # build experiments; review runs always go setup by setup
    kind: ExperimentKind = ExperimentKind.BUILD
    bugs_per_task: int = Field(default=1, ge=1)  # review experiments: planted bugs per task
    controls: bool = True  # review experiments: one clean run per task, to count false alarms

    @classmethod
    def load(cls, path: Path) -> Self:
        """Relative paths are resolved against the experiment file."""
        text = path.read_text()
        raw = yaml.safe_load(text)
        loaded = cls.model_validate(raw)
        base = path.parent.resolve()
        suite = (base / loaded.suite).resolve()
        setups = {name: (base / config).resolve() for name, config in loaded.setups.items()}
        missing = [str(p) for p in (suite, *setups.values()) if not p.is_file()]
        if missing:
            raise ValueError(f"experiment {path} refers to missing files: {missing}")
        return loaded.model_copy(update={"suite": suite, "setups": setups})

    def selected(self, suite: TaskSuite) -> list[EvalTask]:
        if not self.tasks:
            return list(suite.tasks)
        known = {t.id for t in suite.tasks}
        unknown = [t for t in self.tasks if t not in known]
        if unknown:
            raise ValueError(f"tasks not in the suite: {unknown}")
        return [t for t in suite.tasks if t.id in self.tasks]


class TokenCount:
    """Team model usage from a project's log, per real model (Jev decisions and the fallback left out)."""

    @staticmethod
    def of(events: Sequence[Event], config: ProjectConfig) -> tuple[int, dict[str, Usage]]:
        """(model runs, tokens per model as "<provider>:<model>")."""
        spent = [e for e in events if isinstance(e, Spent) and not e.by.startswith("jev")]
        tokens: dict[str, Usage] = {}
        for event in spent:
            # By the real model, not the config's name for it: two setups may both call theirs "coder".
            model = config.models[event.model].model if event.model in config.models else event.model
            usage = Usage(input_tokens=event.input_tokens, output_tokens=event.output_tokens)
            before = tokens.get(model, Usage())
            tokens[model] = before + usage
        return len(spent), tokens


class RunMeasure(Frozen):
    """One task under one setup."""

    setup: str
    task: str
    solved: bool | None  # hidden tests on the delivered tip; None when they couldn't run
    model_runs: int  # team model runs (Jev decisions not counted)
    tokens: dict[str, Usage]  # per model, as "<provider>:<model>"
    seconds: float
    escalations: int  # still open at the end
    note: str = ""

    @property
    def total(self) -> Usage:
        usage = Usage()
        for part in self.tokens.values():
            usage = usage + part
        return usage


class SetupSummary(Frozen):
    setup: str
    runs: int
    solved: int
    scored: int  # runs whose hidden tests could run
    solve_rate_low: float  # 95% Wilson interval of the solve rate
    solve_rate_high: float
    tokens_per_task: float  # input + output, mean over all runs
    tokens_per_solved: float | None
    median_seconds: float
    escalated: int  # runs that ended with an open escalation

    @classmethod
    def of(cls, setup: str, measures: Sequence[RunMeasure]) -> Self:
        scored = [m for m in measures if m.solved is not None]
        solved = [m for m in scored if m.solved]
        totals = [m.total.input_tokens + m.total.output_tokens for m in measures]
        all_tokens = sum(totals)
        seconds = [m.seconds for m in measures]
        return cls(
            setup=setup,
            runs=len(measures),
            solved=len(solved),
            scored=len(scored),
            solve_rate_low=Wilson.lower(len(solved), len(scored)),
            solve_rate_high=Wilson.upper(len(solved), len(scored)) if scored else 0.0,
            tokens_per_task=all_tokens / len(measures) if measures else 0.0,
            tokens_per_solved=all_tokens / len(solved) if solved else None,
            median_seconds=statistics.median(seconds) if seconds else 0.0,
            escalated=sum(m.escalations > 0 for m in measures),
        )

    def row(self) -> str:
        rate = f"{self.solved}/{self.scored}" if self.scored else "-"
        interval = f"{self.solve_rate_low:.0%}-{self.solve_rate_high:.0%}"
        per_solved = f"{self.tokens_per_solved:,.0f}" if self.tokens_per_solved is not None else "-"
        return (
            f"{self.setup:<16} {rate:>7} {interval:>9} {self.tokens_per_task:>14,.0f} {per_solved:>14} "
            f"{self.median_seconds:>8.0f} {self.escalated:>9}"
        )


class ComparisonReport(Frozen):
    measures: tuple[RunMeasure, ...]
    summaries: tuple[SetupSummary, ...]

    HEADER: ClassVar[str] = (
        f"{'setup':<16} {'solved':>7} {'95% CI':>9} {'tokens/task':>14} {'tokens/solved':>14} "
        f"{'median s':>8} {'escalated':>9}"
    )

    @classmethod
    def of(cls, measures: Sequence[RunMeasure], setups: Sequence[str]) -> Self:
        summaries = tuple(SetupSummary.of(s, [m for m in measures if m.setup == s]) for s in setups)
        return cls(measures=tuple(measures), summaries=summaries)

    @staticmethod
    def line(measure: RunMeasure) -> str:
        outcome = {True: "solved", False: "failed", None: "error"}[measure.solved]
        total = measure.total
        note = f"  ({measure.note[:80]})" if measure.note else ""
        return (
            f"{measure.setup:<16} {measure.task:<20} {outcome:<7} runs={measure.model_runs} "
            f"tokens={total.input_tokens}+{total.output_tokens} {measure.seconds:.0f}s{note}"
        )

    def render(self) -> str:
        rows = [s.row() for s in self.summaries]
        return "\n".join([self.HEADER, *rows])

    def write(self, path: Path) -> None:
        text = self.model_dump_json(indent=2)
        path.write_text(text + "\n")


type ServicesFor = Callable[[str, EvalTask], ProjectServices]


class ComparisonRun:
    """Runs every selected task of the suite under every setup, each in a fresh project under `workdir`."""

    MAX_ROUNDS = 40

    def __init__(
        self, experiment: Experiment, workdir: Path, services_for: ServicesFor | None = None
    ) -> None:
        self._experiment = experiment
        self._workdir = workdir
        self._services_for = services_for or self.real_services
        self._suite = TaskSuite.load(experiment.suite)
        self._tasks = experiment.selected(self._suite)
        self._configs = {name: ProjectConfig.load(path) for name, path in experiment.setups.items()}

    @staticmethod
    def real_services(setup: str, task: EvalTask) -> ProjectServices:
        return ProjectServices()

    @property
    def setups(self) -> list[str]:
        return list(self._configs)

    async def run(self, report: Callable[[RunMeasure], None] | None = None) -> ComparisonReport:
        """Every task under every setup, in the experiment's order; `report` hears each run as it finishes."""
        measures: list[RunMeasure] = []
        for setup, task in self.schedule():
            measure = await self.run_task(setup, task)
            measures.append(measure)
            if report is not None:
                report(measure)
        return ComparisonReport.of(measures, self.setups)

    def schedule(self) -> list[tuple[str, EvalTask]]:
        """The (setup, task) runs in order. By setup by default, so each setup's models load once."""
        if self._experiment.order is RunOrder.BY_TASK:
            return [(setup, task) for task in self._tasks for setup in self._configs]
        return [(setup, task) for setup in self._configs for task in self._tasks]

    async def run_task(self, setup: str, task: EvalTask) -> RunMeasure:
        root = self._workdir / setup / task.id
        if root.exists():
            raise FileExistsError(f"{root} exists; use a new workdir for a new comparison")
        config = self._configs[setup]
        services = self._services_for(setup, task)
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
            solved = self._delivered(project, config, task, f"{setup}-{task.id}")
            return self._measure(project, config, setup, task, solved, seconds, note)
        finally:
            project.close()

    def _delivered(self, project: Project, config: ProjectConfig, task: EvalTask, name: str) -> bool | None:
        """The hidden tests on the tip of the first shared level: what the setup delivered."""
        tip = project.repository.tip(config.levels.base)
        sandbox = Sandboxes.build(config.sandbox)
        suite = self._suite
        hidden = HiddenTests(suite.test_command, suite.test_timeout_s, sandbox, suite.test_failure_exit_codes)
        result = hidden.run(project.repository, tip, task.hidden_tests, name)
        if result.status is CheckStatus.ERROR:
            return None
        return result.passed

    @staticmethod
    def _measure(
        project: Project,
        config: ProjectConfig,
        setup: str,
        task: EvalTask,
        solved: bool | None,
        seconds: float,
        note: str,
    ) -> RunMeasure:
        envelopes = project.board.log.read()
        events = [e.event for e in envelopes]
        model_runs, tokens = TokenCount.of(events, config)
        views = project.views()
        open_escalations = views.open_escalations()
        return RunMeasure(
            setup=setup,
            task=task.id,
            solved=solved,
            model_runs=model_runs,
            tokens=tokens,
            seconds=round(seconds, 1),
            escalations=len(open_escalations),
            note=note,
        )


class ComparisonFiles:
    """Where a comparison keeps its results: `results.json` in the workdir, every run and the summary."""

    RESULTS = "results.json"

    @classmethod
    def results(cls, workdir: Path) -> Path:
        return workdir / cls.RESULTS
