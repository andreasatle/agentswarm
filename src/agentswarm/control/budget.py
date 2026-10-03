"""A project's daily spend budget (proposal §14.1, `budgets.usd_per_day`).

Spend is on the board (`Spent` events, see board/spend.py). A project over its budget for the current UTC
day starts no team actions: the runners skip their turn (like a pause, without marking anything handled),
and the supervisor escalates once a day. The next day, work resumes by itself.
"""

from datetime import UTC, datetime

from agentswarm.board.blackboard import BoardViews
from agentswarm.core.clock import Clock


class Budget:
    def __init__(self, usd_per_day: float | None, clock: Clock) -> None:
        self._usd_per_day = usd_per_day
        self._clock = clock

    def day_start(self) -> datetime:
        now = self._clock.now().astimezone(UTC)
        return now.replace(hour=0, minute=0, second=0, microsecond=0)

    def spent_today(self, views: BoardViews) -> float:
        return views.spent_since(self.day_start())

    def exhausted(self, views: BoardViews) -> bool:
        return self._usd_per_day is not None and self.spent_today(views) >= self._usd_per_day

    def describe(self, views: BoardViews) -> str:
        return f"budget exhausted: ${self.spent_today(views):.2f} of ${self._usd_per_day:.2f} spent today"
