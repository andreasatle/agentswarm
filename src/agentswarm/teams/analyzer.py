"""Analyzer teams: they examine changes on a shared level and report findings, never commits (§10.3).

Only the merge queue writes level branches, so teams above the lowest level are analyzers. An analyzer
examines an artifact at its merge commit, in a throwaway checkout, and ends by signing off the version it
saw, with or without findings. A gate above the level can require that sign-off.

Evidence first (§18, finding floods): a model may only report a problem it backs with a test it wrote
and ran that fails. Problems without such a test are dropped, so a weak claim never blocks anything.
"""

import logging
import re
from abc import ABC, abstractmethod
from collections.abc import Callable, Sequence
from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict

from agentswarm.core.domain import (
    ArtifactContext,
    Escalated,
    Event,
    Finding,
    FindingAdded,
    FindingKind,
    Level,
    Severity,
    SignedOff,
    TeamName,
)
from agentswarm.gating.checks import CheckResult, CheckStatus, CommandCheck
from agentswarm.judging.judges import JudgeState
from agentswarm.llm.workers import Worker, WorkerError
from agentswarm.teams.relevance import RelevanceCheck, RelevanceFilter
from agentswarm.teams.runner import Relevance
from agentswarm.workspace.files import UnsafePath
from agentswarm.workspace.git import Repository, Worktree


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class AnalysisToolbox:
    """Read-only tools, plus running test files the model writes into a scratch folder of the checkout."""

    DIRECTORY = "_analysis"  # never committed: the checkout is detached and thrown away
    NAME = re.compile(r"^test_[a-z0-9_]+\.py$")
    READ_LIMIT = 100_000

    def __init__(self, worktree: Worktree, diff: str, runner: CommandCheck) -> None:
        self._worktree = worktree
        self._diff = diff
        self._runner = runner
        self.runs: dict[str, CheckResult] = {}

    def tools(self) -> Sequence[Callable[..., str]]:
        return [self.list_files, self.read_file, self.show_change, self.run_test]

    def list_files(self) -> str:
        """List every file in the project, one relative path per line."""
        return "\n".join(f for f in self._worktree.files() if not f.startswith(self.DIRECTORY))

    def read_file(self, path: str) -> str:
        """Return the content of the file at `path`, relative to the project root."""
        try:
            return self._worktree.read(path, self.READ_LIMIT)
        except (OSError, UnicodeDecodeError, UnsafePath) as error:
            return f"error: {error}"

    def show_change(self) -> str:
        """Show the change being examined, as a unified diff."""
        return self._diff[: self.READ_LIMIT]

    def run_test(self, name: str, content: str) -> str:
        """Write a pytest file named `name` (test_*.py) with `content`, run it, and return the output.

        Use it to demonstrate a problem: a test that fails because of the change is the evidence a report
        needs. Run the same name again to replace the file.
        """
        if not self.NAME.match(name):
            return "error: name must look like test_something.py (lower case, digits, underscores)"
        path = f"{self.DIRECTORY}/{name}"
        self._worktree.write(path, content)
        result = self._runner.run_on(self._worktree.path, [path])
        self.runs[name] = result
        if result.status is CheckStatus.ERROR:
            return f"ERROR: the test could not run properly (this is not evidence)\n{result.output}"
        return f"{'PASSED' if result.passed else 'FAILED'}\n{result.output}"

    def failed(self, name: str) -> bool:
        run = self.runs.get(name)
        return run is not None and run.failed  # an error running the test proves nothing

    def test_source(self, name: str) -> str:
        return self._worktree.read(f"{self.DIRECTORY}/{name}")


class ReportedProblem(Frozen):
    title: str  # one line
    detail: str  # what goes wrong and why it matters
    severity: Literal["major", "minor"]
    test: str  # the run_test name that fails because of this problem


class AnalysisReport(Frozen):
    summary: str  # one line: what was examined
    problems: tuple[ReportedProblem, ...]


class AnalysisRequest(Frozen):
    north_star: str
    task: str  # what the change was meant to do
    diff: str  # the change, truncated like the judge's (JudgeState.DIFF_LIMIT)


class Examination(Frozen):
    """What an analyzer's model produced: findings backed by evidence, and how many claims were dropped."""

    findings: tuple[Finding, ...]
    dropped: int


class Analyzer(ABC):
    """Examines each artifact at its level once per version, then signs off.

    The base handles what every analyzer shares: the deterministic relevance rule, the Jev relevance filter,
    the sign-off, escalating when the model fails, and the relevance outcome. Subclasses decide what the
    model may use and what counts as evidence (`examine`): failing tests for code (`ProbingAnalyzer`),
    verbatim quotes for prose (`ReviewingAnalyzer`).
    """

    INSTRUCTIONS: ClassVar[str]
    KIND: ClassVar[FindingKind]
    _log = logging.getLogger(__name__)

    def __init__(
        self, name: TeamName, watches: Level, worker: Worker, relevance: RelevanceFilter | None = None
    ) -> None:
        self._name = name
        self._watches = watches
        self._worker = worker
        self._relevance = relevance

    @property
    def name(self) -> TeamName:
        return self._name

    @property
    def watches(self) -> Level:
        return self._watches

    @property
    def pausable(self) -> bool:
        return False  # checking merged work is never what drift is about

    def relevant(self, ctx: ArtifactContext) -> Relevance:
        """Deterministic: merged into the level, and not yet signed off by this team in this version."""
        if ctx.merge_commit is None or self._name in ctx.signed_off_by:
            return Relevance.NO
        return Relevance.YES

    async def act(self, ctx: ArtifactContext, worktree: Worktree) -> list[Event]:
        assert ctx.merge_commit is not None  # guaranteed by relevant()
        base, head = Repository.merged_change(ctx.merge_commit)
        diff = worktree.diff_between(base, head)
        sign_off = SignedOff(artifact=ctx.artifact.id, team=self._name, version=ctx.version)
        check = (
            self._relevance.check(ctx, ctx.merge_commit, base, diff)
            if self._relevance is not None
            else RelevanceCheck(relevance=Relevance.YES, work=True, decision=None)
        )
        if not check.work:
            return [sign_off]
        request = AnalysisRequest(
            north_star=ctx.north_star, task=ctx.artifact.task, diff=JudgeState.truncate(diff)
        )
        try:
            examination = await self.examine(ctx, worktree, diff, request)
        except WorkerError as error:
            return [Escalated(artifact=ctx.artifact.id, reason=f"{self._name} failed: {error}")]
        if examination.dropped:
            self._log.info(
                "%s: dropped %d problem(s) on %s without evidence",
                self._name,
                examination.dropped,
                ctx.artifact.id,
            )
        if self._relevance is not None:
            summary = f"{len(examination.findings)} finding(s), {examination.dropped} dropped"
            self._relevance.record_result(check, bool(examination.findings), summary)
        return [*(FindingAdded(finding=f) for f in examination.findings), sign_off]

    @abstractmethod
    async def examine(
        self, ctx: ArtifactContext, worktree: Worktree, diff: str, request: AnalysisRequest
    ) -> Examination:
        """Let the model look, keep only problems backed by evidence. May raise WorkerError."""

    def finding(self, ctx: ArtifactContext, severity: Literal["major", "minor"], detail: str) -> Finding:
        return Finding(
            artifact=ctx.artifact.id,
            level=self._watches,
            team=self._name,
            kind=self.KIND,
            severity=Severity.MAJOR if severity == "major" else Severity.MINOR,
            detail=detail,
        )


class ProbingAnalyzer(Analyzer):
    """Evidence is a failing test: the model writes and runs scratch tests (AnalysisToolbox)."""

    def __init__(
        self,
        name: TeamName,
        watches: Level,
        worker: Worker,
        runner: CommandCheck,
        relevance: RelevanceFilter | None = None,
    ) -> None:
        super().__init__(name, watches, worker, relevance)
        self._runner = runner

    async def examine(
        self, ctx: ArtifactContext, worktree: Worktree, diff: str, request: AnalysisRequest
    ) -> Examination:
        toolbox = AnalysisToolbox(worktree, diff, self._runner)
        report = await self._worker.run(self.INSTRUCTIONS, request, AnalysisReport, toolbox)
        findings = tuple(
            self.finding(
                ctx,
                p.severity,
                f"{p.title}: {p.detail}\n\nFailing test ({p.test}):\n{toolbox.test_source(p.test)}",
            )
            for p in report.problems
            if toolbox.failed(p.test)
        )
        return Examination(findings=findings, dropped=len(report.problems) - len(findings))


class ReadingToolbox:
    """Read-only tools for reviewers of text: list and read files, and see the change."""

    READ_LIMIT = 100_000

    def __init__(self, worktree: Worktree, diff: str) -> None:
        self._worktree = worktree
        self._diff = diff

    def tools(self) -> Sequence[Callable[..., str]]:
        return [self.list_files, self.read_file, self.show_change]

    def list_files(self) -> str:
        """List every file in the project, one relative path per line."""
        return "\n".join(self._worktree.files())

    def read_file(self, path: str) -> str:
        """Return the content of the file at `path`, relative to the project root."""
        try:
            return self._worktree.read(path, self.READ_LIMIT)
        except (OSError, UnicodeDecodeError, UnsafePath) as error:
            return f"error: {error}"

    def show_change(self) -> str:
        """Show the change being reviewed, as a unified diff."""
        return self._diff[: self.READ_LIMIT]

    def contains(self, path: str, quote: str) -> bool:
        """Whether `path` holds `quote` verbatim (whitespace runs count as one space)."""
        text = self.read_file(path)
        if text.startswith("error:"):
            return False
        return Quotes.normal(quote) in Quotes.normal(text)


class Quotes:
    MIN_LENGTH = 12  # shorter quotes match too easily to count as evidence

    @staticmethod
    def normal(text: str) -> str:
        return " ".join(text.split())


class QuotedProblem(Frozen):
    title: str  # one line
    detail: str  # what is wrong and why it matters to the reader
    severity: Literal["major", "minor"]
    file: str  # the file the quote is from, relative to the project root
    quote: str  # the exact text the problem is about, copied verbatim (at least a few words)


class ReviewReport(Frozen):
    summary: str
    problems: tuple[QuotedProblem, ...]


class ReviewingAnalyzer(Analyzer):
    """Evidence is a verbatim quote: a problem must cite text that really is in the named file."""

    async def examine(
        self, ctx: ArtifactContext, worktree: Worktree, diff: str, request: AnalysisRequest
    ) -> Examination:
        toolbox = ReadingToolbox(worktree, diff)
        report = await self._worker.run(self.INSTRUCTIONS, request, ReviewReport, toolbox)
        findings = tuple(
            self.finding(ctx, p.severity, f'{p.title}: {p.detail}\n\nIn {p.file}: "{p.quote.strip()}"')
            for p in report.problems
            if len(Quotes.normal(p.quote)) >= Quotes.MIN_LENGTH and toolbox.contains(p.file, p.quote)
        )
        return Examination(findings=findings, dropped=len(report.problems) - len(findings))


class IntegrationTester(ProbingAnalyzer):
    """Tests how the change works together with the rest of the code (§10.3)."""

    KIND: ClassVar[FindingKind] = FindingKind.TEST_FAILURE
    INSTRUCTIONS: ClassVar[str] = """\
You are the integration tester. The change in the data has been merged into the shared code. Find out
whether it works together with the rest of the project.

- Use show_change, list_files and read_file to see what changed and what depends on it.
- Write pytest files with run_test that exercise the changed code through the code that uses it, and
  the code it uses: real call paths, not the new function alone.
- Report a problem only if a test you ran fails because of the change; name that test in the report.
  A passing suite with no problems is a good result: report no problems then.
- Do not report style, naming or missing features the task did not ask for."""


class RedTeam(ProbingAnalyzer):
    """Tries to break the change: edge cases, misuse, unusual input (§10.3)."""

    KIND: ClassVar[FindingKind] = FindingKind.EDGE_CASE
    INSTRUCTIONS: ClassVar[str] = """\
You are the red team. The change in the data has been merged into the shared code. Try to break it.

- Use show_change and read_file to see exactly what the change does.
- Write pytest files with run_test that feed it unusual or hostile input: empty values, huge values,
  wrong types, unexpected characters, repeated calls. Assert what a careful user would expect.
- Report a problem only if a test you ran fails because of the change; name that test in the report.
  Behaviour the task explicitly allowed is not a problem. If nothing breaks, report no problems.
- Do not report style or naming."""
