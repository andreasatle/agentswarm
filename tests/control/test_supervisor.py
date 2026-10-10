from datetime import timedelta
from pathlib import Path
from typing import Any

from agentswarm.board.artifacts import ArtifactFactory
from agentswarm.control.supervisor import Supervisor
from agentswarm.core.config import JevMode, ProjectConfig
from agentswarm.core.domain import ArtifactCreated, ArtifactId, Escalated, Paused, Rejected, Resumed, Sized
from agentswarm.jev.api import NoulAnswer
from agentswarm.judging.deciders import FakeDecider
from agentswarm.judging.judgement import Judgement
from agentswarm.judging.judges import DriftCheck, Judge
from agentswarm.judging.policy import Policy
from agentswarm.teams.executor import FakeExecutor
from tests.support.harness import Harness
from tests.support.samples import Samples


class Supervised:
    """A harness plus a supervisor over it, with a drift judge answering as scripted."""

    def __init__(
        self, tmp_path: Path, on_course: float | None = None, mode: JevMode = JevMode.ACTIVE, **settings: Any
    ) -> None:
        self.h = Harness(tmp_path)
        config: dict[str, Any] = {
            "project": "demo",
            "domain": "software",
            "north_star": Harness.NORTH_STAR,
            "levels": ["raw", "experimental", "candidate"],
            "teams": [],
            "gates": [],
            "supervisor": {"stall_after_minutes": 30, "max_rejections": 3, "drift_every": 2},
            "budgets": {"max_rounds_per_artifact": 4},
        } | settings
        drift = None
        if on_course is not None:
            judge = Judge(DriftCheck)
            decider = FakeDecider([DriftCheck(on_course=NoulAnswer(noul=on_course))])
            drift = Judgement(judge, Policy(judge), decider, self.h.decisions, mode, self.h.clock)
        self._config = ProjectConfig.model_validate(config)
        self._drift = drift
        self.supervisor = self.started()

    def started(self) -> Supervisor:
        """A supervisor starting now, as at the start of a run."""
        return Supervisor(
            self._config, self.h.board, self.h.repository, self.h.decisions, self._drift, self.h.clock
        )

    def escalations(self, id: ArtifactId) -> list[str]:
        return [e.reason for e in self.h.board.current().history(id) if isinstance(e, Escalated)]

    def merge(self, *numbers: int) -> None:
        """Create, commit and promote artifacts a-000<n> into experimental."""
        for n in numbers:
            artifact = self.h.create(ArtifactId(f"a-{n:04d}"), task=f"task {n}")
            self.h.push(artifact, {f"f{n}.txt": "x"})
            assert self.h.promoter.step() == 1

    def rejection(self) -> Rejected:
        return Rejected(
            artifact=Samples.A, level=Samples.RAW, findings=(Samples.finding(),), gate_decision=None
        )


class TestStalls:
    def test_an_idle_artifact_is_escalated_once(self, tmp_path: Path) -> None:
        s = Supervised(tmp_path)
        s.h.create()
        assert s.supervisor.step() == 0
        s.h.clock.advance(timedelta(minutes=31))
        assert s.supervisor.step() == 1
        assert s.supervisor.step() == 0  # one escalation per state
        assert s.escalations(Samples.A) == ["supervisor: stalled at raw: nothing happened for 31 minutes"]

    def test_a_change_after_an_escalation_lets_it_escalate_again(self, tmp_path: Path) -> None:
        s = Supervised(tmp_path)
        artifact = s.h.create()
        s.h.clock.advance(timedelta(minutes=31))
        s.supervisor.step()
        s.h.push(artifact, {"a.txt": "a"})
        s.h.clock.advance(timedelta(minutes=31))
        assert s.supervisor.step() == 1
        assert len(s.escalations(Samples.A)) == 2

    def test_time_before_the_supervisor_started_is_no_stall(self, tmp_path: Path) -> None:
        s = Supervised(tmp_path)
        s.h.create()
        s.h.clock.advance(timedelta(hours=10))  # the computer was off between runs
        s.supervisor = s.started()
        assert s.supervisor.step() == 0
        s.h.clock.advance(timedelta(minutes=31))
        assert s.supervisor.step() == 1

    def test_an_artifact_waiting_its_turn_is_no_stall(self, tmp_path: Path) -> None:
        s = Supervised(tmp_path)
        s.h.create()
        s.h.create(ArtifactId("a-0002"), task="task 2")
        assert s.h.claims.claim(ArtifactId("a-0002"), Samples.EXECUTOR, timedelta(hours=2)) is not None
        s.h.clock.advance(timedelta(minutes=31))
        assert s.supervisor.step() == 1  # the claimed one, not the one queued behind it
        assert s.escalations(Samples.A) == []

    def test_a_child_waiting_for_its_earlier_sibling_is_no_stall(self, tmp_path: Path) -> None:
        s = Supervised(tmp_path)
        parent = s.h.create()
        factory = ArtifactFactory(s.h.board, Harness.LADDER)
        first, second = (factory.child(parent, i, f"part {i}", Samples.EXECUTOR) for i in (1, 2))
        s.h.board.append([ArtifactCreated(artifact=first), ArtifactCreated(artifact=second)])
        s.h.board.append([Sized(artifact=parent.id, fits=False, children=(first.id, second.id))])
        s.h.clock.advance(timedelta(minutes=31))
        s.supervisor.step()
        assert s.escalations(second.id) == []  # its turn comes when the first one merges

    def test_umbrellas_and_the_top_level_are_never_stalled(self, tmp_path: Path) -> None:
        s = Supervised(tmp_path, levels=["raw", "experimental"])
        artifact = s.h.create()
        s.h.push(artifact, {"a.txt": "a"})
        s.h.promoter.step()  # now at experimental, the top level of this ladder
        s.h.clock.advance(timedelta(hours=5))
        assert s.supervisor.step() == 0


class TestRejectionsAndBudget:
    def test_repeated_rejections_escalate(self, tmp_path: Path) -> None:
        s = Supervised(tmp_path)
        s.h.create()
        s.h.board.append([s.rejection(), s.rejection()])
        assert s.supervisor.step() == 0
        s.h.board.append([s.rejection()])
        assert s.supervisor.step() == 1
        assert s.escalations(Samples.A) == ["supervisor: rejected 3 times in a row"]

    def test_rounds_over_the_budget_escalate(self, tmp_path: Path) -> None:
        s = Supervised(tmp_path)
        artifact = s.h.create()
        for i in range(5):
            s.h.push(artifact, {"a.txt": f"{i}"})
        assert s.supervisor.step() == 1
        assert s.escalations(Samples.A) == ["supervisor: used 5 rounds, over the budget of 4"]


class TestDrift:
    def test_drift_pauses_new_work_in_active_mode(self, tmp_path: Path) -> None:
        s = Supervised(tmp_path, on_course=0.05)
        s.merge(1)
        assert s.supervisor.step() == 0  # fewer than drift_every changes merged
        s.merge(2)
        assert s.supervisor.step() == 1
        assert s.h.board.current().paused
        (decision,) = [d for d in s.h.decisions.all() if d.point == "drift"]
        assert "task 1" in decision.state.task and "task 2" in decision.state.task
        assert "f2.txt" in decision.state.diff

    def test_shadow_mode_only_logs_drift(self, tmp_path: Path) -> None:
        s = Supervised(tmp_path, on_course=0.05, mode=JevMode.SHADOW)
        s.merge(1, 2)
        assert s.supervisor.step() == 0
        assert not s.h.board.current().paused
        assert s.supervisor.step() == 0  # asked once per drift_every changes
        assert [d.point for d in s.h.decisions.all()].count("drift") == 1


class TestPause:
    async def test_a_pause_stops_teams_that_start_work_until_resumed(self, tmp_path: Path) -> None:
        h = Harness(tmp_path)
        runner = h.runner(FakeExecutor(Samples.RAW))
        h.create()
        h.board.append([Paused(reason="drift")])
        assert await runner.step() == 0
        h.board.append([Resumed(reason="a person looked")])
        assert await runner.step() == 1
