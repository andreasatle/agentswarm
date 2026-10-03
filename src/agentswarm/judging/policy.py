"""The policy: plain code that turns a judge's probabilities into a ruling (proposal §11.2, §11.5).

Per question, with p the probability of yes (yes is good):
    p ≥ pass  → passes          p ≤ fail → fails          otherwise → unsure
Then for the whole judge:
    any question fails → REJECT, one failure per failed question
    else any unsure    → ESCALATE
    else               → PROMOTE
"""

from collections.abc import Mapping
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from agentswarm.core.config import Threshold
from agentswarm.core.domain import FindingKind
from agentswarm.jev.api import NoulAnswer
from agentswarm.judging.judges import Judge


class Verdict(StrEnum):
    PROMOTE = "promote"
    REJECT = "reject"
    ESCALATE = "escalate"


class QuestionVerdict(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    UNSURE = "unsure"


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class QuestionRuling(Frozen):
    field: str
    p_yes: float
    threshold: Threshold
    verdict: QuestionVerdict


class Failure(Frozen):
    """A failed question, ready to become a Finding once the gate adds the artifact and level."""

    field: str
    kind: FindingKind
    detail: str


class Ruling(Frozen):
    verdict: Verdict
    questions: tuple[QuestionRuling, ...]
    failures: tuple[Failure, ...] = ()


class Policy:
    """Thresholds for one judge. Questions without their own threshold use the default."""

    # Conservative starting point from the Jev evaluation (§11.5): Noul was well calibrated.
    DEFAULT = Threshold(pass_at=0.9, fail_at=0.2)

    def __init__(self, judge: Judge, thresholds: Mapping[str, Threshold] | None = None) -> None:
        thresholds = dict(thresholds or {})
        unknown = set(thresholds) - set(judge.fields())
        if unknown:
            raise ValueError(f"thresholds for questions {judge.name} doesn't ask: {sorted(unknown)}")
        self._judge = judge
        self._thresholds = {field: thresholds.get(field, self.DEFAULT) for field in judge.fields()}

    def threshold(self, field: str) -> Threshold:
        return self._thresholds[field]

    def rule(self, answers: BaseModel) -> Ruling:
        """Rule on answers to this policy's judge. Pure: no I/O, same answers give the same ruling."""
        if not isinstance(answers, self._judge.spec):
            raise TypeError(f"expected answers to {self._judge.name}, got {type(answers).__name__}")
        rulings: list[QuestionRuling] = []
        failures: list[Failure] = []
        for question in self._judge.questions:
            answer = getattr(answers, question.field)
            if not isinstance(answer, NoulAnswer):  # Judge guarantees this; checked for the type checker
                raise TypeError(f"{self._judge.name}.{question.field} is not a Noul answer")
            ruling = self.rule_question(question.field, answer.noul, self._thresholds[question.field])
            rulings.append(ruling)
            if ruling.verdict is QuestionVerdict.FAIL and question.fails is not None:
                failures.append(
                    Failure(
                        field=question.field,
                        kind=question.fails.kind,
                        detail=f"{question.fails.message} (Jev: p(yes) = {answer.noul:.2f})",
                    )
                )
        return Ruling(verdict=self.combine(rulings), questions=tuple(rulings), failures=tuple(failures))

    @staticmethod
    def rule_question(field: str, p_yes: float, threshold: Threshold) -> QuestionRuling:
        if p_yes >= threshold.pass_at:
            verdict = QuestionVerdict.PASS
        elif p_yes <= threshold.fail_at:
            verdict = QuestionVerdict.FAIL
        else:
            verdict = QuestionVerdict.UNSURE
        return QuestionRuling(field=field, p_yes=p_yes, threshold=threshold, verdict=verdict)

    @staticmethod
    def combine(rulings: list[QuestionRuling]) -> Verdict:
        verdicts = {r.verdict for r in rulings}
        if QuestionVerdict.FAIL in verdicts:
            return Verdict.REJECT
        if QuestionVerdict.UNSURE in verdicts:
            return Verdict.ESCALATE
        return Verdict.PROMOTE
