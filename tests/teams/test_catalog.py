import pytest

from agentswarm.board.artifacts import ArtifactFactory
from agentswarm.board.blackboard import Blackboard
from agentswarm.board.eventlog import InMemoryEventLog
from agentswarm.core.clock import FakeClock
from agentswarm.core.config import CheckConfig, JevMode, LevelLadder, TeamConfig
from agentswarm.gating.checks import CheckSet
from agentswarm.judging.deciders import FakeDecider
from agentswarm.judging.decisions import InMemoryDecisions
from agentswarm.judging.judgement import Judgements
from agentswarm.judging.judges import JudgeCatalog
from agentswarm.llm.workers import FakeWorker, FakeWorkers
from agentswarm.teams.analyzer import IntegrationTester, RedTeam
from agentswarm.teams.catalog import TeamCatalog, TeamResources
from agentswarm.teams.executor import ExecutorTeam, FakeExecutor
from agentswarm.workspace.sandbox import LocalSandbox
from tests.support.samples import Samples


class Resources:
    @staticmethod
    def make() -> TeamResources:
        checks = CheckSet({"tests": CheckConfig(command=("true",))}, LocalSandbox())
        judgements = Judgements(
            JudgeCatalog.standard(), FakeDecider(), InMemoryDecisions(), JevMode.SHADOW, FakeClock()
        )
        board = Blackboard(InMemoryEventLog(Samples.PROJECT, FakeClock()))
        ladder = LevelLadder(levels=(Samples.RAW, Samples.EXPERIMENTAL))
        return TeamResources(
            FakeWorkers({"fake": FakeWorker([])}),
            checks,
            judgements,
            ArtifactFactory(board, ladder),
            ladder,
            (),
        )


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

    def test_builds_analyzers_with_their_relevance_filter(self) -> None:
        team = TeamConfig(
            use="red_team",
            watches=Samples.EXPERIMENTAL,
            model="fake",
            checks=("tests",),
            relevance="AdversarialRelevance",
        )
        red_team = TeamCatalog.standard().build(team, Resources.make())
        assert isinstance(red_team, RedTeam)
        assert red_team.name == "red_team"
        tester = TeamCatalog.standard().build(
            TeamConfig(
                use="integration_tester", watches=Samples.EXPERIMENTAL, model="fake", checks=("tests",)
            ),
            Resources.make(),
        )
        assert isinstance(tester, IntegrationTester)

    def test_analyzers_need_a_check_to_run_tests_with(self) -> None:
        with pytest.raises(ValueError, match="needs a check"):
            TeamCatalog.standard().build(
                TeamConfig(use="red_team", watches=Samples.EXPERIMENTAL, model="fake"), Resources.make()
            )

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
