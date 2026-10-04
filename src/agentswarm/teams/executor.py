"""Executor teams: they implement an artifact's task on its raw branch (proposal §10.3)."""

from abc import ABC, abstractmethod
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict

from agentswarm.core.domain import (
    ArtifactContext,
    CommitPushed,
    Escalated,
    EscalationResolved,
    Event,
    Instruction,
    Level,
    Promoted,
    Rejected,
    Resolution,
    Routed,
    Sized,
    TeamName,
)
from agentswarm.gating.checks import CheckResult, HardCheck
from agentswarm.llm.workers import Worker, WorkerError
from agentswarm.teams.routing import Router
from agentswarm.teams.runner import Relevance
from agentswarm.workspace.files import UnsafePath
from agentswarm.workspace.git import Worktree


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class CheckReport(Frozen):
    results: tuple[CheckResult, ...]

    @property
    def passed(self) -> bool:
        return all(r.passed for r in self.results)

    def summary(self) -> str:
        if not self.results:
            return "no checks configured"
        return "\n\n".join(
            f"{r.name}: {'passed' if r.passed else r.status.upper()}" + ("" if r.passed else f"\n{r.output}")
            for r in self.results
        )


class WorktreeToolbox:
    """The executor's tools, confined to one worktree. Checks run in the project's sandbox.

    Each public method is a tool: its signature and docstring are what the model sees. Errors are
    returned as text so the model can correct itself, never raised into the agent loop.
    """

    READ_LIMIT = 100_000  # characters

    def __init__(self, worktree: Worktree, checks: Sequence[HardCheck]) -> None:
        self._worktree = worktree
        self._checks = tuple(checks)

    def tools(self) -> Sequence[Callable[..., str]]:
        return [self.list_files, self.read_file, self.write_file, self.delete_file, self.run_checks]

    def list_files(self) -> str:
        """List every file in the project, one relative path per line."""
        return "\n".join(self._worktree.files()) or "(the project has no files yet)"

    def read_file(self, path: str) -> str:
        """Return the full content of the file at `path`, relative to the project root."""
        try:
            content = self._worktree.read(path, self.READ_LIMIT + 1)
        except (OSError, UnicodeDecodeError, UnsafePath) as error:
            return f"error: {error}"
        if len(content) > self.READ_LIMIT:
            return content[: self.READ_LIMIT] + f"\n... (truncated at {self.READ_LIMIT} characters)"
        return content

    def write_file(self, path: str, content: str) -> str:
        """Create or overwrite the file at `path`, relative to the project root, with `content`."""
        try:
            relative = self._worktree.relative(path)
            self._worktree.write(relative, content)
        except (OSError, UnsafePath) as error:
            return f"error: {error}"
        return f"wrote {relative} ({len(content)} characters)"

    def delete_file(self, path: str) -> str:
        """Delete the file at `path`, relative to the project root."""
        try:
            relative = self._worktree.relative(path)
            self._worktree.delete(relative)
        except (OSError, UnsafePath) as error:
            return f"error: {error}"
        return f"deleted {relative}"

    def run_checks(self) -> str:
        """Run the project's checks (such as the test suite) and report which passed and why others failed."""
        return self.check_report().summary()

    def check_report(self) -> CheckReport:
        return CheckReport(results=tuple(check.run(self._worktree.path) for check in self._checks))


class ExecutorRequest(Frozen):
    """The payload an executor's model receives."""

    north_star: str
    instruction: Instruction
    files: tuple[str, ...]
    diff_summary: str  # what the branch already changes relative to the level it forked from


class ExecutorReport(Frozen):
    """What the model returns when it has finished working."""

    summary: str  # one line: what was changed; used as the commit message
    complete: bool  # whether the model believes every part of the task is done


class Executor(ABC):
    """Shared by all executors: acts when an artifact has no commit yet, or its latest commit was rejected.

    In a project with a decomposition team (`requires_sizing`), it also waits until the artifact has been
    sized as fitting one branch; fix artifacts are small by construction and need no sizing.
    """

    def __init__(self, watches: Level, name: TeamName, requires_sizing: bool = False) -> None:
        self._watches = watches
        self._name = name
        self._requires_sizing = requires_sizing

    @property
    def name(self) -> TeamName:
        return self._name

    @property
    def watches(self) -> Level:
        return self._watches

    @property
    def pausable(self) -> bool:
        return True

    def relevant(self, ctx: ArtifactContext) -> Relevance:
        sized = [e for e in ctx.history if isinstance(e, Sized)]
        if any(not s.fits for s in sized):
            return Relevance.NO  # an umbrella: its children do the work
        if self._requires_sizing and ctx.artifact.fixes is None and not sized:
            return Relevance.NO  # wait for the decomposition team
        milestones = [
            e
            for e in ctx.history
            if isinstance(e, CommitPushed | Rejected | Promoted | Escalated | EscalationResolved)
        ]
        if not milestones or isinstance(milestones[-1], Rejected):
            return Relevance.YES
        last = milestones[-1]
        if isinstance(last, EscalationResolved) and last.resolution is Resolution.RETRY:
            return Relevance.YES
        return Relevance.NO

    @abstractmethod
    async def act(self, ctx: ArtifactContext, worktree: Worktree) -> list[Event]: ...


class FakeExecutor(Executor):
    """An executor without a model: writes the task (and the findings it was asked to fix) to a file."""

    def __init__(
        self, watches: Level, name: TeamName = TeamName("executor"), requires_sizing: bool = False
    ) -> None:
        super().__init__(watches, name, requires_sizing)

    async def act(self, ctx: ArtifactContext, worktree: Worktree) -> list[Event]:
        artifact = ctx.artifact
        lines = [f"# {artifact.task}", ""] + [f"- fixed: {f.detail}" for f in ctx.open_findings]
        worktree.write(f"{artifact.id}.md", "\n".join(lines) + "\n")
        sha = worktree.commit(f"{artifact.id}: {artifact.task}")
        return [CommitPushed(artifact=artifact.id, commit=sha)]


@dataclass(frozen=True)
class Tier:
    model: str  # the name in the project's `models`
    worker: Worker


class ExecutorTeam(Executor):
    """An executor backed by a model that edits the worktree with tools and runs the checks itself.

    After the model finishes, the executor runs the checks once more, deterministically. Work that passes
    is committed and announced for the gate. Work that still fails, or that the model calls incomplete,
    is committed but escalated, so it is kept for a human and the gate doesn't judge it.
    """

    INSTRUCTIONS = """\
You are the executor in a software team. Implement the task in `instruction.goal` in the project.

- Use list_files and read_file to understand the project before changing it.
- Make the smallest change that fully accomplishes the task. Do not change unrelated code.
- Fix every finding in `instruction.must_fix`; they explain why the previous attempt was rejected.
- Follow every rule in `instruction.constraints`.
- Add or update tests for the behaviour you change.
- Run run_checks after your changes and keep fixing until every check passes.
- When you are done, give your final answer in the required structured format: a one-line summary
  of the change, and whether the task is complete. Do not finish with a plain-text reply."""

    def __init__(
        self,
        workers: Worker | Sequence[Tier],  # one worker, or tiers from cheap to strong (§13.3)
        checks: Sequence[HardCheck],
        watches: Level,
        constraints: Sequence[str] = (),
        name: TeamName = TeamName("executor"),
        requires_sizing: bool = False,
        router: Router | None = None,  # chooses the starting tier by the task's difficulty (Phase 8)
    ) -> None:
        super().__init__(watches, name, requires_sizing)
        self._router = router
        self._tiers: tuple[Tier, ...] | None = tuple(workers) if isinstance(workers, Sequence) else None
        self._single = workers if not isinstance(workers, Sequence) else None
        if self._tiers is not None and not self._tiers:
            raise ValueError("an executor needs at least one model tier")
        self._checks = tuple(checks)
        self._constraints = tuple(constraints)

    async def act(self, ctx: ArtifactContext, worktree: Worktree) -> list[Event]:
        artifact = ctx.artifact
        toolbox = WorktreeToolbox(worktree, self._checks)
        routed = self._route(ctx, worktree)  # a Routed event on the first attempt, when routing is on
        start = self.starting_tier(ctx, routed)
        model, worker = self.tier_for(ctx, start)
        events: list[Event] = [routed] if routed is not None else []
        request = self.request(ctx, worktree)
        try:
            report = await worker.run(self.INSTRUCTIONS, request, ExecutorReport, toolbox)
        except WorkerError as error:
            return events + self._escalate(ctx, worktree, f"executor failed: {error}")
        if not worktree.has_changes():
            no_changes = Escalated(artifact=artifact.id, reason=f"executor made no changes: {report.summary}")
            return [*events, no_changes]

        sha = worktree.commit(f"{artifact.id}: {report.summary}")
        events.append(CommitPushed(artifact=artifact.id, commit=sha, model=model))
        checks = toolbox.check_report()
        if not checks.passed:
            events.append(Escalated(artifact=artifact.id, reason=f"checks still fail:\n{checks.summary()}"))
        elif not report.complete:
            events.append(
                Escalated(artifact=artifact.id, reason=f"executor reports incomplete: {report.summary}")
            )
        return events

    def tier_for(self, ctx: ArtifactContext, start: int = 0) -> tuple[str | None, Worker]:
        """From the starting tier, one tier up per rejection so far, staying on the strongest."""
        if self._tiers is None:
            assert self._single is not None
            return None, self._single
        rejections = sum(isinstance(e, Rejected) for e in ctx.history)
        index = min(start + rejections, len(self._tiers) - 1)
        tier = self._tiers[index]
        return tier.model, tier.worker

    def starting_tier(self, ctx: ArtifactContext, routed_now: Routed | None) -> int:
        """Where the tiers start: the route chosen on the first attempt, or the cheapest tier."""
        earlier = [e for e in ctx.history if isinstance(e, Routed)]
        routed = routed_now if routed_now is not None else (earlier[0] if earlier else None)
        if routed is None or self._tiers is None:
            return 0
        names = [t.model for t in self._tiers]
        return names.index(routed.model) if routed.model in names else 0

    def _route(self, ctx: ArtifactContext, worktree: Worktree) -> Routed | None:
        """Ask the router once per artifact, before its first attempt."""
        if self._router is None or self._tiers is None:
            return None
        if any(isinstance(e, Routed) for e in ctx.history):
            return None
        head = worktree.head()
        choice = self._router.choose(ctx, head)
        tier = self._tiers[choice.tier]
        model_id = self._router.model_id(choice.tier)
        return Routed(
            artifact=ctx.artifact.id,
            team=self.name,
            model=tier.model,
            model_id=model_id,
            difficulty=choice.difficulty,
            decision=choice.decision,
            acted=choice.acted,
        )

    @staticmethod
    def _escalate(ctx: ArtifactContext, worktree: Worktree, reason: str) -> list[Event]:
        """Escalate, first committing any work in progress so a human can see it."""
        escalated = Escalated(artifact=ctx.artifact.id, reason=reason)
        if not worktree.has_changes():
            return [escalated]
        sha = worktree.commit(f"{ctx.artifact.id}: work in progress (escalated)")
        return [CommitPushed(artifact=ctx.artifact.id, commit=sha), escalated]

    def request(self, ctx: ArtifactContext, worktree: Worktree) -> ExecutorRequest:
        return ExecutorRequest(
            north_star=ctx.north_star,
            instruction=Instruction(
                goal=ctx.artifact.task, must_fix=ctx.open_findings, constraints=self._constraints
            ),
            files=tuple(worktree.files()),
            diff_summary=ctx.diff_summary,
        )
