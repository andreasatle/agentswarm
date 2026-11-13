"""A project's daily spend budget (proposal §14.1, `budgets.usd_per_day`).

Spend is on the board (`Spent` events, see board/spend.py). A project over its budget for the current UTC
day starts no team actions: the runners skip their turn (like a pause, without marking anything handled),
and the supervisor escalates once a day. The next day, work resumes by itself.
"""

from datetime import UTC, datetime

from agentswarm.board.blackboard import BoardViews
from agentswarm.control.limits import SharedSpend
from agentswarm.core.clock import Clock
from agentswarm.core.domain import OverBudget


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
        self._refused: tuple[datetime, str] | None = None  # a model call refused today, and why
        # Model runs admitted but not yet recorded on the board, by run: their worst case so far. Concurrent
        # runs (parallel team actions) each count the others', so together they stay within the budget.
        self._in_flight: dict[str, float] = {}

    def day_start(self) -> datetime:
        now = self._clock.now().astimezone(UTC)
        return now.replace(hour=0, minute=0, second=0, microsecond=0)

    def spent_today(self, views: BoardViews) -> float:
        return views.spent_since(self.day_start())

    def exhausted(self, views: BoardViews) -> bool:
        return self.reason(views) is not None

    def admit(self, views: BoardViews, usd: float, run: str = "") -> None:
        """Admit a model call whose worst case costs `usd` (the run's unrecorded use so far included), or
        refuse it (OverBudget) when that would pass this project's budget or a machine-wide limit (audit A03:
        checked before every call, not only between actions, so an approved budget is a cap). Other runs in
        flight count with their worst case until they are recorded (`release`). A refusal stands for the rest
        of the day: no new action starts."""
        others = sum(v for k, v in self._in_flight.items() if k != run)
        spent = self.spent_today(views) + others
        if self._usd_per_day is not None and spent + usd > self._usd_per_day:
            self.refuse(
                f"budget: a model call needing up to ${usd:.2f} won't fit "
                f"(${spent:.2f} of ${self._usd_per_day:.2f} spent or in flight today)"
            )
        if self._shared is not None and (over := self._shared.over(self._accounts, usd + others)) is not None:
            self.refuse(f"a model call needing up to ${usd:.2f} won't fit the {over}")
        self._in_flight[run] = usd

    def release(self, run: str) -> None:
        """The run's spend is recorded on the board (or it made no call): it no longer counts as in flight."""
        self._in_flight.pop(run, None)

    def refuse(self, reason: str) -> None:
        self._refused = (self.day_start(), reason)
        raise OverBudget(reason)

    def reason(self, views: BoardViews) -> str | None:
        """Why no new action may start: this project's budget, or a machine-wide limit; None when it may."""
        if self._refused is not None and self._refused[0] == self.day_start():
            return self._refused[1]
        if self._usd_per_day is not None and self.spent_today(views) + self._reserve >= self._usd_per_day:
            return f"budget exhausted: ${self.spent_today(views):.2f} of ${self._usd_per_day:.2f} spent today"
        if self._shared is not None:
            return self._shared.over(self._accounts, self._reserve)
        return None

    def describe(self, views: BoardViews) -> str:
        return self.reason(views) or "within budget"
