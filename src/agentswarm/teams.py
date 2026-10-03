"""Teams and the loop every team runs (proposal §10)."""

from datetime import timedelta
from enum import StrEnum
from typing import Protocol

from agentswarm.blackboard import Blackboard
from agentswarm.claims import ClaimRegistry
from agentswarm.clock import Clock
from agentswarm.context import ContextBuilder
from agentswarm.domain import (
    Artifact,
    ArtifactContext,
    ArtifactId,
    Event,
    Level,
    TeamName,
)
from agentswarm.git import Repository, Worktree


class Relevance(StrEnum):
    YES = "yes"
    NO = "no"
    UNSURE = "unsure"  # threshold not met either way: skip now, the supervisor may escalate


class Team(Protocol):
    """One kind of work at one level. Teams never call each other; they only read and append to the board."""

    @property
    def name(self) -> TeamName: ...

    @property
    def watches(self) -> Level: ...

    def relevant(self, ctx: ArtifactContext) -> Relevance:
        """Cheap: is there something for this team here?"""
        ...

    async def act(self, ctx: ArtifactContext, worktree: Worktree) -> list[Event]:
        """Expensive: do the work in the worktree. Returns events; never writes the board itself."""
        ...


class TeamRunner:
    """Runs one team as a level-triggered loop (proposal §10.2).

    Each step looks at the current state of every artifact at the team's level. The runner remembers the
    artifact versions it has already handled, including the version its own events produced, so a team
    reacts once to each new state and never to its own output.
    """

    def __init__(
        self,
        team: Team,
        board: Blackboard,
        claims: ClaimRegistry,
        repository: Repository,
        contexts: ContextBuilder,
        clock: Clock,
        lease: timedelta = timedelta(minutes=10),
    ) -> None:
        self._team = team
        self._board = board
        self._claims = claims
        self._repository = repository
        self._contexts = contexts
        self._clock = clock
        self._lease = lease
        self._handled: set[tuple[ArtifactId, int]] = set()

    @property
    def team(self) -> Team:
        return self._team

    async def step(self) -> int:
        """Act on every artifact that needs this team. Returns how many it acted on."""
        acted = 0
        for artifact in self._candidates():
            if await self._handle(artifact):
                acted += 1
        return acted

    def _candidates(self) -> list[Artifact]:
        views = self._board.current()
        now = self._clock.now()
        return [
            a
            for a in views.artifacts_at(self._team.watches)
            if (a.id, views.version(a.id)) not in self._handled and views.claim_on(a.id, now) is None
        ]

    async def _handle(self, artifact: Artifact) -> bool:
        version = self._board.current().version(artifact.id)
        ctx = self._contexts.build(artifact.id)
        if self._team.relevant(ctx) is not Relevance.YES:
            self._handled.add((artifact.id, version))
            return False
        claim = self._claims.claim(artifact.id, self._team.name, self._lease)
        if claim is None:
            return False  # another team got it first; look again next step
        try:
            worktree = self._repository.checkout_artifact(artifact)
            try:
                events = await self._team.act(ctx, worktree)
            finally:
                worktree.remove()
            views = self._board.append(events)
        finally:
            self._claims.release(claim)
        self._handled.add((artifact.id, views.version(artifact.id)))
        return True
