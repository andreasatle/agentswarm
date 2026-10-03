import pytest
from pydantic import ValidationError

from agentswarm.config import Threshold
from agentswarm.domain import FindingKind
from agentswarm.jev.api import NoulAnswer
from agentswarm.judges import Judge, RawToExperimental
from agentswarm.policy import Policy, QuestionVerdict, Verdict


class Answers:
    @staticmethod
    def of(in_scope: float, tests_relevant: float, complete: float) -> RawToExperimental:
        return RawToExperimental(
            in_scope=NoulAnswer(noul=in_scope),
            tests_relevant=NoulAnswer(noul=tests_relevant),
            complete=NoulAnswer(noul=complete),
        )


JUDGE = Judge(RawToExperimental)


class TestPolicy:
    def test_all_clear_passes_promote(self) -> None:
        ruling = Policy(JUDGE).rule(Answers.of(0.95, 0.97, 0.99))
        assert ruling.verdict is Verdict.PROMOTE
        assert ruling.failures == ()
        assert [q.verdict for q in ruling.questions] == [QuestionVerdict.PASS] * 3

    def test_any_clear_failure_rejects_with_one_failure_per_failed_question(self) -> None:
        ruling = Policy(JUDGE).rule(Answers.of(0.10, 0.50, 0.05))
        assert ruling.verdict is Verdict.REJECT
        assert [(f.field, f.kind) for f in ruling.failures] == [
            ("in_scope", FindingKind.OUT_OF_SCOPE),
            ("complete", FindingKind.INCOMPLETE),
        ]
        assert (
            ruling.failures[1].detail
            == "The change does not address every part of the task (Jev: p(yes) = 0.05)"
        )

    def test_unsure_without_failures_escalates(self) -> None:
        ruling = Policy(JUDGE).rule(Answers.of(0.95, 0.60, 0.95))
        assert ruling.verdict is Verdict.ESCALATE
        assert ruling.questions[1].verdict is QuestionVerdict.UNSURE

    @pytest.mark.parametrize(
        ("p", "expected"),
        [(0.9, QuestionVerdict.PASS), (0.8999, QuestionVerdict.UNSURE), (0.2, QuestionVerdict.FAIL)],
        ids=["at-pass", "just-below-pass", "at-fail"],
    )
    def test_thresholds_are_inclusive(self, p: float, expected: QuestionVerdict) -> None:
        assert Policy.rule_question("x", p, Policy.DEFAULT).verdict is expected

    def test_per_question_thresholds_override_the_default(self) -> None:
        policy = Policy(JUDGE, {"tests_relevant": Threshold(pass_at=0.6, fail_at=0.1)})
        assert policy.threshold("tests_relevant").pass_at == 0.6
        assert policy.threshold("complete") == Policy.DEFAULT
        assert policy.rule(Answers.of(0.95, 0.65, 0.95)).verdict is Verdict.PROMOTE

    def test_thresholds_for_unknown_questions_are_errors(self) -> None:
        with pytest.raises(ValueError, match="doesn't ask"):
            Policy(JUDGE, {"in_scpoe": Threshold(pass_at=0.9, fail_at=0.2)})

    def test_answers_to_another_judge_are_errors(self) -> None:
        with pytest.raises(TypeError, match="expected answers to RawToExperimental"):
            Policy(JUDGE).rule(NoulAnswer(noul=0.5))


class TestThreshold:
    def test_fail_must_be_below_pass(self) -> None:
        with pytest.raises(ValidationError, match="must be below"):
            Threshold(pass_at=0.5, fail_at=0.5)

    def test_reads_pass_and_fail_from_config(self) -> None:
        assert Threshold.model_validate({"pass": 0.8, "fail": 0.3}) == Threshold(pass_at=0.8, fail_at=0.3)
