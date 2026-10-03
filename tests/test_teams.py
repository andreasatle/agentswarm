from datetime import timedelta
from pathlib import Path

import pytest

from agentswarm.config import TeamConfig
from agentswarm.domain import ArtifactContext, CommitPushed, DecisionId, Event, Level, Rejected, TeamName
from agentswarm.git import Worktree
from agentswarm.teams import FakeExecutor, Relevance, TeamCatalog
from tests.harness import Harness
from tests.samples import Samples


class RecordingTeam:
    """A team whose relevance is fixed and which records what it acted on; optionally fails while acting."""

    def __init__(self, relevance: Relevance = Relevance.YES, fail: bool = False) -> None:
        self._relevance = relevance
        self._fail = fail
        self.acted: list[ArtifactContext] = []

    @property
    def name(self) -> TeamName:
        return Samples.TESTER

    @property
    def watches(self) -> Level:
        return Samples.RAW

    def relevant(self, ctx: ArtifactContext) -> Relevance:
        return self._relevance

    async def act(self, ctx: ArtifactContext, worktree: Worktree) -> list[Event]:
        self.acted.append(ctx)
        if self._fail:
            raise RuntimeError("team crashed")
        return []


class TestTeamRunner:
    async def test_acts_once_per_artifact_version(self, tmp_path: Path) -> None:
        h = Harness(tmp_path)
        team = RecordingTeam()
        runner = h.runner(team)
        artifact = h.create()
        assert await runner.step() == 1
        assert await runner.step() == 0
        h.push(artifact, {"a.txt": "a"})  # something new happened to the artifact
        assert await runner.step() == 1
        assert len(team.acted) == 2

    async def test_irrelevant_artifacts_are_not_claimed_or_asked_again(self, tmp_path: Path) -> None:
        h = Harness(tmp_path)
        team = RecordingTeam(Relevance.NO)
        runner = h.runner(team)
        h.create()
        assert await runner.step() == 0
        assert h.board.current().history(Samples.A) == [Samples.created()]  # no claim was made
        assert await runner.step() == 0
        assert team.acted == []

    async def test_skips_artifacts_claimed_by_another_team(self, tmp_path: Path) -> None:
        h = Harness(tmp_path)
        team = RecordingTeam()
        h.create()
        h.claims.claim(Samples.A, Samples.EXECUTOR, timedelta(minutes=10))
        assert await h.runner(team).step() == 0
        assert team.acted == []

    async def test_releases_the_claim_when_the_team_crashes(self, tmp_path: Path) -> None:
        h = Harness(tmp_path)
        h.create()
        with pytest.raises(RuntimeError):
            await h.runner(RecordingTeam(fail=True)).step()
        assert h.board.current().active_claims(h.clock.now()) == []
        assert not (tmp_path / "worktrees" / Samples.A).exists()


class TestFakeExecutor:
    async def test_commits_the_task_and_requested_fixes(self, tmp_path: Path) -> None:
        h = Harness(tmp_path)
        executor = FakeExecutor(Samples.RAW)
        artifact = h.create()
        h.board.append(
            [
                CommitPushed(artifact=artifact.id, commit=h.push(artifact, {}, announce=False)),
                Rejected(
                    artifact=artifact.id,
                    level=Samples.RAW,
                    findings=(Samples.finding(),),
                    gate_decision=DecisionId("d-1"),
                ),
            ]
        )
        ctx = h.contexts.build(artifact.id)
        assert executor.relevant(ctx) is Relevance.YES
        worktree = h.repository.checkout_artifact(artifact)
        (event,) = await executor.act(ctx, worktree)
        assert isinstance(event, CommitPushed)
        assert "fixed: Does not address every part of the task" in worktree.read("a-0001.md")

    def test_is_relevant_only_before_the_first_commit_or_after_a_rejection(self, tmp_path: Path) -> None:
        h = Harness(tmp_path)
        executor = FakeExecutor(Samples.RAW)
        artifact = h.create()
        assert executor.relevant(h.contexts.build(artifact.id)) is Relevance.YES
        h.push(artifact, {"a.txt": "a"})
        assert executor.relevant(h.contexts.build(artifact.id)) is Relevance.NO


class TestTeamCatalog:
    def test_builds_registered_teams(self) -> None:
        team = TeamCatalog.with_fakes().build(TeamConfig(use="fake_executor", watches=Samples.RAW))
        assert team.name == "executor"
        assert team.watches == Samples.RAW

    def test_unknown_team_is_an_error(self) -> None:
        with pytest.raises(ValueError, match="unknown team"):
            TeamCatalog.with_fakes().build(TeamConfig(use="nobody", watches=Samples.RAW))

    def test_names_are_registered_once(self) -> None:
        catalog = TeamCatalog.with_fakes()
        with pytest.raises(ValueError, match="already registered"):
            catalog.register("fake_executor", lambda config: FakeExecutor(config.watches))
