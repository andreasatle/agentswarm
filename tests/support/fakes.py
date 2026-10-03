"""Fake LangChain chat models, so the real create_agent loop runs offline."""

from collections.abc import Callable, Iterator, Sequence
from typing import Any, Self

from langchain.messages import AIMessage
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.runnables import Runnable


class FakeToolModel(GenericFakeChatModel):
    """Replays scripted AI messages, including tool calls. Binding tools is accepted and ignored."""

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

    def model(self) -> FakeToolModel:
        messages: Iterator[AIMessage] = iter(self._messages)
        return FakeToolModel(messages=messages)
