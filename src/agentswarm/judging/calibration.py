"""The calibration report: do the judge's probabilities match how often it is right? (§15.2)

Ported from ~/Projects/Jev/09_calibration.py: ten equal-width bins of p(yes), the fraction actually yes
in each, and the expected calibration error (ECE). Added for Phase 4's threshold tuning:

- one report per decision point and question, since thresholds are per question;
- the Brier score, the mean squared distance between p(yes) and the truth;
- coverage and accuracy at the policy's thresholds: how often the judge would decide on its own, and how
  often it would be right when it does;
- which outcome sources the truths come from, since the numbers are only as good as their ground truth.
"""

from collections import Counter
from collections.abc import Sequence
from typing import ClassVar, Self

from pydantic import BaseModel, ConfigDict

from agentswarm.core.config import Threshold
from agentswarm.judging.deciders import LLMDecider
from agentswarm.judging.decisions import DecisionStore, Outcomes, OutcomeSource
from agentswarm.judging.policy import Policy, QuestionVerdict


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Labelled(Frozen):
    """One question of one decision, with its ground truth."""

    point: str
    judge_version: str
    field: str
    p_yes: float
    truth: bool
    source: OutcomeSource


class Bin(Frozen):
    low: float
    high: float
    n: int
    mean_p: float
    fraction_yes: float


class QuestionCalibration(Frozen):
    point: str
    judge_version: str
    field: str
    n: int
    positives: int  # how many have truth yes
    bins: tuple[Bin, ...]
    ece: float
    brier: float
    threshold: Threshold
    coverage: float  # fraction the policy would decide (pass or fail) instead of escalating
    decided_accuracy: float | None  # among those, the fraction decided correctly; None if none decided
    sources: dict[OutcomeSource, int]

    BINS: ClassVar[int] = 10
    SMALL_N: ClassVar[int] = 200  # below this, the numbers say little (Jev tutorial, step 9)

    @classmethod
    def of(cls, rows: Sequence[Labelled], threshold: Threshold) -> Self:
        if not rows:
            raise ValueError("no labelled answers")
        n = len(rows)
        bins: list[Bin] = []
        ece = 0.0
        for b in range(cls.BINS):
            low, high = b / cls.BINS, (b + 1) / cls.BINS
            members = [r for r in rows if cls.bin_of(r.p_yes) == b]
            if not members:
                continue
            mean_p = sum(r.p_yes for r in members) / len(members)
            fraction = sum(r.truth for r in members) / len(members)
            bins.append(Bin(low=low, high=high, n=len(members), mean_p=mean_p, fraction_yes=fraction))
            ece += len(members) / n * abs(mean_p - fraction)
        rulings = [(Policy.rule_question(r.field, r.p_yes, threshold).verdict, r.truth) for r in rows]
        decided = [(verdict, truth) for verdict, truth in rulings if verdict is not QuestionVerdict.UNSURE]
        correct = sum((verdict is QuestionVerdict.PASS) == truth for verdict, truth in decided)
        return cls(
            point=rows[0].point,
            judge_version=rows[0].judge_version,
            field=rows[0].field,
            n=n,
            positives=sum(r.truth for r in rows),
            bins=tuple(bins),
            ece=ece,
            brier=sum((r.p_yes - r.truth) ** 2 for r in rows) / n,
            threshold=threshold,
            coverage=len(decided) / n,
            decided_accuracy=correct / len(decided) if decided else None,
            sources=dict(Counter(r.source for r in rows)),
        )

    @classmethod
    def bin_of(cls, p: float) -> int:
        return min(int(p * cls.BINS), cls.BINS - 1)  # p = 1.0 belongs in the top bin

    def render(self) -> str:
        sources = ", ".join(f"{source} {count}" for source, count in sorted(self.sources.items()))
        t = self.threshold
        decides = f"  at pass ≥ {t.pass_at} / fail ≤ {t.fail_at}: decides {self.coverage:.0%}"
        if self.decided_accuracy is not None:
            decides += f", right {self.decided_accuracy:.0%} of those"
        lines = [
            f"{self.point} · {self.field} (judge {self.judge_version or 'unversioned'}): n={self.n} "
            f"({self.positives} yes), truths from {sources}",
            "  bin        n  mean p(yes)  fraction yes",
            *(
                f"  {b.low:.1f}-{b.high:.1f}  {b.n:3d}  {b.mean_p:11.2f}  {b.fraction_yes:12.2f}"
                for b in self.bins
            ),
            f"  ECE {self.ece:.3f} · Brier {self.brier:.3f}",
            decides,
        ]
        if self.positives in (0, self.n):
            lines.append("  ! every truth is the same: calibration can't be judged from this")
        if self.n < self.SMALL_N:
            lines.append(f"  ! only {self.n} answers: too few for the numbers to mean much (aim for 200-500)")
        return "\n".join(lines)


class LabelledAnswers(Frozen):
    """Every calibrated answer that has a ground truth, grouped by decision point, judge version and question.

    Shared by the calibration report and the threshold tuner. LLM-fallback decisions are left out:
    uncalibrated answers would only blur the picture.
    """

    groups: dict[tuple[str, str, str], tuple[Labelled, ...]]  # (point, judge_version, field) -> answers
    unlabelled: int  # questions asked that have no outcome yet
    judges: dict[tuple[str, str], str]  # (point, judge_version) -> judge name

    @classmethod
    def of(cls, store: DecisionStore) -> Self:
        groups: dict[tuple[str, str, str], list[Labelled]] = {}
        judges: dict[tuple[str, str], str] = {}
        unlabelled = 0
        for decision in store.all():
            if LLMDecider.is_llm(decision.model):
                continue
            judges[(decision.point, decision.judge_version)] = decision.judge
            outcomes = store.outcomes(decision.id)
            for question in decision.ruling.questions:
                best = Outcomes.strongest(outcomes, question.field)
                if best is None:
                    unlabelled += 1
                    continue
                row = Labelled(
                    point=decision.point,
                    judge_version=decision.judge_version,
                    field=question.field,
                    p_yes=question.p_yes,
                    truth=best.truth,
                    source=best.source,
                )
                groups.setdefault((row.point, row.judge_version, row.field), []).append(row)
        return cls(
            groups={key: tuple(rows) for key, rows in sorted(groups.items())},
            unlabelled=unlabelled,
            judges=judges,
        )


class CalibrationReport(Frozen):
    questions: tuple[QuestionCalibration, ...]
    unlabelled: int  # questions asked that have no outcome yet

    @classmethod
    def of(cls, store: DecisionStore, threshold: Threshold = Policy.DEFAULT) -> Self:
        answers = LabelledAnswers.of(store)  # one wording of a judge is not evidence about another
        questions = tuple(QuestionCalibration.of(rows, threshold) for rows in answers.groups.values())
        return cls(questions=questions, unlabelled=answers.unlabelled)

    def render(self) -> str:
        if not self.questions:
            return f"No labelled answers yet ({self.unlabelled} without an outcome)."
        blocks = [q.render() for q in self.questions]
        blocks.append(f"{self.unlabelled} answers without an outcome are not included.")
        return "\n\n".join(blocks)
