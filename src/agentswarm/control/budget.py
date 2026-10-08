"""A project's daily spend budget (proposal §14.1, `budgets.usd_per_day`).

Spend is on the board (`Spent` events, see board/spend.py). A project over its budget for the current UTC
day starts no team actions: the runners skip their turn (like a pause, without marking anything handled),
and the supervisor escalates once a day. The next day, work resumes by itself.
"""

from datetime import UTC, datetime

from agentswarm.board.blackboard import BoardViews
from agentswarm.control.limits import SharedSpend
from agentswarm.core.clock import Clock


class Budget:
    def __init__(
        self,
        usd_per_day: float | None,
        clock: Clock,
        reserve_usd: float = 0.0,
        shared: SharedSpend | None = None,  # machine-wide limits, across every run (control/limits.py)
        accounts: frozenset[str] = frozenset(),  # who bills this project's models, e.g. "anthropic"
    ) -> None:
        self._usd_per_day = usd_per_day
        self._clock = clock
        self._shared = shared
        self._accounts = accounts
        self._reserve = reserve_usd  # what one action may cost: none starts with less than this left

    def day_start(self) -> datetime:
        now = self._clock.now().astimezone(UTC)
        return now.replace(hour=0, minute=0, second=0, microsecond=0)

    def spent_today(self, views: BoardViews) -> float:
        return views.spent_since(self.day_start())

    def exhausted(self, views: BoardViews) -> bool:
        return self.reason(views) is not None

    def reason(self, views: BoardViews) -> str | None:
        """Why no new action may start: this project's budget, or a machine-wide limit; None when it may."""
        if self._usd_per_day is not None and self.spent_today(views) + self._reserve >= self._usd_per_day:
            return f"budget exhausted: ${self.spent_today(views):.2f} of ${self._usd_per_day:.2f} spent today"
        if self._shared is not None:
            return self._shared.over(self._accounts, self._reserve)
        return None

    def describe(self, views: BoardViews) -> str:
        return self.reason(views) or "within budget"
