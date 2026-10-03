"""Teams and the loop every team runs (proposal §10)."""

from collections.abc import Callable
from datetime import timedelta
from enum import StrEnum
from typing import Protocol, Self

from agentswarm.blackboard import Blackboard
from agentswarm.claims import ClaimRegistry
from agentswarm.clock import Clock
from agentswarm.config import TeamConfig
from agentswarm.context import ContextBuilder
from agentswarm.domain import (
    Artifact,
    ArtifactContext,
    ArtifactId,
    CommitPushed,
    Escalated,
    Event,
    Level,
    Promoted,
    Rejected,
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


class FakeExecutor:
    """An executor without a model: writes the task (and the findings it was asked to fix) to a file.

    It acts when the artifact has no commit yet, or when a gate has rejected its latest commit.
    """

    def __init__(self, watches: Level, name: TeamName = TeamName("executor")) -> None:
        self._watches = watches
        self._name = name

    @property
    def name(self) -> TeamName:
        return self._name

    @property
    def watches(self) -> Level:
        return self._watches

    def relevant(self, ctx: ArtifactContext) -> Relevance:
        milestones = [e for e in ctx.history if isinstance(e, CommitPushed | Rejected | Promoted | Escalated)]
        if not milestones or isinstance(milestones[-1], Rejected):
            return Relevance.YES
        return Relevance.NO

    async def act(self, ctx: ArtifactContext, worktree: Worktree) -> list[Event]:
        artifact = ctx.artifact
        lines = [f"# {artifact.task}", ""] + [f"- fixed: {f.detail}" for f in ctx.open_findings]
        worktree.write(f"{artifact.id}.md", "\n".join(lines) + "\n")
        sha = worktree.commit(f"{artifact.id}: {artifact.task}")
        return [CommitPushed(artifact=artifact.id, commit=sha)]


type TeamFactory = Callable[[TeamConfig], Team]


class TeamCatalog:
    """Team definitions by name, instantiated per project from its configuration (proposal §10.3)."""

    def __init__(self) -> None:
        self._factories: dict[str, TeamFactory] = {}

    @classmethod
    def with_fakes(cls) -> Self:
        catalog = cls()
        catalog.register("fake_executor", lambda config: FakeExecutor(config.watches))
        return catalog

    def register(self, name: str, factory: TeamFactory) -> None:
        if name in self._factories:
            raise ValueError(f"team {name!r} is already registered")
        self._factories[name] = factory

    def build(self, config: TeamConfig) -> Team:
        factory = self._factories.get(config.use)
        if factory is None:
            raise ValueError(f"unknown team {config.use!r}; known: {sorted(self._factories)}")
        return factory(config)
