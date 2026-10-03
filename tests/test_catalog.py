import pytest

from agentswarm.catalog import TeamCatalog, TeamResources
from agentswarm.config import CheckConfig, TeamConfig
from agentswarm.executor import ExecutorTeam, FakeExecutor
from agentswarm.gates import CheckSet
from agentswarm.sandbox import LocalSandbox
from agentswarm.workers import FakeWorker, FakeWorkers
from tests.samples import Samples


class Resources:
    @staticmethod
    def make() -> TeamResources:
        checks = CheckSet({"tests": CheckConfig(command=("true",))}, LocalSandbox())
        return TeamResources(FakeWorkers({"fake": FakeWorker([])}), checks)


class TestTeamCatalog:
    def test_builds_the_standard_teams(self) -> None:
        catalog = TeamCatalog.standard()
        fake = catalog.build(TeamConfig(use="fake_executor", watches=Samples.RAW), Resources.make())
        real = catalog.build(
            TeamConfig(use="executor", watches=Samples.RAW, model="fake", checks=("tests",)), Resources.make()
        )
        assert isinstance(fake, FakeExecutor)
        assert isinstance(real, ExecutorTeam)
        assert real.watches == Samples.RAW

    def test_executor_without_a_model_is_an_error(self) -> None:
        with pytest.raises(ValueError, match="needs a model"):
            TeamCatalog.standard().build(TeamConfig(use="executor", watches=Samples.RAW), Resources.make())

    def test_unknown_team_is_an_error(self) -> None:
        with pytest.raises(ValueError, match="unknown team"):
            TeamCatalog.standard().build(TeamConfig(use="nobody", watches=Samples.RAW), Resources.make())

    def test_names_are_registered_once(self) -> None:
        catalog = TeamCatalog.standard()
        with pytest.raises(ValueError, match="already registered"):
            catalog.register("fake_executor", lambda team, resources: FakeExecutor(team.watches))
