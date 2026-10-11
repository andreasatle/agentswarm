"""The decomposition team: splits tasks too large for one branch (proposal §10.4, §20 question 7).

For every new artifact at the lowest level (fix artifacts excepted) it asks Jev whether the task fits one
branch. If it clearly doesn't, a model proposes child tasks, Jev checks that each child is clearly
smaller (the progress guard), and the artifact becomes an umbrella over its children. A join gate
promotes the umbrella once its children are through (`gating/join.py`).

In shadow mode the questions are logged but every task is treated as fitting: nothing is split.
"""

from pydantic import BaseModel, ConfigDict, Field

from agentswarm.board.artifacts import ArtifactFactory
from agentswarm.control.escalations import Escalations
from agentswarm.core.config import JevMode
from agentswarm.core.domain import (
    ArtifactContext,
    ArtifactCreated,
    Escalated,
    Event,
    Level,
    Sized,
    TeamName,
    Unbuildable,
)
from agentswarm.judging.deciders import DeciderError
from agentswarm.judging.judgement import Judgement
from agentswarm.judging.policy import Verdict
from agentswarm.llm.workers import Worker, WorkerError
from agentswarm.teams.routing import RoutedTiers
from agentswarm.teams.runner import Relevance
from agentswarm.workspace.git import Worktree


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ChildTask(Frozen):
    task: str  # self-contained: the executor of the child sees only this text


class Split(Frozen):
    children: tuple[ChildTask, ...] = Field(min_length=2, max_length=6)


class SplitRequest(Frozen):
    north_star: str
    task: str
    depth: int  # how many splits above this task already
    failed: str | None = None  # why every builder model failed it, when that is why it is being split


class Decomposer:
    NAME = TeamName("decomposer")
    INSTRUCTIONS = """\
You are the planner. The task in the data is too large for one change by one team. Split it into 2 to
6 child tasks that together accomplish it.

- Each child task must be self-contained: whoever does it sees only that text.
- Each child must be clearly smaller than the parent and doable in one branch without further design
  decisions.
- The children are done one after another, in the order you give, each on top of what the earlier ones
  merged: order them so each step builds on the last and leaves everything working.
- Don't add work the parent task didn't ask for.
- If `failed` is given, every builder model tried the task and failed for that reason: split so that the
  part that failed becomes small enough to do on its own."""

    def __init__(
        self,
        watches: Level,
        worker: Worker,
        size: Judgement,
        split: Judgement,
        artifacts: ArtifactFactory,
        max_depth: int,
        build_at_max_depth: bool = False,  # at the limit, let the executor try instead of asking a person
        routing: RoutedTiers | None = None,  # instead of `worker`: tiers, and a router to start on one
    ) -> None:
        self._routing = routing
        self._build_at_max_depth = build_at_max_depth
        self._watches = watches
        self._worker = worker
        self._size = size
        self._split = split
        self._artifacts = artifacts
        self._max_depth = max_depth

    @property
    def name(self) -> TeamName:
        return self.NAME

    @property
    def watches(self) -> Level:
        return self._watches

    @property
    def pausable(self) -> bool:
        return True

    def relevant(self, ctx: ArtifactContext) -> Relevance:
        unsized = not any(isinstance(e, Sized) for e in ctx.history)
        return Relevance.YES if (unsized or self.failed(ctx)) and ctx.artifact.fixes is None else Relevance.NO

    @staticmethod
    def failed(ctx: ArtifactContext) -> bool:
        """Every builder model failed it since it was last sized: split it after all (ADaPT); unless a person
        chose to retry it instead."""
        return Escalations.awaiting_split(ctx.history)

    async def act(self, ctx: ArtifactContext, worktree: Worktree) -> list[Event]:
        artifact = ctx.artifact
        head = worktree.head()  # nothing is committed yet: the decision refers to the fork point
        decision = None
        if self.failed(ctx):  # the builders' failure is the evidence: no size question (ADaPT)
            if ctx.depth >= self._max_depth:
                reason = f"every builder model failed it, and it is already split {ctx.depth} times"
                return [Escalated(artifact=artifact.id, reason=reason)]
        else:
            try:
                size = self._size.ask(f"size:{self._watches}", ctx, head, head, "")
            except DeciderError:
                return [Sized(artifact=artifact.id, fits=True)]  # without an answer, let the executor try
            decision = size.id
            fits = Sized(artifact=artifact.id, fits=True, decision=size.id)
            if size.mode is JevMode.SHADOW or size.ruling.verdict is not Verdict.REJECT:
                return [fits]  # fits, or unsure: the gate will catch a change that is too big
            if ctx.depth >= self._max_depth and self._build_at_max_depth:
                return [fits]  # unattended: the gate's checks catch a change that is too big
            if ctx.depth >= self._max_depth:
                reason = f"too large, but already split {ctx.depth} times"
                return [Escalated(artifact=artifact.id, reason=reason)]

        unbuildable = [e for e in ctx.history if isinstance(e, Unbuildable)]
        failed = unbuildable[-1].reason if self.failed(ctx) else None
        request = SplitRequest(north_star=ctx.north_star, task=artifact.task, depth=ctx.depth, failed=failed)
        worker = self._worker
        routed: list[Event] = []  # routed only when the model is needed: a task that fits makes no call
        if self._routing is not None:
            start, routed = self._routing.start(ctx, head)
            worker = self._routing.tiers[start].worker
        try:
            split = await worker.run(self.INSTRUCTIONS, request, Split)
        except WorkerError as error:
            return [*routed, Escalated(artifact=artifact.id, reason=f"decomposition failed: {error}")]
        proposal = [child.task for child in split.children]
        try:
            check = self._split.ask(f"split:{self._watches}", ctx, head, head, "", proposal)
        except DeciderError as error:
            return [*routed, Escalated(artifact=artifact.id, reason=f"split could not be checked: {error}")]
        if (
            check.mode is JevMode.ACTIVE and check.ruling.verdict is not Verdict.PROMOTE
        ):  # shadow never blocks
            return [
                *routed,
                Escalated(artifact=artifact.id, reason="proposed split is not clearly smaller than the task"),
            ]

        children = [self._artifacts.child(artifact, i, task, self.NAME) for i, task in enumerate(proposal, 1)]
        return [
            *routed,
            *(ArtifactCreated(artifact=child) for child in children),
            Sized(
                artifact=artifact.id, fits=False, children=tuple(c.id for c in children), decision=decision
            ),
        ]
