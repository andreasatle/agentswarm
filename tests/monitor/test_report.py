from pathlib import Path

from agentswarm.board.artifacts import ArtifactFactory
from agentswarm.core.domain import Artifact, ArtifactCreated, Escalated, Paused, Promoted, TeamName
from agentswarm.monitor.report import HardeningSummary, RunVerdict
from tests.support.harness import Harness


class TestHardeningSummary:
    def test_a_split_fix_counts_as_merged_once_every_step_is(self, tmp_path: Path) -> None:
        h = Harness(tmp_path)
        factory = ArtifactFactory(h.board, Harness.LADDER)
        fix = factory.create("fix what a red team found", TeamName("red_team"))
        steps = [factory.child(fix, n, f"step {n}", TeamName("planner")) for n in (1, 2)]
        h.board.append([ArtifactCreated(artifact=step) for step in steps])

        def merge(step: Artifact) -> None:
            lowest, base = Harness.LADDER.lowest, Harness.LADDER.base
            promoted = Promoted(
                artifact=step.id, from_level=lowest, to_level=base, gate_decision=None, commit=f"m-{step.id}"
            )
            h.board.append([promoted])

        merge(steps[0])
        assert not HardeningSummary.merged(h.board.current(), fix.id)  # one step still open
        merge(steps[1])
        assert HardeningSummary.merged(h.board.current(), fix.id)


class TestRunVerdict:
    def test_a_run_says_why_it_stopped(self, tmp_path: Path) -> None:
        h = Harness(tmp_path)
        lowest = Harness.LADDER.lowest

        def verdict() -> str:
            return RunVerdict.render(h.board.current(), h.log.read(), lowest)

        assert verdict() == "FINISHED: no work left at the lowest level"
        task = ArtifactFactory(h.board, Harness.LADDER).create("a task", TeamName("human"))
        assert verdict() == "STOPPED with 1 task(s) unfinished"
        h.board.append([Escalated(artifact=task.id, reason="every model failed")])
        assert "1 waiting for a person" in verdict()
        h.board.append([Paused(reason="the model provider is unavailable (haiku55)")])
        assert verdict().startswith("STOPPED, paused: the model provider is unavailable (haiku55)")
