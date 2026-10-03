import asyncio
import threading
from datetime import timedelta
from pathlib import Path

from agentswarm.board.spend import SpendLedger
from agentswarm.control.budget import Budget
from agentswarm.control.scheduler import RateLimiter, Scheduler, SchedulerConfig
from agentswarm.control.supervisor import Supervisor
from agentswarm.core.config import Price, ProjectConfig
from agentswarm.core.domain import Escalated, ProjectId, Usage
from agentswarm.teams.executor import FakeExecutor
from agentswarm.workspace.sandbox import LocalSandbox
from tests.support.harness import Harness
from tests.support.samples import Samples


class Clockwork:
    """A fake monotonic clock whose sleep moves it forward."""

    def __init__(self) -> None:
        self.t = 0.0
        self.slept: list[float] = []

    def now(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.t += seconds


class TestRateLimiter:
    def test_waits_once_the_window_is_full(self) -> None:
        clock = Clockwork()
        limiter = RateLimiter(2, now=clock.now, sleep=clock.sleep)
        limiter.acquire()
        clock.t = 10.0
        limiter.acquire()
        assert clock.slept == []
        limiter.acquire()  # the third within 60 s waits until the first leaves the window
        assert clock.slept == [50.0]


class TestScheduler:
    async def test_caps_concurrent_model_calls_across_projects(self) -> None:
        scheduler = Scheduler(SchedulerConfig(max_model_calls=2))
        running, peak = 0, 0

        async def act(project: str) -> None:
            nonlocal running, peak
            async with scheduler.model_call(ProjectId(project)):
                running += 1
                peak = max(peak, running)
                await asyncio.sleep(0.01)
                running -= 1

        await asyncio.gather(*(act(p) for p in ["a", "b", "a", "b", "a"]))
        assert peak == 2
        assert scheduler.granted == {"a": 3, "b": 2}

    def test_throttled_sandboxes_share_slots(self, tmp_path: Path) -> None:
        scheduler = Scheduler(SchedulerConfig(max_sandboxes=1))
        sandbox = scheduler.throttle(LocalSandbox())
        results: list[bool] = []
        threads = [
            threading.Thread(target=lambda: results.append(sandbox.run(["true"], tmp_path, 10).passed))
            for _ in range(3)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert results == [True, True, True]


class Spending:
    """A harness whose board has spend recorded today."""

    PRICE = Price(input=1_000_000, output=0)  # a dollar a token: readable numbers

    def __init__(self, tmp_path: Path, usd: int) -> None:
        self.h = Harness(tmp_path)
        SpendLedger(self.h.board).record("executor", "m", Usage(input_tokens=usd), self.PRICE)


class TestBudget:
    def test_a_project_over_its_daily_budget_is_exhausted_until_tomorrow(self, tmp_path: Path) -> None:
        s = Spending(tmp_path, usd=5)
        budget = Budget(5.0, s.h.clock)
        assert budget.exhausted(s.h.board.current())
        assert not Budget(10.0, s.h.clock).exhausted(s.h.board.current())
        assert not Budget(None, s.h.clock).exhausted(s.h.board.current())
        s.h.clock.advance(timedelta(days=1))
        assert not budget.exhausted(s.h.board.current())

    async def test_teams_wait_while_the_budget_is_exhausted(self, tmp_path: Path) -> None:
        s = Spending(tmp_path, usd=5)
        s.h.create()
        runner = s.h.runner(FakeExecutor(Samples.RAW), budget=Budget(5.0, s.h.clock))
        assert await runner.step() == 0
        s.h.clock.advance(timedelta(days=1))
        assert await runner.step() == 1

    def test_the_supervisor_reports_an_exhausted_budget_once_a_day(self, tmp_path: Path) -> None:
        s = Spending(tmp_path, usd=5)
        config = ProjectConfig.model_validate(
            {
                "project": "demo",
                "domain": "software",
                "north_star": "n",
                "levels": ["raw", "experimental"],
                "teams": [],
                "gates": [],
            }
        )
        supervisor = Supervisor(
            config, s.h.board, s.h.repository, s.h.decisions, None, s.h.clock, Budget(5.0, s.h.clock)
        )
        assert supervisor.step() == 1
        assert supervisor.step() == 0
        escalations = [e.event for e in s.h.log.read() if isinstance(e.event, Escalated)]
        assert escalations[0].artifact is None and "budget exhausted: $5.00 of $5.00" in escalations[0].reason
