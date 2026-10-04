"""Teams and the loop every team runs (proposal §10)."""

import logging
from datetime import timedelta
from enum import StrEnum
from typing import Protocol

from agentswarm.board.blackboard import Blackboard
from agentswarm.board.claims import ClaimRegistry
from agentswarm.board.context import ContextBuilder
from agentswarm.control.budget import Budget
from agentswarm.control.scheduler import Scheduler
from agentswarm.core.clock import Clock
from agentswarm.core.domain import (
    Artifact,
    ArtifactContext,
    ArtifactId,
    Event,
    Level,
    ProjectId,
    TeamName,
)
from agentswarm.workspace.git import Repository, Worktree


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

    @property
    def pausable(self) -> bool:
        """Whether a project pause stops this team (teams that start new work); analyzers carry on."""
        ...

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

    _log = logging.getLogger(__name__)

    def __init__(
        self,
        team: Team,
        board: Blackboard,
        claims: ClaimRegistry,
        repository: Repository,
        contexts: ContextBuilder,
        clock: Clock,
        lease: timedelta = timedelta(minutes=10),
        budget: Budget | None = None,  # over budget, the team waits like a paused one
        scheduler: Scheduler | None = None,  # shared across projects: a slot is held while the team acts
        project: ProjectId = ProjectId("project"),
    ) -> None:
        self._budget = budget
        self._scheduler = scheduler
        self._project = project
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
        if self._team.pausable and self._board.current().paused:
            return 0  # nothing is marked handled, so the work is picked up after Resumed
        acted = 0
        for artifact in self._candidates():
            if self._budget is not None and self._budget.exhausted(self._board.current()):
                break  # checked before every action, not once per step; the rest waits for tomorrow
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
        if self._board.current().version(artifact.id) != version:
            self._claims.release(claim)  # it changed between reading it and claiming it; look again
            return False
        try:
            worktree = (
                self._repository.checkout_artifact(artifact)
                if ctx.merge_commit is None
                else self._repository.checkout_merged(ctx.merge_commit, artifact.id)
            )
            try:
                if self._scheduler is None:
                    events = await self._team.act(ctx, worktree)
                else:
                    async with self._scheduler.model_call(self._project):
                        events = await self._team.act(ctx, worktree)
            finally:
                worktree.remove()
            published = self._claims.publish(claim, version, events)
        finally:
            self._claims.release(claim)
        if published is None:
            self._log.warning(
                "%s: discarded %s's result, superseded while it worked", artifact.id, self._team.name
            )
            return False
        views = published
        self._handled.add((artifact.id, views.version(artifact.id)))
        return True
