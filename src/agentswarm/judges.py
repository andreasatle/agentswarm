"""Judges: the questions a gate asks Jev (proposal §11.3), and what a "no" means (§11.6, source 1).

A judge spec is a Jev spec whose fields are all Noul questions phrased so that **yes is good**, each
annotated with the finding a failure produces:

    in_scope: Annotated[NoulAnswer, Ask("Does the change stay within its task?"),
                        Fails(FindingKind.OUT_OF_SCOPE, "Changes things the task did not ask for")]
"""

from dataclasses import dataclass
from typing import Annotated, Self

from pydantic import BaseModel

from agentswarm.domain import FindingKind
from agentswarm.jev.api import NoulAnswer
from agentswarm.jev.spec import Ask


@dataclass(frozen=True)
class Fails:
    """What a clear "no" to the question means: the finding's kind and its fixed message."""

    kind: FindingKind
    message: str


@dataclass(frozen=True)
class JudgeQuestion:
    field: str
    ask: Ask
    fails: Fails


class Judge:
    """A validated judge spec: every field is a Noul answer with both Ask(...) and Fails(...)."""

    def __init__(self, spec: type[BaseModel]) -> None:
        self.spec = spec
        self.questions = tuple(self._question(name) for name in spec.model_fields)
        if not self.questions:
            raise TypeError(f"judge {spec.__name__} asks no questions")

    @property
    def name(self) -> str:
        return self.spec.__name__

    def fields(self) -> tuple[str, ...]:
        return tuple(q.field for q in self.questions)

    def _question(self, name: str) -> JudgeQuestion:
        field = self.spec.model_fields[name]
        where = f"{self.spec.__name__}.{name}"
        if field.annotation is not NoulAnswer:
            raise TypeError(f"{where}: judges use Noul answers only for now (see docs/BACKLOG.md)")
        ask = next((m for m in field.metadata if isinstance(m, Ask)), None)
        fails = next((m for m in field.metadata if isinstance(m, Fails)), None)
        if ask is None or fails is None:
            raise TypeError(f"{where}: needs both Ask(...) and Fails(...) in Annotated[...]")
        return JudgeQuestion(name, ask, fails)


class RawToExperimental(BaseModel):
    """The raw → experimental gate (§11.3)."""

    in_scope: Annotated[
        NoulAnswer,
        Ask("Does the change stay within its task, without unrequested modifications?"),
        Fails(FindingKind.OUT_OF_SCOPE, "The change modifies things the task did not ask for"),
    ]
    tests_relevant: Annotated[
        NoulAnswer,
        Ask("Do the new tests exercise the behaviour the task describes, rather than trivially passing?"),
        Fails(FindingKind.INCOMPLETE, "The tests do not exercise the behaviour the task describes"),
    ]
    complete: Annotated[
        NoulAnswer,
        Ask("Does the change address every part of the task?"),
        Fails(FindingKind.INCOMPLETE, "The change does not address every part of the task"),
    ]


class JudgeCatalog:
    """Judges by name, since project configurations refer to them as `judge: RawToExperimental`."""

    def __init__(self) -> None:
        self._judges: dict[str, Judge] = {}

    @classmethod
    def standard(cls) -> Self:
        catalog = cls()
        catalog.register(RawToExperimental)
        return catalog

    def register(self, spec: type[BaseModel]) -> Judge:
        judge = Judge(spec)
        if judge.name in self._judges:
            raise ValueError(f"judge {judge.name!r} is already registered")
        self._judges[judge.name] = judge
        return judge

    def get(self, name: str) -> Judge:
        judge = self._judges.get(name)
        if judge is None:
            raise ValueError(f"unknown judge {name!r}; known: {sorted(self._judges)}")
        return judge
