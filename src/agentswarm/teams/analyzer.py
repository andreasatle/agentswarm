"""Analyzer teams: they examine changes on a shared level and report findings, never commits (§10.3).

Only the merge queue writes level branches, so teams above the lowest level are analyzers. An analyzer
examines an artifact at its merge commit, in a throwaway checkout, and ends by signing off the version it
saw, with or without findings. A gate above the level can require that sign-off.

Evidence first (§18, finding floods): a model may only report a problem it backs with a test it wrote
and ran that fails. Problems without such a test are dropped, so a weak claim never blocks anything.
"""

import logging
import re
from abc import ABC
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
from agentswarm.gating.checks import CheckResult, CommandCheck
from agentswarm.judging.judges import JudgeState
from agentswarm.llm.workers import Worker, WorkerError
from agentswarm.teams.relevance import RelevanceCheck, RelevanceFilter
from agentswarm.teams.runner import Relevance
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
        if path.startswith(("/", "..")) or ".." in path.split("/"):
            return "error: path must stay inside the project"
        try:
            return self._worktree.read(path)[: self.READ_LIMIT]
        except (OSError, UnicodeDecodeError) as error:
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
        return f"{'PASSED' if result.passed else 'FAILED'}\n{result.output}"

    def failed(self, name: str) -> bool:
        run = self.runs.get(name)
        return run is not None and not run.passed

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


class Analyzer(ABC):
    """Examines each artifact at its level once per version; subclasses set the instructions and kind."""

    INSTRUCTIONS: ClassVar[str]
    KIND: ClassVar[FindingKind]
    _log = logging.getLogger(__name__)

    def __init__(
        self,
        name: TeamName,
        watches: Level,
        worker: Worker,
        runner: CommandCheck,
        relevance: RelevanceFilter | None = None,
    ) -> None:
        self._name = name
        self._watches = watches
        self._worker = worker
        self._runner = runner
        self._relevance = relevance

    @property
    def name(self) -> TeamName:
        return self._name

    @property
    def watches(self) -> Level:
        return self._watches

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

        toolbox = AnalysisToolbox(worktree, diff, self._runner)
        request = AnalysisRequest(
            north_star=ctx.north_star, task=ctx.artifact.task, diff=JudgeState.truncate(diff)
        )
        try:
            report = await self._worker.run(self.INSTRUCTIONS, request, AnalysisReport, toolbox)
        except WorkerError as error:
            return [Escalated(artifact=ctx.artifact.id, reason=f"{self._name} failed: {error}")]

        findings = [self._finding(ctx, p, toolbox) for p in report.problems if toolbox.failed(p.test)]
        dropped = len(report.problems) - len(findings)
        if dropped:
            self._log.info(
                "%s: dropped %d problem(s) on %s without a failing test", self._name, dropped, ctx.artifact.id
            )
        if self._relevance is not None:
            self._relevance.record_result(
                check, bool(findings), f"{len(findings)} finding(s), {dropped} dropped"
            )
        return [*(FindingAdded(finding=f) for f in findings), sign_off]

    def _finding(self, ctx: ArtifactContext, problem: ReportedProblem, toolbox: AnalysisToolbox) -> Finding:
        return Finding(
            artifact=ctx.artifact.id,
            level=self._watches,
            team=self._name,
            kind=self.KIND,
            severity=Severity.MAJOR if problem.severity == "major" else Severity.MINOR,
            detail=(
                f"{problem.title}: {problem.detail}\n\n"
                f"Failing test ({problem.test}):\n{toolbox.test_source(problem.test)}"
            ),
        )
