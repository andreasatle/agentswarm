"""The merge queue of one level: promotions into it are serialized (proposal §9).

Each request is merged onto the level's current tip in a worktree, the gate's hard checks rerun on the
merged result, and the merge is kept only if they pass. The level branch therefore always passes its
own checks. Merging (rather than rebasing the artifact branch) tests exactly the tree that lands and
keeps the artifact's own branch history intact.
"""

from collections import deque

from pydantic import BaseModel, ConfigDict

from agentswarm.domain import ArtifactContext, DecisionId, Event, Promoted, Rejected
from agentswarm.gates import Gate
from agentswarm.git import Repository, Worktree


class MergeRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    context: ArtifactContext
    commit: str  # the commit the gate judged, not whatever the branch points to now
    decision: DecisionId | None  # the gate's decision, if it has a judge


class MergeQueue:
    def __init__(self, repository: Repository, gate: Gate) -> None:
        self._repository = repository
        self._gate = gate
        self._pending: deque[MergeRequest] = deque()

    def __len__(self) -> int:
        return len(self._pending)

    def enqueue(self, request: MergeRequest) -> None:
        self._pending.append(request)

    def process(self) -> list[Event]:
        """Merge every pending request in order. Returns the events; never writes the board itself."""
        if not self._pending:
            return []
        worktree = self._repository.checkout_level(self._gate.to_level)
        events: list[Event] = []
        try:
            while self._pending:
                events.append(self._merge(worktree, self._pending.popleft()))
        finally:
            worktree.remove()
            # After a failure the rest are dropped: their artifacts still have unjudged commits,
            # so the promoter judges them again on its next step.
            self._pending.clear()
        return events

    def _merge(self, worktree: Worktree, request: MergeRequest) -> Event:
        ctx = request.context
        artifact = ctx.artifact
        before = worktree.head()
        if not worktree.merge(
            request.commit, f"Promote {artifact.id} to {self._gate.to_level}: {artifact.task}"
        ):
            return Rejected(
                artifact=artifact.id,
                level=self._gate.from_level,
                findings=(self._gate.conflict_finding(ctx),),
                gate_decision=request.decision,
            )
        findings = self._gate.run_checks(worktree.path, ctx)
        if findings:
            worktree.reset_hard(before)
            return Rejected(
                artifact=artifact.id,
                level=self._gate.from_level,
                findings=findings,
                gate_decision=request.decision,
            )
        return Promoted(
            artifact=artifact.id,
            from_level=self._gate.from_level,
            to_level=self._gate.to_level,
            gate_decision=request.decision,
        )
