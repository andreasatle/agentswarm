"""The team catalog: team definitions by name, instantiated per project from its configuration (§10.3)."""

from collections.abc import Callable
from typing import Self

from agentswarm.core.config import TeamConfig
from agentswarm.core.domain import TeamName
from agentswarm.gating.checks import CheckSet, CommandCheck, HardCheck
from agentswarm.judging.judgement import Judgements
from agentswarm.llm.workers import Worker, WorkerProvider
from agentswarm.teams.analyzer import IntegrationTester, RedTeam
from agentswarm.teams.executor import ExecutorTeam, FakeExecutor
from agentswarm.teams.relevance import RelevanceFilter
from agentswarm.teams.runner import Team


class TeamResources:
    """What a team definition may draw on when it is instantiated: models, checks and judgements."""

    def __init__(self, workers: WorkerProvider, checks: CheckSet, judgements: Judgements) -> None:
        self._workers = workers
        self._checks = checks
        self._judgements = judgements

    def worker_for(self, team: TeamConfig) -> Worker:
        if team.model is None:
            raise ValueError(f"team {team.use!r} needs a model; set `model:` in its configuration")
        return self._workers.worker(team.model, team.max_model_calls)

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
                resources.worker_for(team), resources.checks_for(team), team.watches, team.constraints
            ),
        )
        catalog.register("fake_executor", lambda team, resources: FakeExecutor(team.watches))
        for use, analyzer in (("integration_tester", IntegrationTester), ("red_team", RedTeam)):
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

    def register(self, name: str, factory: TeamFactory) -> None:
        if name in self._factories:
            raise ValueError(f"team {name!r} is already registered")
        self._factories[name] = factory

    def build(self, team: TeamConfig, resources: TeamResources) -> Team:
        factory = self._factories.get(team.use)
        if factory is None:
            raise ValueError(f"unknown team {team.use!r}; known: {sorted(self._factories)}")
        return factory(team, resources)
