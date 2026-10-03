"""The decomposition team: splits tasks too large for one branch (proposal §10.4, §20 question 7).

For every new artifact at the lowest level (fix artifacts excepted) it asks Jev whether the task fits one
branch. If it clearly doesn't, a model proposes child tasks, Jev checks that each child is clearly
smaller (the progress guard), and the artifact becomes an umbrella over its children. A join gate
promotes the umbrella once its children are through (`gating/join.py`).

In shadow mode the questions are logged but every task is treated as fitting: nothing is split.
"""

from pydantic import BaseModel, ConfigDict, Field

from agentswarm.board.artifacts import ArtifactFactory
from agentswarm.core.config import JevMode
from agentswarm.core.domain import (
    ArtifactContext,
    ArtifactCreated,
    Escalated,
    Event,
    Level,
    Sized,
    TeamName,
)
from agentswarm.judging.deciders import DeciderError
from agentswarm.judging.judgement import Judgement
from agentswarm.judging.policy import Verdict
from agentswarm.llm.workers import Worker, WorkerError
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


class Decomposer:
    NAME = TeamName("decomposer")
    INSTRUCTIONS = """\
You are the planner. The task in the data is too large for one change by one team. Split it into 2 to
6 child tasks that together accomplish it.

- Each child task must be self-contained: whoever does it sees only that text.
- Each child must be clearly smaller than the parent and doable in one branch without further design
  decisions.
- Children should touch different parts of the code where possible, so they don't conflict.
- Don't add work the parent task didn't ask for."""

    def __init__(
        self,
        watches: Level,
        worker: Worker,
        size: Judgement,
        split: Judgement,
        artifacts: ArtifactFactory,
        max_depth: int,
    ) -> None:
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

    def relevant(self, ctx: ArtifactContext) -> Relevance:
        unsized = not any(isinstance(e, Sized) for e in ctx.history)
        return Relevance.YES if unsized and ctx.artifact.fixes is None else Relevance.NO

    async def act(self, ctx: ArtifactContext, worktree: Worktree) -> list[Event]:
        artifact = ctx.artifact
        head = worktree.head()  # nothing is committed yet: the decision refers to the fork point
        try:
            size = self._size.ask(f"size:{self._watches}", ctx, head, head, "")
        except DeciderError:
            return [Sized(artifact=artifact.id, fits=True)]  # without an answer, let the executor try
        fits = Sized(artifact=artifact.id, fits=True, decision=size.id)
        if size.mode is JevMode.SHADOW or size.ruling.verdict is not Verdict.REJECT:
            return [fits]  # fits, or unsure: the gate will catch a change that is too big
        if ctx.depth >= self._max_depth:
            return [Escalated(artifact=artifact.id, reason=f"too large, but already split {ctx.depth} times")]

        request = SplitRequest(north_star=ctx.north_star, task=artifact.task, depth=ctx.depth)
        try:
            split = await self._worker.run(self.INSTRUCTIONS, request, Split)
        except WorkerError as error:
            return [Escalated(artifact=artifact.id, reason=f"decomposition failed: {error}")]
        proposal = [child.task for child in split.children]
        try:
            check = self._split.ask(f"split:{self._watches}", ctx, head, head, "", proposal)
        except DeciderError as error:
            return [Escalated(artifact=artifact.id, reason=f"split could not be checked: {error}")]
        if check.ruling.verdict is not Verdict.PROMOTE:
            return [
                Escalated(artifact=artifact.id, reason="proposed split is not clearly smaller than the task")
            ]

        children = [self._artifacts.child(artifact, i, task, self.NAME) for i, task in enumerate(proposal, 1)]
        return [
            *(ArtifactCreated(artifact=child) for child in children),
            Sized(artifact=artifact.id, fits=False, children=tuple(c.id for c in children), decision=size.id),
        ]
