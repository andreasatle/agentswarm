from typing import Annotated

import pytest
from pydantic import BaseModel

from agentswarm.core.domain import FindingKind
from agentswarm.jev.api import NoulAnswer, ScoreAnswer
from agentswarm.jev.spec import Ask, QuestionSet
from agentswarm.judging.judges import ChangeReadiness, Fails, Judge, JudgeCatalog


class TestJudge:
    def test_raw_to_experimental_asks_the_three_questions_of_the_proposal(self) -> None:
        judge = Judge(ChangeReadiness)
        assert judge.fields() == ("in_scope", "tests_relevant", "complete")
        assert [q.fails.kind for q in judge.questions] == [
            FindingKind.OUT_OF_SCOPE,
            FindingKind.INCOMPLETE,
            FindingKind.INCOMPLETE,
        ]

    def test_the_spec_is_also_a_valid_jev_spec(self) -> None:
        questions = QuestionSet.of(ChangeReadiness).model_dump(mode="json", exclude_none=True)
        assert questions["complete"] == {
            "type": "noul",
            "instructions": "Does the change address every part of the task?",
        }

    def test_every_question_needs_ask_and_fails(self) -> None:
        class NoFails(BaseModel):
            done: Annotated[NoulAnswer, Ask("Done?")]

        with pytest.raises(TypeError, match="needs both Ask"):
            Judge(NoFails)

    def test_only_noul_questions_for_now(self) -> None:
        class Scored(BaseModel):
            quality: Annotated[
                ScoreAnswer,
                Ask("How good?", levels=("bad", "good")),
                Fails(FindingKind.INCOMPLETE, "Not good"),
            ]

        with pytest.raises(TypeError, match="Noul answers only"):
            Judge(Scored)

    def test_a_judge_must_ask_something(self) -> None:
        class Empty(BaseModel):
            pass

        with pytest.raises(TypeError, match="asks no questions"):
            Judge(Empty)


class TestJudgeCatalog:
    def test_standard_catalog_knows_raw_to_experimental(self) -> None:
        assert JudgeCatalog.standard().get("ChangeReadiness").spec is ChangeReadiness

    def test_the_phase_2_name_still_finds_the_judge(self) -> None:
        catalog = JudgeCatalog.standard()
        assert catalog.get("RawToExperimental") is catalog.get("ChangeReadiness")

    def test_unknown_and_duplicate_judges_are_errors(self) -> None:
        catalog = JudgeCatalog.standard()
        with pytest.raises(ValueError, match="unknown judge"):
            catalog.get("Nope")
        with pytest.raises(ValueError, match="already registered"):
            catalog.register(ChangeReadiness)
