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
    def allowed(cls) -> bool:
        """Whether the budget allows another attempt now; always outside a runner's action or without one."""
        check = cls._allowed.get()
        return check is None or check()

    @classmethod
    def current(cls) -> ArtifactId | None:
        return cls._current.get()


class SpendLedger:
    def __init__(
        self,
        board: Blackboard,
        shared: SharedSpend | None = None,  # the machine-wide ledger the limits are checked against
        project: str = "",
        accounts: Mapping[str, str] | None = None,  # model name -> who bills it, e.g. "openrouter.ai"
    ) -> None:
        self._board = board
        self._shared = shared
        self._project = project
        self._accounts = dict(accounts or {})

    def meter(self, by: str, model: str, price: Price | None) -> Meter:
        """A callback for one team's worker (or one decision point): records each usage it is given."""
        return lambda usage: self.record(by, model, usage, price)

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
                )
            ]
        )
