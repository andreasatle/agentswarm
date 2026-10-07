"""The atomic benchmark: one prompt, one answer, scored by plain code (Phase 8 revision, with the user).

Whole-project comparisons mix a model with the sandbox, the gate, the tool loop and the provider's settings.
A probe measures the model alone: one call with a typed payload that carries all the context (no tools),
one typed answer, and a known truth to score it against.

- `produce`: the task and every file of the seed; the answer gives the new content of the files it
  changes. Solved when the task's hidden tests pass.
- `critique`: the task, the changed code and its diff, either clean (the reference) or with one planted
  bug. Caught when the answer's test fails on the bug and passes on the reference; a test that fails on
  clean code is a false alarm.

Every model answers exactly the same probes. Results go to `<workdir>/results.jsonl` as they come, and a
rerun skips the (model, probe) pairs already there, so an interrupted run resumes.
"""

import difflib
import json
import random
import shutil
import statistics
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from typing import ClassVar, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field

from agentswarm.core.clock import Clock, SystemClock
from agentswarm.core.config import ModelConfig, Price, ProjectConfig, SandboxConfig
from agentswarm.core.domain import Usage
from agentswarm.evaluation.difficulty import DifficultyRatings
from agentswarm.evaluation.review import BugPlanter, PlantedBug
from agentswarm.evaluation.suite import EvalTask, HiddenTests, TaskSuite
from agentswarm.gating.checks import CheckStatus
from agentswarm.judging.tuning import Wilson
from agentswarm.llm.models import ChatModels
from agentswarm.llm.workers import (
    CutOff,
    InvalidAnswer,
    LangChainWorkers,
    Worker,
    WorkerError,
    WorkerProvider,
)
from agentswarm.teams.routing import Bands, CallKind, RoutedCall, RoutingLedger
from agentswarm.workspace.files import ConfinedFiles, UnsafePath
from agentswarm.workspace.sandbox import Sandbox, Sandboxes


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ProbeKind(StrEnum):
    PRODUCE = "produce"
    CRITIQUE = "critique"
    ACCEPT = "accept"  # write the acceptance tests for a task before anyone builds it


class SourceFile(Frozen):
    path: str = Field(description="relative to the project root, e.g. greeter.py or tests/test_greeter.py")
    content: str = Field(description="the complete text of the file")


class ProduceRequest(Frozen):
    """The payload of a produce probe."""

    north_star: str
    task: str
    constraints: tuple[str, ...]
    files: tuple[SourceFile, ...]  # every file of the project as it is


class ProduceAnswer(Frozen):
    """Your change: the complete new content of every file you create or change."""

    files: tuple[SourceFile, ...] = Field(
        description="every file you create or change, each with its complete new content; files you leave "
        "out stay as they are"
    )
    summary: str = Field(description="one line: what you changed")


class CritiqueRequest(Frozen):
    """The payload of a critique probe."""

    north_star: str
    task: str  # what the change was meant to do
    files: tuple[SourceFile, ...]  # the project's files after the change
    diff: str  # the change, as a unified diff


class CritiqueAnswer(Frozen):
    """Your review: whether the change has a bug, and a test that shows it."""

    problem: bool = Field(description="true if the change has a bug: behaviour that contradicts the task")
    explanation: str = Field(description="the bug and why it is one; or why the change is correct")
    test: str = Field(
        description="when problem is true: a complete pytest file that fails because of the bug and would "
        "pass if the code did what the task says; otherwise empty"
    )


@dataclass(frozen=True)
class Probe:
    """One prompt to put to every model, and what is needed to score its answer."""

    id: str  # "<task>/produce", "<task>/clean" or "<task>/<bug label>"
    kind: ProbeKind
    suite: TaskSuite
    north_star: str
    seed: Path | None
    task: EvalTask
    bug: PlantedBug | None  # critique probes only; None is the clean control
    difficulty: float | None = None  # Jev's rating of the task, when the experiment has one
    bugs: tuple[PlantedBug, ...] = ()  # accept probes: the planted bugs the tests should catch


class Outcome(StrEnum):
    SOLVED = "solved"  # produce: the hidden tests pass
    UNSOLVED = "unsolved"
    CAUGHT = "caught"  # critique of a bug: the test fails on the bug and passes on the reference
    MISSED = "missed"
    WRONG_EVIDENCE = "wrong_evidence"  # the test fails on the reference too: it doesn't show the bug
    QUIET = "quiet"  # critique of clean code: no test that fails on it
    FALSE_ALARM = "false_alarm"
    ACCEPTED = "accepted"  # accept: the tests pass on the reference and fail on the seed
    WRONG_REQUIREMENT = (
        "wrong_requirement"  # accept: they fail on the reference, so they ask for more or else
    )
    TESTS_NOTHING = "tests_nothing"  # accept: they pass on the seed, before any work
    INVALID_ANSWER = "invalid_answer"  # the model replied, but never with JSON that fits the schema
    CUT_OFF = "cut_off"  # the reply stopped at the output limit
    NO_ANSWER = "no_answer"  # the provider failed or the call limit was reached

    @property
    def success(self) -> bool:
        return self in (Outcome.SOLVED, Outcome.CAUGHT, Outcome.QUIET, Outcome.ACCEPTED)


class Scored(Frozen):
    outcome: Outcome
    detail: str = ""  # why, for reading the results: test output or the error
    bugs_caught: int | None = None  # accept: how many of the planted bugs the tests catch
    bugs: int | None = None


class ProbeResult(Frozen):
    probe: str
    kind: ProbeKind
    task: str
    bug: str | None
    model: str  # the name in the experiment
    model_id: str  # "<provider>:<model>"
    outcome: Outcome
    claimed: bool | None  # critique: whether the model said there is a problem
    usage: Usage
    seconds: float
    detail: str
    difficulty: float | None = None  # Jev's rating of the task
    answer: str | None = None
    bugs_caught: int | None = None  # accept probes: planted bugs the tests catch
    bugs: int | None = None  # the model's answer as JSON, for reading and for rating it blind later

    @property
    def success(self) -> bool:
        return self.outcome.success


class TaskSample(Frozen):
    """A stratified sample of the suites' tasks: so many from each difficulty band of Jev's ratings.

    The bands are the curves' thirds (easy below 1/3, medium below 2/3, hard above). The choice within a band
    is a seeded shuffle of the sorted task ids, so it doesn't depend on the order of the suites. A band with
    fewer tasks than asked gives all it has; tasks without a rating are left out.
    """

    ratings: Path  # from `agentswarm rate`
    easy: int = Field(ge=0)
    medium: int = Field(ge=0)
    hard: int = Field(ge=0)
    seed: int = 0

    @staticmethod
    def band(difficulty: float) -> int:
        return Bands.of(difficulty)

    def choose(self, candidates: Sequence[str], difficulties: dict[str, float]) -> set[str]:
        wanted = (self.easy, self.medium, self.hard)
        chosen: set[str] = set()
        for band, count in enumerate(wanted):
            members = sorted(
                t for t in candidates if t in difficulties and self.band(difficulties[t]) == band
            )
            random.Random(f"{self.seed}:{band}").shuffle(members)
            chosen.update(members[:count])
        return chosen


class ProbeExperiment(Frozen):
    """Suites to draw probes from, the kinds of probe, and the models to put them to."""

    suites: tuple[Path, ...] = Field(min_length=1)
    kinds: tuple[ProbeKind, ...] = (ProbeKind.PRODUCE, ProbeKind.CRITIQUE)
    tasks: tuple[str, ...] = ()  # task ids to use; empty: every task of every suite
    sample: TaskSample | None = None  # instead of `tasks`: a stratified sample by Jev's difficulty
    bugs_per_task: int = Field(default=2, ge=0)
    controls: bool = True  # critique: one clean probe per task, to count false alarms
    constraints: tuple[str, ...] = ()  # given to every produce probe
    sandbox: SandboxConfig = SandboxConfig()
    models: dict[str, ModelConfig] = Field(min_length=1)
    ledger: Path | None = None  # the routing ledger: each result with a difficulty teaches the router

    @classmethod
    def load(cls, path: Path) -> Self:
        """Paths are relative to the experiment file, or start with ~ for the home folder."""
        loaded = cls.model_validate(yaml.safe_load(path.read_text()))
        base = path.parent.resolve()
        suites = tuple((base / s.expanduser()).resolve() for s in loaded.suites)
        sample = loaded.sample
        if sample is not None:
            sample = sample.model_copy(update={"ratings": (base / sample.ratings.expanduser()).resolve()})
        ledger = (base / loaded.ledger.expanduser()).resolve() if loaded.ledger is not None else None
        files = [*suites, *([sample.ratings] if sample is not None else [])]
        missing = [str(f) for f in files if not f.is_file()]
        if missing:
            raise ValueError(f"probe experiment {path} refers to missing files: {missing}")
        if loaded.tasks and sample is not None:
            raise ValueError(f"probe experiment {path}: give `tasks` or `sample`, not both")
        return loaded.model_copy(update={"suites": suites, "sample": sample, "ledger": ledger})


class TestBench:
    """Lays out a project in a scratch folder and runs tests on it in the sandbox."""

    __test__ = False  # not a pytest class
    HIDDEN = HiddenTests.DIRECTORY
    PROBE = "_probe"  # where a critique's test goes, outside the project's own packages
    PROBE_TEST = "test_probe.py"
    OUTPUT_LIMIT = 2000

    def __init__(self, sandbox: Sandbox, scratch: Path) -> None:
        self._sandbox = sandbox
        self._scratch = scratch

    def lay_out(self, probe: Probe, overlay: dict[str, str]) -> Path:
        """A fresh copy of the seed with `overlay` (path -> content) written over it."""
        folder = self._scratch / probe.id.replace("/", "_").replace(" ", "_").replace(":", "_")
        shutil.rmtree(folder, ignore_errors=True)
        if probe.seed is not None:
            shutil.copytree(probe.seed, folder, ignore=shutil.ignore_patterns(".git"))
        folder.mkdir(parents=True, exist_ok=True)
        files = ConfinedFiles(folder)
        for path, content in overlay.items():
            files.write(path, content)  # raises UnsafePath for a path that leaves the folder
        return folder

    def hidden_tests(self, probe: Probe, folder: Path) -> Scored:
        for test in probe.task.hidden_tests:
            ConfinedFiles(folder).write(f"{self.HIDDEN}/{test.name}", test.read_text())
        return self._run(probe, folder, self.HIDDEN, Outcome.SOLVED, Outcome.UNSOLVED)

    def probe_test(self, probe: Probe, folder: Path, test: str) -> CheckStatus:
        ConfinedFiles(folder).write(f"{self.PROBE}/{self.PROBE_TEST}", test)
        command = (*probe.suite.test_command, self.PROBE)
        result = self._sandbox.run(command, folder, probe.suite.test_timeout_s)
        return CheckStatus.of(result, probe.suite.test_failure_exit_codes)

    def _run(self, probe: Probe, folder: Path, target: str, passed: Outcome, failed: Outcome) -> Scored:
        command = (*probe.suite.test_command, target)
        result = self._sandbox.run(command, folder, probe.suite.test_timeout_s)
        status = CheckStatus.of(result, probe.suite.test_failure_exit_codes)
        outcome = passed if status is CheckStatus.PASSED else failed
        return Scored(outcome=outcome, detail=result.output[-self.OUTPUT_LIMIT :])

    def clean_up(self) -> None:
        shutil.rmtree(self._scratch, ignore_errors=True)


class ProjectFiles:
    """Text files of a project folder, and diffs between versions of them."""

    SKIPPED = ("__pycache__",)

    @classmethod
    def of(cls, folder: Path | None) -> dict[str, str]:
        if folder is None:
            return {}
        files: dict[str, str] = {}
        for path in sorted(folder.rglob("*")):
            relative = path.relative_to(folder)
            hidden = any(part.startswith(".") or part in cls.SKIPPED for part in relative.parts)
            if path.is_file() and not hidden:
                files[relative.as_posix()] = path.read_text()
        return files

    @staticmethod
    def sources(files: dict[str, str]) -> tuple[SourceFile, ...]:
        return tuple(SourceFile(path=path, content=content) for path, content in sorted(files.items()))

    @staticmethod
    def diff(before: dict[str, str], after: dict[str, str]) -> str:
        parts: list[str] = []
        for path in sorted(after):
            old = before.get(path, "")
            if old == after[path]:
                continue
            lines = difflib.unified_diff(
                old.splitlines(keepends=True),
                after[path].splitlines(keepends=True),
                fromfile=f"a/{path}" if path in before else "/dev/null",
                tofile=f"b/{path}",
            )
            parts.append("".join(lines))
        return "\n".join(parts)


class Producing:
    """Produce probes: implement the task in one answer, without tools."""

    KIND = ProbeKind.PRODUCE
    ANSWER = ProduceAnswer
    INSTRUCTIONS = """\
You implement one task in a small Python project, in a single answer. The data holds the task, the
project's constraints and every file of the project.

- Answer with the complete new content of every file you create or change. Files you leave out stay as
  they are; a partial file replaces the whole file.
- Make the smallest change that fully accomplishes the task. Do not change unrelated code.
- Follow every rule in `constraints`.
- Add or update tests for the behaviour you change, in the project's tests/ folder."""

    def __init__(self, constraints: Sequence[str]) -> None:
        self._constraints = tuple(constraints)

    def request(self, probe: Probe) -> ProduceRequest:
        files = ProjectFiles.sources(ProjectFiles.of(probe.seed))
        return ProduceRequest(
            north_star=probe.north_star, task=probe.task.task, constraints=self._constraints, files=files
        )

    def score(self, probe: Probe, answer: ProduceAnswer, bench: TestBench) -> Scored:
        overlay = {f.path: f.content for f in answer.files}
        try:
            folder = bench.lay_out(probe, overlay)
        except UnsafePath as error:
            return Scored(outcome=Outcome.UNSOLVED, detail=f"unsafe path in the answer: {error}")
        return bench.hidden_tests(probe, folder)


class Critiquing:
    """Critique probes: find the bug in a change, if there is one, and prove it with a failing test."""

    KIND = ProbeKind.CRITIQUE
    ANSWER = CritiqueAnswer
    INSTRUCTIONS = """\
You review one change to a small Python project, in a single answer. The data holds the task the change
was meant to accomplish, every file of the project after the change, and the change as a diff.

- Decide whether the change has a bug: behaviour that contradicts the task.
- If it has, set problem to true, explain the bug, and write a complete pytest file that fails because of
  the bug and would pass if the code did what the task says. Import the code the way the project's own
  tests do. The test runs from the project root.
- If you find no bug, set problem to false and leave test empty. Correct changes are common: do not invent
  problems.
- Review the behaviour of the code only. Whether the change added its own tests is not part of this review.
- Do not report style, naming, or features the task did not ask for."""

    @staticmethod
    def reviewed(probe: Probe) -> dict[str, str]:
        """The project after the change: the seed with the reference written over it (the bug planted)."""
        files = ProjectFiles.of(probe.seed)
        bug = probe.bug
        for target, source in probe.task.reference.items():
            files[target] = bug.source if bug is not None and target == bug.path else source.read_text()
        return files

    @staticmethod
    def reference(probe: Probe) -> dict[str, str]:
        return {target: source.read_text() for target, source in probe.task.reference.items()}

    def request(self, probe: Probe) -> CritiqueRequest:
        after = self.reviewed(probe)
        diff = ProjectFiles.diff(ProjectFiles.of(probe.seed), after)
        return CritiqueRequest(
            north_star=probe.north_star, task=probe.task.task, files=ProjectFiles.sources(after), diff=diff
        )

    def score(self, probe: Probe, answer: CritiqueAnswer, bench: TestBench) -> Scored:
        quiet = Outcome.QUIET if probe.bug is None else Outcome.MISSED
        if not answer.problem or not answer.test.strip():
            return Scored(outcome=quiet, detail=answer.explanation)
        on_clean = bench.probe_test(probe, bench.lay_out(probe, self.reference(probe)), answer.test)
        if probe.bug is None:
            alarm = on_clean is CheckStatus.FAILED  # a test that can't run proves nothing: dropped
            return Scored(outcome=Outcome.FALSE_ALARM if alarm else Outcome.QUIET, detail=answer.explanation)
        buggy = {probe.bug.path: probe.bug.source}
        on_bug = bench.probe_test(probe, bench.lay_out(probe, self.reference(probe) | buggy), answer.test)
        if on_bug is not CheckStatus.FAILED:
            return Scored(outcome=Outcome.MISSED, detail=f"the test does not fail on the bug ({on_bug})")
        if on_clean is not CheckStatus.PASSED:
            return Scored(
                outcome=Outcome.WRONG_EVIDENCE, detail=f"the test fails on the reference too ({on_clean})"
            )
        return Scored(outcome=Outcome.CAUGHT, detail=answer.explanation)


class AcceptRequest(Frozen):
    """The payload of an accept probe."""

    north_star: str
    task: str
    files: tuple[SourceFile, ...]  # every file of the project as it is, before the task is done


class AcceptAnswer(Frozen):
    """The acceptance tests for the task: written before the work, by someone other than its producer."""

    test: str = Field(description="a complete pytest file that checks every requirement the task states")
    summary: str = Field(description="one line: what the tests cover")


class Accepting:
    """Accept probes: write a task's acceptance tests before it is built, judged against the known answer."""

    KIND = ProbeKind.ACCEPT
    ANSWER = AcceptAnswer
    INSTRUCTIONS = """\
You write the acceptance tests for one task in a small Python project, before anyone implements it, in a
single answer. The data holds the task and every file of the project as it is now.

- Write one complete pytest file that checks every requirement the task states: one test per stated rule
  or example, with the exact expected values.
- Test only through the public interface the task describes (functions, classes, commands), importing the
  code the way the project's own tests do. The test runs from the project root.
- The tests must fail now, because the feature doesn't exist yet, and pass once it is done correctly.
- Do not test anything the task doesn't ask for, and do not implement the feature."""

    def request(self, probe: Probe) -> AcceptRequest:
        files = ProjectFiles.sources(ProjectFiles.of(probe.seed))
        return AcceptRequest(north_star=probe.north_star, task=probe.task.task, files=files)

    def score(self, probe: Probe, answer: AcceptAnswer, bench: TestBench) -> Scored:
        """Right tests pass on the reference, fail on the untouched seed, and catch the planted bugs."""
        reference = Critiquing.reference(probe)
        on_reference = bench.probe_test(probe, bench.lay_out(probe, reference), answer.test)
        on_seed = bench.probe_test(probe, bench.lay_out(probe, {}), answer.test)
        caught = 0
        for bug in probe.bugs:
            buggy = reference | {bug.path: bug.source}
            if bench.probe_test(probe, bench.lay_out(probe, buggy), answer.test) is CheckStatus.FAILED:
                caught += 1
        counts = {"bugs_caught": caught, "bugs": len(probe.bugs)}
        if on_reference is not CheckStatus.PASSED:
            return Scored(
                outcome=Outcome.WRONG_REQUIREMENT, detail=f"fails on the reference ({on_reference})", **counts
            )
        if on_seed is CheckStatus.PASSED:
            return Scored(outcome=Outcome.TESTS_NOTHING, detail="passes before the work is done", **counts)
        return Scored(outcome=Outcome.ACCEPTED, detail=answer.summary, **counts)


type ProbeHandler = Producing | Critiquing | Accepting


class ProbeSet:
    """Builds every probe of an experiment; planted bugs are cached in the workdir so a rerun reuses them."""

    BUGS = "bugs.json"

    def __init__(self, experiment: ProbeExperiment, sandbox: Sandbox, workdir: Path) -> None:
        self._experiment = experiment
        self._sandbox = sandbox
        self._workdir = workdir

    def build(self) -> list[Probe]:
        cache = self._workdir / self.BUGS
        bugs: dict[str, list[PlantedBug]] = {}
        if cache.is_file():
            raw: dict[str, list[dict[str, object]]] = json.loads(cache.read_text())
            bugs = {task: [PlantedBug.model_validate(b) for b in found] for task, found in raw.items()}
        suites = [TaskSuite.load(path) for path in self._experiment.suites]
        sample = self._experiment.sample
        difficulties: dict[str, float] = {}
        wanted = set(self._experiment.tasks)
        if sample is not None:
            difficulties = {t: r.difficulty for t, r in DifficultyRatings(sample.ratings).done().items()}
            wanted = sample.choose([t.id for s in suites for t in s.tasks], difficulties)
        probes: list[Probe] = []
        for suite in suites:
            config = ProjectConfig.load(suite.project)
            planter = BugPlanter(suite, self._sandbox, self._workdir / "_planting")
            for task in suite.tasks:
                if wanted and task.id not in wanted:
                    continue
                found = self.probes_for(suite, config, task, planter, bugs)
                probes += [replace(p, difficulty=difficulties.get(task.id)) for p in found]
        cache.write_text(
            json.dumps({t: [b.model_dump() for b in found] for t, found in bugs.items()}, indent=2)
        )
        return probes

    def probes_for(
        self,
        suite: TaskSuite,
        config: ProjectConfig,
        task: EvalTask,
        planter: BugPlanter,
        bugs: dict[str, list[PlantedBug]],
    ) -> list[Probe]:
        def probe(id: str, kind: ProbeKind, bug: PlantedBug | None) -> Probe:
            return Probe(id, kind, suite, config.north_star, config.seed, task, bug)

        probes: list[Probe] = []
        if ProbeKind.PRODUCE in self._experiment.kinds:
            probes.append(probe(f"{task.id}/produce", ProbeKind.PRODUCE, None))
        needs_bugs = {ProbeKind.CRITIQUE, ProbeKind.ACCEPT} & set(self._experiment.kinds)
        if needs_bugs and task.reference and task.id not in bugs:
            bugs[task.id] = planter.plant(task, config.seed, self._experiment.bugs_per_task)
        if ProbeKind.CRITIQUE in self._experiment.kinds and task.reference:
            if self._experiment.controls:
                probes.append(probe(f"{task.id}/clean", ProbeKind.CRITIQUE, None))
            probes += [probe(f"{task.id}/{b.label}", ProbeKind.CRITIQUE, b) for b in bugs[task.id]]
        if ProbeKind.ACCEPT in self._experiment.kinds and task.reference:
            accept = probe(f"{task.id}/accept", ProbeKind.ACCEPT, None)
            probes.append(replace(accept, bugs=tuple(bugs[task.id])))
        return probes


class UsageTally:
    """A worker's meter that adds up the usage of one probe at a time."""

    def __init__(self) -> None:
        self.usage = Usage()

    def __call__(self, usage: Usage) -> None:
        self.usage = self.usage + usage

    def take(self) -> Usage:
        usage = self.usage
        self.usage = Usage()
        return usage


class ProbeSummary(Frozen):
    model: str
    model_id: str
    kind: ProbeKind
    probes: int
    successes: int
    low: float  # 95% Wilson interval of the success rate
    high: float
    invalid_answers: int  # replied, but never with JSON that fits the schema
    cut_off: int  # stopped at the output limit
    no_answer: int  # the provider failed or the call limit was reached
    false_alarms: int  # critique: clean code reported, with a test that fails on it
    wrong_evidence: int  # critique: a test that fails on the reference too
    median_tokens: int
    median_seconds: float
    usd: float | None  # all these probes together; None when the model has no price

    @classmethod
    def of(
        cls, model: str, kind: ProbeKind, results: Sequence[ProbeResult], price: Price | None = None
    ) -> Self:
        successes = sum(r.success for r in results)
        usd = (
            round(sum(price.usd(r.usage.input_tokens, r.usage.output_tokens) for r in results), 4)
            if price is not None
            else None
        )
        tokens = [r.usage.input_tokens + r.usage.output_tokens for r in results]
        return cls(
            model=model,
            model_id=results[0].model_id,
            kind=kind,
            probes=len(results),
            successes=successes,
            low=Wilson.lower(successes, len(results)),
            high=Wilson.upper(successes, len(results)),
            invalid_answers=sum(r.outcome is Outcome.INVALID_ANSWER for r in results),
            cut_off=sum(r.outcome is Outcome.CUT_OFF for r in results),
            no_answer=sum(r.outcome is Outcome.NO_ANSWER for r in results),
            false_alarms=sum(r.outcome is Outcome.FALSE_ALARM for r in results),
            wrong_evidence=sum(r.outcome is Outcome.WRONG_EVIDENCE for r in results),
            median_tokens=int(statistics.median(tokens)),
            median_seconds=round(statistics.median(r.seconds for r in results), 1),
            usd=usd,
        )

    def row(self) -> str:
        rate = f"{self.successes}/{self.probes}"
        ci = f"{self.low:.0%}-{self.high:.0%}"
        return (
            f"{self.model:<14} {self.kind:<9} {rate:>7} {ci:>9} {self.invalid_answers:>7} {self.cut_off:>7} "
            f"{self.no_answer:>9} {self.false_alarms:>12} "
            f"{self.wrong_evidence:>14} {self.median_tokens:>10,} {self.median_seconds:>8} "
            f"{f'${self.usd:.2f}' if self.usd is not None else '-':>8}"
        )


class ProbeReport(Frozen):
    results: tuple[ProbeResult, ...]
    summaries: tuple[ProbeSummary, ...]

    HEADER: ClassVar[str] = (
        f"{'model':<14} {'kind':<9} {'success':>7} {'95% CI':>9} {'invalid':>7} {'cut off':>7} "
        f"{'no answer':>9} {'false alarms':>12} "
        f"{'wrong evidence':>14} {'tokens':>10} {'median s':>8} {'cost':>8}"
    )

    @classmethod
    def of(cls, results: Sequence[ProbeResult], models: Mapping[str, ModelConfig]) -> Self:
        summaries: list[ProbeSummary] = []
        for model, config in models.items():
            for kind in ProbeKind:
                mine = [r for r in results if r.model == model and r.kind is kind]
                if mine:
                    summaries.append(ProbeSummary.of(model, kind, mine, config.usd_per_million))
        return cls(results=tuple(results), summaries=tuple(summaries))

    @staticmethod
    def line(result: ProbeResult) -> str:
        tokens = f"tokens={result.usage.input_tokens}+{result.usage.output_tokens}"
        return f"{result.model:<14} {result.probe:<48} {result.outcome:<14} {tokens} {result.seconds:.0f}s"

    def render(self) -> str:
        return "\n".join([self.HEADER, *(s.row() for s in self.summaries)])


class ProbeFiles:
    """`results.jsonl` (one result per line, appended as they come) and `summary.json` in the workdir."""

    RESULTS = "results.jsonl"
    SUMMARY = "summary.json"

    def __init__(self, workdir: Path) -> None:
        self._workdir = workdir

    def done(self) -> list[ProbeResult]:
        path = self._workdir / self.RESULTS
        if not path.is_file():
            return []
        return [ProbeResult.model_validate_json(line) for line in path.read_text().splitlines() if line]

    def append(self, result: ProbeResult) -> None:
        with (self._workdir / self.RESULTS).open("a") as file:
            file.write(result.model_dump_json() + "\n")

    def write_summary(self, report: ProbeReport) -> None:
        summaries = [s.model_dump(mode="json") for s in report.summaries]
        (self._workdir / self.SUMMARY).write_text(json.dumps(summaries, indent=2) + "\n")


class ProbeRun:
    """Puts every probe to every model, one model at a time (each loads once), and scores the answers."""

    # One call answers a probe: there are no tools. A model that replies in plain text instead still gets the
    # worker's final-answer request, and a reply cut off at max_tokens ends the probe after one capped reply.
    MAX_MODEL_CALLS = 1

    def __init__(
        self,
        experiment: ProbeExperiment,
        workdir: Path,
        workers: WorkerProvider | None = None,  # None: the experiment's real models
        sandbox: Sandbox | None = None,  # None: the experiment's sandbox
        stopwatch: Callable[[], float] = time.monotonic,
        clock: Clock | None = None,
    ) -> None:
        self._clock = clock or SystemClock()
        self._ledger = RoutingLedger(experiment.ledger) if experiment.ledger is not None else None
        self._experiment = experiment
        self._workdir = workdir
        self._workers = workers or LangChainWorkers(ChatModels(dict(experiment.models)))
        self._sandbox = sandbox or Sandboxes.build(experiment.sandbox)
        self._stopwatch = stopwatch
        self._handlers: dict[ProbeKind, ProbeHandler] = {
            ProbeKind.PRODUCE: Producing(experiment.constraints),
            ProbeKind.CRITIQUE: Critiquing(),
            ProbeKind.ACCEPT: Accepting(),
        }
        self._files = ProbeFiles(workdir)

    async def run(self, report: Callable[[ProbeResult], None] | None = None) -> ProbeReport:
        self._workdir.mkdir(parents=True, exist_ok=True)
        probes = ProbeSet(self._experiment, self._sandbox, self._workdir).build()
        results = self._files.done()
        done = {(r.model, r.probe) for r in results}
        bench = TestBench(self._sandbox, self._workdir / "_bench")
        for model in self._experiment.models:
            tally = UsageTally()
            worker = self._workers.worker(model, self.MAX_MODEL_CALLS, tally)
            for probe in probes:
                if (model, probe.id) in done:
                    continue
                result = await self.run_one(model, probe, worker, tally, bench)
                self._files.append(result)
                self._teach(result)
                results.append(result)
                if report is not None:
                    report(result)
        bench.clean_up()
        final = ProbeReport.of(results, self._experiment.models)
        self._files.write_summary(final)
        return final

    async def run_one(
        self, model: str, probe: Probe, worker: Worker, tally: UsageTally, bench: TestBench
    ) -> ProbeResult:
        """The time recorded is the model's, not the scoring's."""
        handler = self._handlers[probe.kind]
        request = handler.request(probe)
        start = self._stopwatch()
        claimed: bool | None = None
        answered: str | None = None
        try:
            answer = await worker.run(handler.INSTRUCTIONS, request, handler.ANSWER)
        except WorkerError as error:
            seconds = self._stopwatch() - start
            scored = Scored(outcome=self.failure(error), detail=str(error)[: TestBench.OUTPUT_LIMIT])
        else:
            seconds = self._stopwatch() - start
            scored = self._score(handler, probe, answer, bench)
            claimed = answer.problem if isinstance(answer, CritiqueAnswer) else None
            answered = answer.model_dump_json()
        return ProbeResult(
            probe=probe.id,
            kind=probe.kind,
            task=probe.task.id,
            bug=probe.bug.label if probe.bug is not None else None,
            model=model,
            model_id=self._experiment.models[model].model,
            outcome=scored.outcome,
            claimed=claimed,
            usage=tally.take(),
            seconds=round(seconds, 2),
            detail=scored.detail,
            difficulty=probe.difficulty,
            answer=answered,
            bugs_caught=scored.bugs_caught,
            bugs=scored.bugs,
        )

    KINDS: ClassVar[dict[ProbeKind, CallKind]] = {
        ProbeKind.PRODUCE: CallKind.PRODUCE,
        ProbeKind.CRITIQUE: CallKind.CRITIQUE,
        ProbeKind.ACCEPT: CallKind.ACCEPT,
    }

    def _teach(self, result: ProbeResult) -> None:
        """A result with a known difficulty goes to the routing ledger; a provider failure says nothing about
        the model, so it is left out. Invalid and cut-off answers count as failures: they are the model's."""
        if self._ledger is None or result.difficulty is None or result.outcome is Outcome.NO_ANSWER:
            return
        call = RoutedCall(
            kind=self.KINDS[result.kind],
            band=Bands.of(result.difficulty),
            model_id=result.model_id,
            success=result.success,
            attributes={"simple": round(1.0 - result.difficulty, 4)},
            source="probe",
            at=self._clock.now(),
        )
        self._ledger.record(call)

    @staticmethod
    def failure(error: WorkerError) -> Outcome:
        if isinstance(error, InvalidAnswer):
            return Outcome.INVALID_ANSWER
        if isinstance(error, CutOff):
            return Outcome.CUT_OFF
        return Outcome.NO_ANSWER

    @staticmethod
    def _score(handler: ProbeHandler, probe: Probe, answer: BaseModel, bench: TestBench) -> Scored:
        if isinstance(handler, Producing) and isinstance(answer, ProduceAnswer):
            return handler.score(probe, answer, bench)
        if isinstance(handler, Critiquing) and isinstance(answer, CritiqueAnswer):
            return handler.score(probe, answer, bench)
        if isinstance(handler, Accepting) and isinstance(answer, AcceptAnswer):
            return handler.score(probe, answer, bench)
        raise TypeError(f"{type(answer).__name__} doesn't fit {type(handler).__name__}")
