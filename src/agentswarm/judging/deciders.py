"""Deciders answer a judge's questions about a state (proposal §12.4). Jev is one implementation."""

from collections.abc import Sequence
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from agentswarm.jev.client import JevClient, JevError


class DeciderError(Exception):
    """No answers: the decider was unreachable or answered in the wrong shape."""


class Answered[M: BaseModel](BaseModel):
    model_config = ConfigDict(frozen=True)

    answers: M
    model: str  # the concrete version that answered, e.g. "jev-1.13.0"


class Decider(Protocol):
    def decide[M: BaseModel](self, state: BaseModel, spec: type[M]) -> Answered[M]: ...


class JevDecider:
    def __init__(self, client: JevClient) -> None:
        self._client = client

    def decide[M: BaseModel](self, state: BaseModel, spec: type[M]) -> Answered[M]:
        try:
            response = self._client.decide(state, spec)
        except JevError as error:
            raise DeciderError(str(error)) from error
        return Answered[spec](answers=response.answers, model=response.model)


class FakeDecider:
    """Returns scripted answers. Records every call.

    Answers are kept per spec, in the order given: asking a spec takes its next answer, and its last
    answer repeats. So one decider can serve several judges (size, split, gate, join) in one run.
    """

    MODEL = "fake"

    def __init__(self, answers: Sequence[BaseModel] = ()) -> None:
        self._answers: dict[type[BaseModel], list[BaseModel]] = {}
        for answer in answers:
            self._answers.setdefault(type(answer), []).append(answer)
        self.calls: list[tuple[BaseModel, type[BaseModel]]] = []

    def decide[M: BaseModel](self, state: BaseModel, spec: type[M]) -> Answered[M]:
        self.calls.append((state, spec))
        if not self._answers:
            raise DeciderError("FakeDecider has no scripted answers")
        queue = self._answers.get(spec)
        if not queue:
            held = ", ".join(t.__name__ for t in self._answers)
            raise DeciderError(f"FakeDecider holds answers for {held}, asked for {spec.__name__}")
        answer = queue.pop(0) if len(queue) > 1 else queue[0]
        assert isinstance(answer, spec)
        return Answered[spec](answers=answer, model=self.MODEL)
