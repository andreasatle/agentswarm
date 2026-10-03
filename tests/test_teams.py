from datetime import timedelta
from pathlib import Path

import pytest

from agentswarm.domain import ArtifactContext, Event, Level, TeamName
from agentswarm.git import Worktree
from agentswarm.teams import Relevance
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
