"""The promoter: the deterministic loop that runs one gate over the artifacts at its level."""

from datetime import timedelta

from agentswarm.board.blackboard import Blackboard
from agentswarm.board.claims import ClaimRegistry
from agentswarm.board.context import ContextBuilder
from agentswarm.core.clock import Clock
from agentswarm.core.domain import Artifact, Claim, Escalated, Event, Rejected
from agentswarm.gating.gate import Gate, GateOutcome, Verdict
from agentswarm.gating.merge_queue import MergeQueue, MergeRequest
from agentswarm.workspace.git import Repository


class Promoter:
    """Level-triggered: each step looks at every artifact at the gate's level with an unjudged commit.

    The promoter claims an artifact while judging it and keeps the claim until the merge queue has
    processed it, so no team can push a new commit between the judgement and the merge.
    """

    def __init__(
        self,
        gate: Gate,
        board: Blackboard,
        claims: ClaimRegistry,
        repository: Repository,
        contexts: ContextBuilder,
        clock: Clock,
        lease: timedelta = timedelta(minutes=10),
    ) -> None:
        self._gate = gate
        self._board = board
        self._claims = claims
        self._repository = repository
        self._contexts = contexts
        self._clock = clock
        self._lease = lease
        self._queue = MergeQueue(repository, gate)

    @property
    def gate(self) -> Gate:
        return self._gate

    def step(self) -> int:
        """Judge every waiting artifact once and process the merge queue. Returns how many were judged."""
        held: list[Claim] = []
        judged = 0
        try:
            for artifact in self._waiting():
                claim = self._claims.claim(artifact.id, self._gate.team, self._lease)
                if claim is None:
                    continue
                judged += 1
                if self._judge(artifact):
                    held.append(claim)
                else:
                    self._claims.release(claim)
            self._board.append(self._queue.process())
        finally:
            for claim in held:
                self._claims.release(claim)
        return judged

    def _waiting(self) -> list[Artifact]:
        views = self._board.current()
        now = self._clock.now()
        return [
            a
            for a in views.artifacts_at(self._gate.from_level)
            if views.unjudged_commit(a.id) is not None and views.claim_on(a.id, now) is None
        ]

    def _judge(self, artifact: Artifact) -> bool:
        """Evaluate the artifact's unjudged commit. Returns True if it was queued for merging."""
        commit = self._board.current().unjudged_commit(artifact.id)
        if commit is None:  # judged by someone else since we listed it
            return False
        ctx = self._contexts.build(artifact.id)
        # Judge exactly the announced commit, detached so the artifact's branch is never moved.
        worktree = self._repository.checkout_detached(commit, f"_gate-{artifact.id}")
        try:
            outcome = self._gate.evaluate(ctx, worktree)
        finally:
            worktree.remove()
        if outcome.verdict is Verdict.PROMOTE:
            self._queue.enqueue(MergeRequest(context=ctx, commit=commit, decision=outcome.decision))
            return True
        self._board.append([self._event(artifact, outcome)])
        return False

    def _event(self, artifact: Artifact, outcome: GateOutcome) -> Event:
        if outcome.verdict is Verdict.REJECT:
            return Rejected(
                artifact=artifact.id,
                level=self._gate.from_level,
                findings=outcome.findings,
                gate_decision=outcome.decision,
            )
        return Escalated(artifact=artifact.id, reason=outcome.reason or f"{self._gate.name}: escalated")
