import asyncio
import threading
from datetime import timedelta
from pathlib import Path

import pytest

from agentswarm.board.spend import SpendLedger
from agentswarm.control.budget import Budget
from agentswarm.control.scheduler import RateLimiter, Scheduler, SchedulerConfig
from agentswarm.control.supervisor import Supervisor
from agentswarm.core.config import Price, ProjectConfig
from agentswarm.core.domain import ArtifactContext, Escalated, Event, OverBudget, ProjectId, Usage
from agentswarm.teams.executor import FakeExecutor
from agentswarm.workspace.git import Worktree
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
        assert Budget(6.0, s.h.clock, reserve_usd=1.0).exhausted(s.h.board.current())  # $1 left is not enough
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


class TestAdmission:
    """Audit A03: every model call is admitted against the budget before it is made."""

    def test_a_call_whose_worst_case_fits_is_admitted_and_one_that_doesnt_is_refused(
        self, tmp_path: Path
    ) -> None:
        s = Spending(tmp_path, usd=4)
        budget = Budget(5.0, s.h.clock)
        budget.admit(s.h.board.current(), 1.0)  # $4 + $1 fits $5 exactly
        with pytest.raises(OverBudget, match=r"needing up to \$1.50 won't fit \(\$4.00 of \$5.00"):
            budget.admit(s.h.board.current(), 1.5)

    def test_a_refusal_stops_new_actions_for_the_day_and_the_supervisor_says_why(
        self, tmp_path: Path
    ) -> None:
        s = Spending(tmp_path, usd=4)
        budget = Budget(5.0, s.h.clock)
        assert not budget.exhausted(s.h.board.current())
        with pytest.raises(OverBudget):
            budget.admit(s.h.board.current(), 2.0)
        assert budget.exhausted(s.h.board.current())  # though $1 is left: the last call needed more
        assert "won't fit" in budget.describe(s.h.board.current())
        s.h.clock.advance(timedelta(days=1))
        assert not budget.exhausted(s.h.board.current())

    def test_the_ledger_admits_through_its_budget_and_without_one_admits_everything(
        self, tmp_path: Path
    ) -> None:
        s = Spending(tmp_path, usd=4)
        with pytest.raises(OverBudget):
            SpendLedger(s.h.board, budget=Budget(5.0, s.h.clock)).meter("writer", "m", None).admit(2.0)
        SpendLedger(s.h.board).meter("writer", "m", None).admit(2.0)

    async def test_a_refused_action_publishes_nothing_and_waits_unhandled(self, tmp_path: Path) -> None:
        s = Spending(tmp_path, usd=0)
        s.h.create()

        class Refused(FakeExecutor):
            calls = 0

            async def act(self, ctx: ArtifactContext, worktree: Worktree) -> list[Event]:
                Refused.calls += 1
                if Refused.calls == 1:
                    raise OverBudget("budget: a model call won't fit")
                return await super().act(ctx, worktree)

        runner = s.h.runner(Refused(Samples.RAW))
        assert await runner.step() == 0
        history = [type(e).__name__ for e in s.h.board.current().history(Samples.A)]
        assert history == ["ArtifactCreated", "Claimed", "Released"]  # nothing of the action's was published
        assert await runner.step() == 1  # not marked handled: tried again once the budget allows it
