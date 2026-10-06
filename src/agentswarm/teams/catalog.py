"""The team catalog: team definitions by name, instantiated per project from its configuration (§10.3)."""

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import ClassVar, Self

from agentswarm.board.artifacts import ArtifactFactory
from agentswarm.core.config import LevelLadder, TeamConfig, Threshold
from agentswarm.core.domain import TeamName
from agentswarm.gating.checks import CheckSet, CommandCheck, HardCheck
from agentswarm.judging.judgement import Judgement, Judgements
from agentswarm.judging.policy import Policy
from agentswarm.llm.workers import Meter, Worker, WorkerProvider
from agentswarm.teams.analyzer import (
    ContractTester,
    EdgeValueTester,
    ErrorTester,
    IntegrationTester,
    ProbingAnalyzer,
    RedTeam,
    TextTester,
)
from agentswarm.teams.decomposer import Decomposer
from agentswarm.teams.documents import AudienceReviewer, ConsistencyChecker, FactChecker, Outliner, Writer
from agentswarm.teams.executor import ExecutorTeam, FakeExecutor, Tier
from agentswarm.teams.relevance import RelevanceFilter
from agentswarm.teams.routing import CallKind, Router, RoutingLedger
from agentswarm.teams.runner import Team


class TeamResources:
    """What a team definition may draw on when it is instantiated: models, checks and judgements."""

    def __init__(
        self,
        workers: WorkerProvider,
        checks: CheckSet,
        judgements: Judgements,
        artifacts: ArtifactFactory,
        ladder: LevelLadder,
        team_uses: Sequence[str],
        meters: Callable[[str, str], Meter] | None = None,  # (team, model) -> meter, for spend
        model_ids: Mapping[str, str] | None = None,  # model name -> "<provider>:<model>", for routing
        project: str = "",  # for the routing ledger's record of where an outcome came from
    ) -> None:
        self._model_ids = dict(model_ids or {})
        self._meters = meters
        self._workers = workers
        self._checks = checks
        self._judgements = judgements
        self.artifacts = artifacts
        self.ladder = ladder
        self._team_uses = frozenset(team_uses)
        self._ledgers: dict[Path, RoutingLedger] = {}
        self.project = project

    def has_team(self, use: str) -> bool:
        return use in self._team_uses

    PLANNERS: ClassVar[frozenset[str]] = frozenset({"decomposer", "outliner"})

    def plans(self) -> bool:
        """Whether the project sizes tasks before work (a decomposer or outliner), so executors wait."""
        return bool(self._team_uses & self.PLANNERS)

    def judgement(self, judge: str, thresholds: Mapping[str, Threshold] | None = None) -> Judgement:
        return self._judgements.build(judge, thresholds)

    def tiers_for(self, team: TeamConfig) -> list[Tier]:
        """The team's model tiers, cheapest first; a single `model` is one tier."""
        models = team.model_tiers or ((team.model,) if team.model is not None else ())
        if not models:
            raise ValueError(f"team {team.use!r} needs a model; set `model:` or `model_tiers:`")
        return [
            Tier(model, self._workers.worker(model, team.max_model_calls, self._meter(team, model)))
            for model in models
        ]

    def router_for(self, team: TeamConfig) -> Router | None:
        """The team's router, if its configuration asks for one (Phase 8)."""
        routing = team.routing
        if routing is None:
            return None
        names = team.model_tiers or ((team.model,) if team.model is not None else ())
        model_ids = [self._model_ids.get(name, name) for name in names]
        if routing.ledger is not None and routing.ledger not in self._ledgers:
            self._ledgers[routing.ledger] = RoutingLedger(routing.ledger)
        ledger = self._ledgers.get(routing.ledger) if routing.ledger is not None else None
        judgement = self._judgements.build("CallProfile")
        return Router(
            judgement,
            f"route:{team.use}",
            CallKind.PRODUCE,
            model_ids,
            ledger,
            routing.target,
            self._judgements.clock,
            source=f"project:{self.project}",
        )

    def worker_for(self, team: TeamConfig) -> Worker:
        if team.model is None:
            raise ValueError(f"team {team.use!r} needs a model; set `model:` in its configuration")
        return self._workers.worker(team.model, team.max_model_calls, self._meter(team, team.model))

    def _meter(self, team: TeamConfig, model: str) -> Meter | None:
        return self._meters(team.use, model) if self._meters is not None else None

    def checks_for(self, team: TeamConfig) -> list[HardCheck]:
        return self._checks.build(team.checks)

    def test_runner_for(self, team: TeamConfig) -> CommandCheck:
        """The team's first check, used to run individual test files (analyzers)."""
        if not team.checks:
            raise ValueError(f"team {team.use!r} needs a check to run tests with; set `checks:`")
        return self._checks.command(team.checks[0])

    def relevance_for(self, team: TeamConfig) -> RelevanceFilter | None:
        if team.relevance is None:
            return None
        judgement = self._judgements.build(team.relevance)
        return RelevanceFilter(
            TeamName(team.use), judgement, self._judgements.decisions, self._judgements.clock
        )


type TeamFactory = Callable[[TeamConfig, TeamResources], Team]


class TeamCatalog:
    def __init__(self) -> None:
        self._factories: dict[str, TeamFactory] = {}

    @classmethod
    def standard(cls) -> Self:
        """The built-in teams. `fake_executor` needs no model, for tests and dry runs."""
        catalog = cls()
        catalog.register(
            "executor",
            lambda team, resources: ExecutorTeam(
                resources.tiers_for(team),
                resources.checks_for(team),
                team.watches,
                team.constraints,
                requires_sizing=resources.plans(),
                router=resources.router_for(team),
                attempts=team.attempts,
            ),
        )
        catalog.register(
            "writer",
            lambda team, resources: Writer(
                resources.tiers_for(team),
                resources.checks_for(team),
                team.watches,
                team.constraints,
                name=TeamName(team.use),
                requires_sizing=resources.plans(),
            ),
        )
        catalog.register(
            "fake_executor",
            lambda team, resources: FakeExecutor(team.watches, requires_sizing=resources.plans()),
        )
        catalog.register("decomposer", lambda team, resources: cls._planner(Decomposer, team, resources))
        catalog.register("outliner", lambda team, resources: cls._planner(Outliner, team, resources))
        for use, reviewer in (
            ("consistency_checker", ConsistencyChecker),
            ("fact_checker", FactChecker),
            ("audience_reviewer", AudienceReviewer),
        ):
            catalog.register(
                use,
                lambda team, resources, reviewer=reviewer: reviewer(
                    TeamName(team.use),
                    team.watches,
                    resources.worker_for(team),
                    resources.relevance_for(team),
                ),
            )
        probing: tuple[tuple[str, type[ProbingAnalyzer]], ...] = (
            ("integration_tester", IntegrationTester),
            ("red_team", RedTeam),
            ("edge_value_tester", EdgeValueTester),  # the narrow red teams (Phase 8)
            ("text_tester", TextTester),
            ("error_tester", ErrorTester),
            ("contract_tester", ContractTester),
        )
        for use, analyzer in probing:
            catalog.register(
                use,
                lambda team, resources, analyzer=analyzer: analyzer(
                    TeamName(team.use),
                    team.watches,
                    resources.worker_for(team),
                    resources.test_runner_for(team),
                    resources.relevance_for(team),
                ),
            )
        return catalog

    @staticmethod
    def size_thresholds(team: TeamConfig) -> dict[str, Threshold] | None:
        """With `split_below`, a planner splits when p(fits) is at most that, not only when clearly low."""
        if team.split_below is None:
            return None
        pass_at = max(Policy.DEFAULT.pass_at, team.split_below)
        return {"fits": Threshold(pass_at=pass_at, fail_at=team.split_below)}

    @classmethod
    def _planner(cls, kind: type[Decomposer], team: TeamConfig, resources: TeamResources) -> Team:
        if team.watches != resources.ladder.lowest:
            raise ValueError(f"the {team.use} must watch the lowest level, {resources.ladder.lowest!r}")
        return kind(
            team.watches,
            resources.worker_for(team),
            resources.judgement("SizeCheck", cls.size_thresholds(team)),
            resources.judgement("SplitCheck"),
            resources.artifacts,
            team.max_depth,
        )

    def knows(self, name: str) -> bool:
        return name in self._factories

    def names(self) -> list[str]:
        return sorted(self._factories)

    def with_team(self, name: str, factory: TeamFactory) -> "TeamCatalog":
        """A copy of this catalog in which `name` is built by `factory`, added or replaced."""
        catalog = TeamCatalog()
        catalog._factories = dict(self._factories)
        catalog._factories[name] = factory
        return catalog

    def register(self, name: str, factory: TeamFactory) -> None:
        if name in self._factories:
            raise ValueError(f"team {name!r} is already registered")
        self._factories[name] = factory

    def build(self, team: TeamConfig, resources: TeamResources) -> Team:
        factory = self._factories.get(team.use)
        if factory is None:
            raise ValueError(f"unknown team {team.use!r}; known: {sorted(self._factories)}")
        return factory(team, resources)
