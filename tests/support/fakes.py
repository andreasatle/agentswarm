"""Fake LangChain chat models, so the real create_agent loop runs offline; and a fake embedding model."""

import hashlib
import math
import re
from collections.abc import Callable, Iterator, Sequence
from typing import Any, Self

from langchain.messages import AIMessage
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import BaseMessage
from langchain_core.outputs import ChatResult
from langchain_core.runnables import Runnable
from pydantic import BaseModel, Field

from agentswarm.llm.workers import Toolbox


class FakeToolModel(GenericFakeChatModel):
    """Replays scripted AI messages, including tool calls. Binding tools is accepted and ignored."""

    prompts: list[list[BaseMessage]] = Field(
        default_factory=list[list[BaseMessage]]
    )  # what each call was sent

    def _generate(self, messages: list[BaseMessage], *args: Any, **kwargs: Any) -> ChatResult:
        self.prompts.append(list(messages))
        return super()._generate(messages, *args, **kwargs)

    def bind_tools(  # type: ignore[override]
        self, tools: Sequence[dict[str, Any] | type | Callable[..., Any]], **kwargs: Any
    ) -> Runnable[Any, Any]:
        return self


class Replies:
    """Builds the scripted messages for FakeToolModel."""

    def __init__(self) -> None:
        self._messages: list[AIMessage] = []

    def call(self, tool: str, **args: Any) -> Self:
        self._messages.append(
            AIMessage(
                content="", tool_calls=[{"name": tool, "args": args, "id": f"call-{len(self._messages)}"}]
            )
        )
        return self

    def text(self, content: str) -> Self:
        self._messages.append(AIMessage(content=content))
        return self

    def cut_off(self, content: str) -> Self:
        """A reply that stopped at the output limit, as Ollama reports it."""
        self._messages.append(AIMessage(content=content, response_metadata={"done_reason": "length"}))
        return self

    def model(self) -> FakeToolModel:
        messages: Iterator[AIMessage] = iter(self._messages)
        return FakeToolModel(messages=messages)


class ScriptedWorker:
    """A worker that answers by what is asked for: one handler per output type, called with the payload.

    Lets one fake model serve several teams in a run (planner, writer, reviewers), each by its output type.
    """

    def __init__(
        self, handlers: dict[type[BaseModel], Callable[[BaseModel, Toolbox | None], BaseModel]]
    ) -> None:
        self._handlers = handlers
        self.calls: list[type[BaseModel]] = []

    async def run[T: BaseModel](
        self, instructions: str, payload: BaseModel, output: type[T], toolbox: Toolbox | None = None
    ) -> T:
        self.calls.append(output)
        result = self._handlers[output](payload, toolbox)
        assert isinstance(result, output)
        return result


class HashingEmbedder:
    """A deterministic stand-in for an embedding model: each word adds to the dimension its hash picks, so
    texts sharing words lie close. Counts the texts it embeds, to show what an index keeps."""

    def __init__(self, dimensions: int = 64, name: str = "fake:hashing") -> None:
        self._dimensions = dimensions
        self._name = name
        self.embedded = 0

    @property
    def name(self) -> str:
        return self._name

    def vector(self, text: str) -> list[float]:
        vector = [0.0] * self._dimensions
        for word in re.findall(r"[a-z0-9]+", text.lower()):
            vector[int(hashlib.md5(word.encode()).hexdigest(), 16) % self._dimensions] += 1.0
        norm = math.sqrt(sum(v * v for v in vector)) or 1.0
        return [v / norm for v in vector]

    async def documents(self, texts: Sequence[str]) -> list[list[float]]:
        self.embedded += len(texts)
        return [self.vector(t) for t in texts]

    async def query(self, text: str) -> list[float]:
        return self.vector(text)
