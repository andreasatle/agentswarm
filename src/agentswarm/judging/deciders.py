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
    """Returns scripted answers in order, then repeats the last one. Records every call."""

    MODEL = "fake"

    def __init__(self, answers: Sequence[BaseModel] = ()) -> None:
        self._answers = list(answers)
        self.calls: list[tuple[BaseModel, type[BaseModel]]] = []

    def decide[M: BaseModel](self, state: BaseModel, spec: type[M]) -> Answered[M]:
        self.calls.append((state, spec))
        if not self._answers:
            raise DeciderError("FakeDecider has no scripted answers")
        answers = self._answers.pop(0) if len(self._answers) > 1 else self._answers[0]
        if not isinstance(answers, spec):
            raise DeciderError(f"FakeDecider holds {type(answers).__name__}, asked for {spec.__name__}")
        return Answered[spec](answers=answers, model=self.MODEL)
