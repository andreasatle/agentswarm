"""Leases that give one team at a time an artifact to work on (proposal §8.2)."""

from datetime import timedelta

from agentswarm.board.blackboard import Blackboard
from agentswarm.board.eventlog import ConcurrencyConflict
from agentswarm.core.clock import Clock
from agentswarm.core.domain import ArtifactId, Claim, Claimed, Released, TeamName


class ClaimRegistry:
    """Claims and releases artifacts through the blackboard.

    A claim is appended only if the log hasn't moved since the registry checked that the artifact was
    free, so two teams racing for the same artifact can't both win. Expired leases need no event: the
    views ignore them, which is what lets a crashed team's work be picked up again.
    """

    def __init__(self, board: Blackboard, clock: Clock, max_attempts: int = 5) -> None:
        self._board = board
        self._clock = clock
        self._max_attempts = max_attempts

    def claim(self, artifact: ArtifactId, team: TeamName, lease: timedelta) -> Claim | None:
        """Claim the artifact for `team`, or return None if another team holds it."""
        for _ in range(self._max_attempts):
            views = self._board.current()
            now = self._clock.now()
            if views.claim_on(artifact, now) is not None:
                return None
            claim = Claim(artifact=artifact, team=team, expires_at=now + lease)
            try:
                self._board.append([Claimed(claim=claim)], expected_head=views.seq)
            except ConcurrencyConflict:
                continue  # the log moved; look again
            return claim
        return None

    def release(self, claim: Claim) -> None:
        self._board.append([Released(artifact=claim.artifact, team=claim.team)])
