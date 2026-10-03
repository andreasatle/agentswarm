"""Decision drafts for tests of the decision log and the reports built on it."""

from agentswarm.core.config import JevMode
from agentswarm.judging.decisions import Decision, DecisionDraft, Outcome, OutcomeSource
from agentswarm.judging.judges import Judge, JudgeState, RawToExperimental
from agentswarm.judging.policy import Policy
from tests.support.samples import Judged, Samples


class Drafts:
    JUDGE = Judge(RawToExperimental)

    @classmethod
    def make(cls, answers: RawToExperimental | None = None, diff: str = "+x") -> DecisionDraft:
        answers = answers or Judged.passing()
        return DecisionDraft(
            point="gate:raw->experimental",
            artifact=Samples.A,
            commit="c0ffee",
            base="b45e",
            mode=JevMode.SHADOW,
            judge=cls.JUDGE.name,
            state=JudgeState(north_star="n", task="t", must_fix=(), diff=diff),
            answers=answers.model_dump(mode="json"),
            ruling=Policy(cls.JUDGE).rule(answers),
            model="fake",
            at=Samples.AT,
        )

    @staticmethod
    def outcome(
        decision: Decision,
        field: str = "complete",
        truth: bool = True,
        source: OutcomeSource = OutcomeSource.HIDDEN_TESTS,
    ) -> Outcome:
        return Outcome(decision=decision.id, field=field, truth=truth, source=source, at=Samples.AT)
