"""Deciders answer a judge's questions about a state (proposal §12.4).

`JevDecider` is the calibrated one. `LLMDecider` asks a language model instead: slower and uncalibrated,
so its decisions are marked (`model: "llm:<name>"`) and left out of calibration and tuning.
`FallbackDecider` uses the second when the first can't answer. `FakeDecider` is for tests.
"""

import asyncio
import logging
from collections.abc import Coroutine, Sequence
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Any, ClassVar, Literal, Protocol

from pydantic import BaseModel, ConfigDict

from agentswarm.core.domain import Usage
from agentswarm.jev.api import NoulAnswer
from agentswarm.jev.client import JevClient, JevError
from agentswarm.jev.spec import Ask
from agentswarm.llm.workers import Worker, WorkerError

if TYPE_CHECKING:
    from agentswarm.control.scheduler import RateLimiter


class DeciderError(Exception):
    """No answers: the decider was unreachable or answered in the wrong shape."""


class Answered[M: BaseModel](BaseModel):
    model_config = ConfigDict(frozen=True)

    answers: M
    model: str  # the concrete version that answered, e.g. "jev-1.13.0"
    usage: Usage | None = None  # tokens, when the decider reports them (Jev does)


class Decider(Protocol):
    def decide[M: BaseModel](self, state: BaseModel, spec: type[M]) -> Answered[M]: ...


class JevDecider:
    def __init__(self, client: JevClient, limiter: "RateLimiter | None" = None) -> None:
        self._client = client
        self._limiter = limiter  # the scheduler's shared Jev rate limit, when there is one

    def decide[M: BaseModel](self, state: BaseModel, spec: type[M]) -> Answered[M]:
        if self._limiter is not None:
            self._limiter.acquire()
        try:
            response = self._client.decide(state, spec)
        except JevError as error:
            raise DeciderError(str(error)) from error
        usage = Usage(input_tokens=response.usage.input_tokens, output_tokens=response.usage.output_tokens)
        return Answered[spec](answers=response.answers, model=response.model, usage=usage)


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


class QuestionAnswer(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    question: str  # the field name, as given
    answer: Literal["yes", "no", "unsure"]


class LLMAnswers(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    answers: tuple[QuestionAnswer, ...]


class LLMQuestions(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    questions: dict[str, str]  # field name -> the question
    state: dict[str, Any]  # what to judge


class LLMDecider:
    """A language model answering Noul questions as yes / no / unsure (proposal §12.4, the fallback)."""

    PREFIX = "llm:"
    P_YES: ClassVar[dict[str, float]] = {"yes": 0.95, "no": 0.05, "unsure": 0.5}
    INSTRUCTIONS = """\
Answer each question in `questions` about `state` with yes, no or unsure. Answer unsure unless the state
clearly supports yes or no. Give one answer per question, using its key as `question`."""

    def __init__(self, worker: Worker, model: str) -> None:
        self._worker = worker
        self.model = f"{self.PREFIX}{model}"

    @classmethod
    def is_llm(cls, model: str) -> bool:
        """Whether a decision was made by an LLM fallback rather than a calibrated decider."""
        return model.startswith(cls.PREFIX)

    def decide[M: BaseModel](self, state: BaseModel, spec: type[M]) -> Answered[M]:
        questions: dict[str, str] = {}
        for name, field in spec.model_fields.items():
            ask = next((m for m in field.metadata if isinstance(m, Ask)), None)
            if field.annotation is not NoulAnswer or ask is None:
                raise DeciderError(
                    f"{self.model} answers Noul questions only; {spec.__name__}.{name} isn't one"
                )
            questions[name] = ask.instructions
        payload = LLMQuestions(questions=questions, state=state.model_dump(mode="json"))
        try:
            reply = self._in_thread(self._worker.run(self.INSTRUCTIONS, payload, LLMAnswers))
        except WorkerError as error:
            raise DeciderError(f"{self.model}: {error}") from error
        given = {a.question: a.answer for a in reply.answers}
        missing = set(questions) - set(given)
        if missing:
            raise DeciderError(f"{self.model} left questions unanswered: {sorted(missing)}")
        answers = spec.model_validate({name: {"noul": self.P_YES[given[name]]} for name in questions})
        return Answered[spec](answers=answers, model=self.model)

    @staticmethod
    def _in_thread[T](work: Coroutine[Any, Any, T]) -> T:
        """Gates are synchronous but run inside the project's event loop: run the worker on its own loop."""
        with ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, work).result()


class FallbackDecider:
    """Asks the primary decider; if it can't answer, asks the fallback and logs that it did."""

    _log = logging.getLogger(__name__)

    def __init__(self, primary: Decider, fallback: Decider) -> None:
        self._primary = primary
        self._fallback = fallback

    def decide[M: BaseModel](self, state: BaseModel, spec: type[M]) -> Answered[M]:
        try:
            return self._primary.decide(state, spec)
        except DeciderError as error:
            self._log.warning("primary decider failed for %s, using the fallback: %s", spec.__name__, error)
            return self._fallback.decide(state, spec)
