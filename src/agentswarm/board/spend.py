"""Spend on the board (Phase 6): every model call and Jev decision appends what it cost.

Workers and deciders report their token usage to a meter; the meter prices it and appends a `Spent` event.
Like the decision log, this is infrastructure, not a team: it writes the board directly. Because spend is
on the board, budgets survive restarts and replays.
"""

from collections.abc import Callable

from agentswarm.board.blackboard import Blackboard
from agentswarm.core.config import Price
from agentswarm.core.domain import Spent, Usage

type Meter = Callable[[Usage], None]


class SpendLedger:
    def __init__(self, board: Blackboard) -> None:
        self._board = board

    def meter(self, by: str, model: str, price: Price | None) -> Meter:
        """A callback for one team's worker (or one decision point): records each usage it is given."""
        return lambda usage: self.record(by, model, usage, price)

    def record(self, by: str, model: str, usage: Usage, price: Price | None) -> None:
        if usage.input_tokens == 0 and usage.output_tokens == 0:
            return
        usd = price.usd_of(usage) if price is not None else 0.0
        self._board.append(
            [
                Spent(
                    by=by,
                    model=model,
                    input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                    usd=usd,
                    priced=price is not None,
                    cache_read_tokens=usage.cache_read_tokens,
                    cache_write_tokens=usage.cache_write_tokens,
                    model_calls=usage.model_calls,
                    largest_input=usage.largest_input,
                )
            ]
        )
