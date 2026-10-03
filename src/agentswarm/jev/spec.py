"""Typed question specs: one Pydantic class declares both the questions and the answers.

Ported from ~/Projects/Jev/jev.py. A spec is a model whose fields are answer types annotated with the
question that produces them:

    class ChangeReadiness(BaseModel):
        complete: Annotated[NoulAnswer, Ask("Does the change address every part of the task?")]
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Self, cast

from pydantic import BaseModel, create_model

from agentswarm.jev.api import (
    ChoiceAnswer,
    ChoiceQuestion,
    NoulAnswer,
    NoulCriteria,
    NoulQuestion,
    ScoreAnswer,
    ScoreQuestion,
)


class Option(StrEnum):
    """A closed answer set whose members carry descriptions.

    Write each member as NAME = "key", "description".
    """

    description: str

    def __new__(cls, key: str, description: str) -> Self:
        member = str.__new__(cls, key)
        member._value_ = key
        member.description = description
        return member


@dataclass(frozen=True)
class Ask:
    """The question text, attached to an answer field with Annotated."""

    instructions: str
    levels: tuple[str, ...] = ()  # Score only: ordered, lowest first
    yes: str = ""  # Noul only, optional: what "true" means
    no: str = ""  # Noul only, optional: what "false" means


class QuestionSet:
    """Builds the questions object Jev expects from a spec's annotated answer fields."""

    @classmethod
    def of(cls, spec: type[BaseModel]) -> BaseModel:
        fields: dict[str, Any] = {}
        for name, field in spec.model_fields.items():
            ask = next((m for m in field.metadata if isinstance(m, Ask)), None)
            if ask is None:
                raise TypeError(f"{spec.__name__}.{name}: missing Ask(...) in Annotated[...]")
            question = cls.question(field.annotation, ask, f"{spec.__name__}.{name}")
            fields[name] = (type(question), question)
        return create_model(f"{spec.__name__}Questions", **fields)()

    @staticmethod
    def question(answer: type[Any] | None, ask: Ask, where: str) -> BaseModel:
        """The question that produces an answer of type `answer`."""
        if answer is NoulAnswer:
            criteria = NoulCriteria(true=ask.yes, false=ask.no) if ask.yes or ask.no else None
            return NoulQuestion(instructions=ask.instructions, criteria=criteria)
        if answer is ScoreAnswer:
            if not ask.levels:
                raise TypeError(f"{where}: a Score question needs levels=(...)")
            return ScoreQuestion(instructions=ask.instructions, criteria=list(ask.levels))
        if isinstance(answer, type) and issubclass(answer, ChoiceAnswer):
            options = QuestionSet.options_of(cast(type[ChoiceAnswer[Any]], answer), where)
            return ChoiceQuestion(
                instructions=ask.instructions, criteria={o.value: o.description for o in options}
            )
        raise TypeError(f"{where}: unsupported answer type {answer}")

    @staticmethod
    def options_of(answer: type[ChoiceAnswer[Any]], where: str) -> list[Option]:
        """The Option members of ChoiceAnswer[SomeOption] (pydantic substitutes SomeOption into the field)."""
        options = answer.model_fields["choice"].annotation
        if not (isinstance(options, type) and issubclass(options, Option)):
            raise TypeError(f"{where}: ChoiceAnswer needs an Option subclass, got {options}")
        return list(options)
