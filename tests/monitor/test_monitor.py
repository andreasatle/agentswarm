import json
import threading
import urllib.request
from datetime import timedelta
from pathlib import Path
from typing import Any

from agentswarm.core.config import ProjectConfig
from agentswarm.core.domain import ArtifactId, AttemptFailed, Scored, Spent, Tally
from agentswarm.monitor.server import MonitorServer
from agentswarm.monitor.state import MonitorState, Status
from tests.support.harness import Harness
from tests.support.samples import Samples

CONFIG = ProjectConfig.model_validate(
    {
        "project": "demo",
        "domain": "software",
        "north_star": Harness.NORTH_STAR,
        "levels": list(Harness.LADDER.levels),
        "teams": [],
        "gates": [],
        "budgets": {"usd_per_day": 3.0},
    }
)


class Watched:
    """A board with one merged step, one being worked on, a failed attempt, spend and two scores."""

    def __init__(self, tmp_path: Path) -> None:
        self.h = Harness(tmp_path)
        merged = self.h.create(task="parse integers")
        self.h.board.append(
            [
                Scored(
                    level=Samples.EXPERIMENTAL,
                    commit="c0",
                    seen=Tally(passed=5, total=100),
                    held_out=None,
                    spent_usd=0.0,
                )
            ]
        )
        failed = AttemptFailed(artifact=merged.id, team=Samples.EXECUTOR, model="cheap", reason="checks fail")
        spent = Spent(
            by=Samples.EXECUTOR,
            model="strong",
            input_tokens=1,
            output_tokens=1,
            usd=0.4,
            priced=True,
            artifact=merged.id,
        )
        self.h.board.append([failed, spent])
        self.h.push(merged, {"a.py": "x = 1\n"})
        assert self.h.promoter.step() == 1
        self.working = self.h.create(Samples.B, task="parse strings")
        assert self.h.claims.claim(self.working.id, Samples.EXECUTOR, timedelta(minutes=10)) is not None
        self.h.board.append(
            [
                Scored(
                    level=Samples.EXPERIMENTAL,
                    commit="c1",
                    seen=Tally(passed=40, total=100),
                    held_out=None,
                    spent_usd=0.4,
                )
            ]
        )
        self.merged = merged

    def state(self) -> MonitorState:
        return MonitorState(CONFIG, self.h.log)


class TestMonitorState:
    def test_the_overview_teams_models_and_plan(self, tmp_path: Path) -> None:
        w = Watched(tmp_path)
        state = w.state().state(now=w.h.clock.now())
        o = state.overview
        assert [p.seen for p in o.progress] == [0.05, 0.4]
        assert (o.spent_usd, o.budget_per_day_usd, o.levels) == (0.4, 3.0, {"raw": 1, "experimental": 1})
        (executor,) = [t for t in state.teams if t.team == Samples.EXECUTOR]
        assert (executor.working_on, executor.failures, executor.spent_usd) == (Samples.B, 1, 0.4)
        assert {m.model: m.attempts for m in state.models}["cheap"] == 1
        statuses = {n.id: n.status for n in state.nodes}
        assert statuses == {w.merged.id: Status.DONE, Samples.B: Status.WORKING}

    def test_one_steps_history(self, tmp_path: Path) -> None:
        w = Watched(tmp_path)
        detail = w.state().detail(w.merged.id)
        assert detail is not None and any("discarded" in line for _, line in detail.lines)
        assert w.state().detail(ArtifactId("a-0099")) is None


class TestMonitorServer:
    def test_serves_the_page_and_the_state_and_only_reads(self, tmp_path: Path) -> None:
        w = Watched(tmp_path)
        before = w.h.log.head()
        server = MonitorServer(CONFIG, tmp_path / "events.sqlite", port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with urllib.request.urlopen(server.url) as page:
                assert b"AgentSwarm monitor" in page.read()
            with urllib.request.urlopen(server.url + "api/state") as reply:
                state: dict[str, Any] = json.load(reply)
            assert state["overview"]["project"] == "demo"
            with urllib.request.urlopen(f"{server.url}api/artifact/{w.merged.id}") as reply:
                assert json.load(reply)["id"] == w.merged.id
        finally:
            server.shutdown()
            server.server_close()
        assert w.h.log.head() == before
