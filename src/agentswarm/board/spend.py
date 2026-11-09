"""Spend on the board (Phase 6): every model call and Jev decision appends what it cost.

Workers and deciders report their token usage to a meter; the meter prices it and appends a `Spent` event.
Like the decision log, this is infrastructure, not a team: it writes the board directly. Because spend is
on the board, budgets survive restarts and replays.
"""

from collections.abc import Callable, Generator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from typing import ClassVar

from agentswarm.board.blackboard import Blackboard
from agentswarm.control.budget import Budget
from agentswarm.control.limits import SharedSpend
from agentswarm.core.config import Price
from agentswarm.core.domain import ArtifactId, Spent, Usage

type Meter = Callable[[Usage], None]


class SpendScope:
    """The artifact a team is acting on, so the money its models spend is recorded against it, and whether
    the budget still allows another attempt within the action.

    Set by the team runner around each action, read by the meter and by teams that make several attempts. A
    context variable: concurrent actions in asyncio tasks each keep their own, and nothing has to be passed
    through the workers.
    """

    _current: ClassVar[ContextVar[ArtifactId | None]] = ContextVar("spend_artifact", default=None)
    _allowed: ClassVar[ContextVar[Callable[[], bool] | None]] = ContextVar("spend_allowed", default=None)
    _attempt: ClassVar[ContextVar[str | None]] = ContextVar("spend_attempt", default=None)

    @classmethod
    @contextmanager
    def on(cls, artifact: ArtifactId, allowed: Callable[[], bool] | None = None) -> Generator[None]:
        token = cls._current.set(artifact)
        allowed_token = cls._allowed.set(allowed)
        try:
            yield
        finally:
            cls._allowed.reset(allowed_token)
            cls._current.reset(token)

    @classmethod
    @contextmanager
    def attempt(cls, attempt: str) -> Generator[None]:
        """Within one executor attempt: its spend carries the attempt's id, as do the events that end it, so
        the cost of each attempt is read by identity, not by where the events landed on the board (audit
        A04: a runner publishes an action's events after all its spend, so position misattributed it)."""
        token = cls._attempt.set(attempt)
        try:
            yield
        finally:
            cls._attempt.reset(token)

    @classmethod
    def current_attempt(cls) -> str | None:
        return cls._attempt.get()

    @classmethod
    def allowed(cls) -> bool:
        """Whether the budget allows another attempt now; always outside a runner's action or without one."""
        check = cls._allowed.get()
        return check is None or check()

    @classmethod
    def current(cls) -> ArtifactId | None:
        return cls._current.get()


class LedgerMeter:
    """One team's meter for one model: records the usage it is told, and admits each model call first.

    A worker asks `admit` before every paid call with what the call could cost at most; the ledger's budget
    refuses it (OverBudget) when that wouldn't fit (audit A03). Without a budget everything is admitted.
    """

    def __init__(self, ledger: "SpendLedger", by: str, model: str, price: Price | None) -> None:
        self._ledger = ledger
        self._by = by
        self._model = model
        self._price = price

    def __call__(self, usage: Usage) -> None:
        self._ledger.record(self._by, self._model, usage, self._price)

    def admit(self, usd: float) -> None:
        self._ledger.admit(usd)


class SpendLedger:
    def __init__(
        self,
        board: Blackboard,
        shared: SharedSpend | None = None,  # the machine-wide ledger the limits are checked against
        project: str = "",
        accounts: Mapping[str, str] | None = None,  # model name -> who bills it, e.g. "openrouter.ai"
        budget: Budget | None = None,  # admits every model call against the project's and machine's limits
    ) -> None:
        self._board = board
        self._shared = shared
        self._project = project
        self._accounts = dict(accounts or {})
        self._budget = budget

    def meter(self, by: str, model: str, price: Price | None) -> LedgerMeter:
        """The meter for one team's worker (or one decision point): records each usage it is given."""
        return LedgerMeter(self, by, model, price)

    def admit(self, usd: float) -> None:
        """Refuse (OverBudget) a model call whose worst case `usd` wouldn't fit the budget."""
        if self._budget is not None and usd > 0:
            self._budget.admit(self._board.current(), usd)

    def record(self, by: str, model: str, usage: Usage, price: Price | None) -> None:
        if usage.input_tokens == 0 and usage.output_tokens == 0:
            return
        billed = usage.fully_billed  # then usd is what the provider charged (Price.usd_of)
        usd = usage.billed_usd if billed else (price.usd_of(usage) if price is not None else 0.0)
        if self._shared is not None:
            self._shared.record(self._project, self._accounts.get(model, model), usd)
        self._board.append(
            [
                Spent(
                    by=by,
                    model=model,
                    input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                    usd=usd,
                    priced=price is not None or billed,
                    billed=billed,
                    cache_read_tokens=usage.cache_read_tokens,
                    cache_write_tokens=usage.cache_write_tokens,
                    model_calls=usage.model_calls,
                    largest_input=usage.largest_input,
                    artifact=SpendScope.current(),
                    attempt=SpendScope.current_attempt(),
                )
            ]
        )
