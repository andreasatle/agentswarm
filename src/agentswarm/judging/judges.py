"""Judges: the questions a gate asks Jev (proposal §11.3), and what a "no" means (§11.6, source 1).

A judge spec is a Jev spec whose fields are all Noul questions phrased so that **yes is good**, each
annotated with the finding a failure produces:

    in_scope: Annotated[NoulAnswer, Ask("Does the change stay within its task?"),
                        Fails(FindingKind.OUT_OF_SCOPE, "Changes things the task did not ask for")]
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Annotated, ClassVar, Self

from pydantic import BaseModel, ConfigDict

from agentswarm.core.domain import ArtifactContext, FindingKind
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
    fails: Fails | None  # what a clear "no" means as a finding; gates need it, relevance filters don't


class Judge:
    """A validated judge spec: every field is a Noul answer with Ask(...), and optionally Fails(...)."""

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

    def can_reject(self) -> bool:
        """Whether every question says what its failure means, as a gate's judge must."""
        return all(q.fails is not None for q in self.questions)

    def _question(self, name: str) -> JudgeQuestion:
        field = self.spec.model_fields[name]
        where = f"{self.spec.__name__}.{name}"
        if field.annotation is not NoulAnswer:
            raise TypeError(f"{where}: judges use Noul answers only for now (see docs/BACKLOG.md)")
        ask = next((m for m in field.metadata if isinstance(m, Ask)), None)
        fails = next((m for m in field.metadata if isinstance(m, Fails)), None)
        if ask is None:
            raise TypeError(f"{where}: needs Ask(...) in Annotated[...]")
        return JudgeQuestion(name, ask, fails)


class ChangeReadiness(BaseModel):
    """Is this change ready to join the shared level: in scope, tested, and complete? (§11.3)

    Named for what it asks, not for a pair of levels, so any project can use it at its first gate.
    """

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


class IntegrationRelevance(BaseModel):
    """Is this change worth integration testing? (§12.1, team relevance filter)"""

    relevant: Annotated[
        NoulAnswer,
        Ask(
            "Does this change alter behaviour that other parts of the code rely on, so that testing how the "
            "parts work together could reveal a problem?"
        ),
    ]


class AdversarialRelevance(BaseModel):
    """Is this change worth attacking? (§12.1, team relevance filter)"""

    relevant: Annotated[
        NoulAnswer,
        Ask(
            "Does this change handle input, state or resources that could be misused, or fail on unusual "
            "values?"
        ),
    ]


class SizeCheck(BaseModel):
    """Can one team do this in one branch? (§10.4)"""

    fits: Annotated[
        NoulAnswer,
        Ask("Can this task be done in one branch by one team, without further design decisions?"),
    ]


class SplitCheck(BaseModel):
    """Is a proposed split real progress? (§10.4, progress guard)"""

    smaller: Annotated[
        NoulAnswer,
        Ask("Is each proposed child task clearly smaller than the parent task, rather than restating it?"),
    ]


class JoinCheck(BaseModel):
    """Do the parts add up to the whole? (§11.4)"""

    accomplished: Annotated[
        NoulAnswer,
        Ask("Together, do these changes accomplish the parent task?"),
        Fails(FindingKind.INCOMPLETE, "The parts don't add up to the whole task"),
    ]


class DriftCheck(BaseModel):
    """Is recent work heading toward the north star? (§14.1, drift)"""

    on_course: Annotated[
        NoulAnswer,
        Ask("Does this recent work advance the north star, rather than serving other goals?"),
    ]


class JudgeState(BaseModel):
    """What a judge is shown about an artifact (§8.3): small, typed, and the diff instead of file names."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    DIFF_LIMIT: ClassVar[int] = 60_000  # characters; Jev's window is 32k tokens for state and questions

    north_star: str
    task: str
    must_fix: tuple[str, ...]  # open findings the change was supposed to address
    diff: str  # unified diff against the level the change goes into, truncated to DIFF_LIMIT
    proposal: tuple[str, ...] = ()  # child tasks proposed by decomposition, for the split check

    @classmethod
    def of(cls, ctx: ArtifactContext, diff: str, proposal: Sequence[str] = ()) -> Self:
        return cls(
            north_star=ctx.north_star,
            task=ctx.artifact.task,
            must_fix=tuple(f.detail for f in ctx.open_findings),
            diff=cls.truncate(diff),
            proposal=tuple(proposal),
        )

    @classmethod
    def truncate(cls, diff: str) -> str:
        if len(diff) <= cls.DIFF_LIMIT:
            return diff
        return diff[: cls.DIFF_LIMIT] + f"\n[diff truncated: {len(diff) - cls.DIFF_LIMIT} more characters]"


class JudgeCatalog:
    """Judges by name, since project configurations refer to them as `judge: ChangeReadiness`."""

    def __init__(self) -> None:
        self._judges: dict[str, Judge] = {}

    @classmethod
    def standard(cls) -> Self:
        catalog = cls()
        catalog.register(ChangeReadiness)
        catalog.register(IntegrationRelevance)
        catalog.register(AdversarialRelevance)
        catalog.register(SizeCheck)
        catalog.register(SplitCheck)
        catalog.register(JoinCheck)
        catalog.register(DriftCheck)
        catalog.alias(
            "RawToExperimental", "ChangeReadiness"
        )  # its Phase 2 name, still in older decision logs
        return catalog

    def register(self, spec: type[BaseModel]) -> Judge:
        judge = Judge(spec)
        if judge.name in self._judges:
            raise ValueError(f"judge {judge.name!r} is already registered")
        self._judges[judge.name] = judge
        return judge

    def alias(self, old: str, name: str) -> None:
        """Make `old` another name for an already registered judge."""
        if old in self._judges:
            raise ValueError(f"judge {old!r} is already registered")
        self._judges[old] = self.get(name)

    def get(self, name: str) -> Judge:
        judge = self._judges.get(name)
        if judge is None:
            raise ValueError(f"unknown judge {name!r}; known: {sorted(self._judges)}")
        return judge
