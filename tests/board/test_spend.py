from datetime import timedelta

import pytest
from langchain.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult
from pydantic import BaseModel

from agentswarm.board.blackboard import Blackboard
from agentswarm.board.eventlog import InMemoryEventLog
from agentswarm.board.spend import SpendLedger
from agentswarm.core.clock import FakeClock
from agentswarm.core.config import Price
from agentswarm.core.domain import Spent, Usage
from agentswarm.llm.workers import LangChainWorker, Tokens, WorkerError
from tests.support.fakes import FakeToolModel
from tests.support.samples import Samples


class Ledger:
    def __init__(self) -> None:
        self.clock = FakeClock(Samples.AT)
        self.board = Blackboard(InMemoryEventLog(Samples.PROJECT, self.clock))
        self.ledger = SpendLedger(self.board)

    def spent(self) -> list[Spent]:
        return [e.event for e in self.board.log.read() if isinstance(e.event, Spent)]


class TestSpendLedger:
    def test_prices_usage_and_records_it_on_the_board(self) -> None:
        ledger = Ledger()
        ledger.ledger.record(
            "executor",
            "large",
            Usage(input_tokens=1_000_000, output_tokens=500_000),
            Price(input=3, output=15),
        )
        (spent,) = ledger.spent()
        assert (spent.by, spent.model, spent.usd, spent.priced) == ("executor", "large", 3 + 7.5, True)

    def test_unpriced_models_cost_nothing_and_say_so(self) -> None:
        ledger = Ledger()
        ledger.ledger.meter("red_team", "local", None)(Usage(input_tokens=10, output_tokens=5))
        (spent,) = ledger.spent()
        assert (spent.usd, spent.priced) == (0.0, False)

    def test_calls_without_tokens_record_nothing(self) -> None:
        ledger = Ledger()
        ledger.ledger.record("executor", "m", Usage(), Price(input=1, output=1))
        assert ledger.spent() == []

    def test_views_sum_spend_since_a_moment(self) -> None:
        ledger = Ledger()
        price = Price(input=1_000_000, output=0)  # one dollar per token, to keep the numbers readable
        ledger.ledger.record("a", "m", Usage(input_tokens=2), price)
        ledger.clock.advance(timedelta(days=1))
        ledger.ledger.record("a", "m", Usage(input_tokens=3), price)
        views = ledger.board.current()
        assert views.spent_since(Samples.AT) == 5.0
        assert views.spent_since(Samples.AT + timedelta(hours=1)) == 3.0


class TestTokens:
    def test_sums_the_usage_langchain_reports(self) -> None:
        messages = [
            AIMessage(
                content="a", usage_metadata={"input_tokens": 10, "output_tokens": 2, "total_tokens": 12}
            ),
            AIMessage(content="b"),
            AIMessage(content="c", usage_metadata={"input_tokens": 5, "output_tokens": 1, "total_tokens": 6}),
        ]
        tokens = Tokens()
        tokens.on_llm_end(LLMResult(generations=[[ChatGeneration(message=m)] for m in messages]))
        # two calls reported usage; the larger prompt had 10 tokens
        assert tokens.usage == Usage(input_tokens=15, output_tokens=3, model_calls=2, largest_input=10)

    async def test_a_worker_reports_its_usage_to_its_meter(self) -> None:
        reply = AIMessage(
            content="",
            tool_calls=[{"name": "Summary", "args": {"text": "done"}, "id": "c1"}],
            usage_metadata={"input_tokens": 120, "output_tokens": 30, "total_tokens": 150},
        )
        heard: list[Usage] = []
        await LangChainWorker(FakeToolModel(messages=iter([reply])), meter=heard.append).run(
            "Do it.", Request(task="t"), Summary
        )
        assert heard == [Usage(input_tokens=120, output_tokens=30, model_calls=1, largest_input=120)]

    async def test_calls_that_completed_are_metered_when_a_later_call_fails(self) -> None:
        first = AIMessage(
            content="",
            tool_calls=[{"name": "look_around", "args": {}, "id": "c1"}],
            usage_metadata={"input_tokens": 120, "output_tokens": 30, "total_tokens": 150},
        )
        heard: list[Usage] = []
        worker = LangChainWorker(FakeToolModel(messages=iter([first])), meter=heard.append)  # no second reply
        with pytest.raises(WorkerError):
            await worker.run("Do it.", Request(task="t"), Summary)
        assert heard == [Usage(input_tokens=120, output_tokens=30, model_calls=1, largest_input=120)]


class Summary(BaseModel):
    text: str


class Request(BaseModel):
    task: str
