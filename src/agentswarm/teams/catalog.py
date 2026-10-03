"""The team catalog: team definitions by name, instantiated per project from its configuration (§10.3)."""

from collections.abc import Callable
from typing import Self

from agentswarm.core.config import TeamConfig
from agentswarm.gating.checks import CheckSet, HardCheck
from agentswarm.llm.workers import Worker, WorkerProvider
from agentswarm.teams.executor import ExecutorTeam, FakeExecutor
from agentswarm.teams.runner import Team


class TeamResources:
    """What a team definition may draw on when it is instantiated: models and checks."""

    def __init__(self, workers: WorkerProvider, checks: CheckSet) -> None:
        self._workers = workers
        self._checks = checks

    def worker_for(self, team: TeamConfig) -> Worker:
        if team.model is None:
            raise ValueError(f"team {team.use!r} needs a model; set `model:` in its configuration")
        return self._workers.worker(team.model, team.max_model_calls)

    def checks_for(self, team: TeamConfig) -> list[HardCheck]:
        return self._checks.build(team.checks)


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
