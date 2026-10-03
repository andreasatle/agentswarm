"""Thresholds from data: the lowest pass and highest fail threshold that meet a target precision (§11.5).

For one decision point and question, with p the judge's p(yes) and the ground truth from the decision log:

- pass threshold: the lowest t such that the answers with p ≥ t are "yes" at least `precision` of the time;
- fail threshold: the highest t such that the answers with p ≤ t are "no" at least `precision` of the time;

each needing at least `min_decided` answers on its side, and then snapped to the nearest answer of its own
kind (the pass threshold up to the lowest "yes" at or above it, the fail threshold down to the highest "no"
at or below it): that never lowers precision or coverage of correct answers, and leaves the values nothing
was observed for to escalate. A question is ready to go active when both exist,
they don't overlap, and it has at least `min_support` answers in all. Every figure comes with the 95%
Wilson lower bound, because 50 answers prove much less than their precision suggests.
"""

import math
from collections.abc import Callable, Sequence
from typing import Self

from pydantic import BaseModel, ConfigDict

from agentswarm.judging.calibration import Labelled, LabelledAnswers
from agentswarm.judging.decisions import DecisionStore
from agentswarm.judging.judges import JudgeCatalog


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Wilson:
    Z = 1.96  # 95%

    @classmethod
    def lower(cls, successes: int, n: int) -> float:
        """The lower end of the 95% Wilson score interval for a success rate."""
        if n == 0:
            return 0.0
        p = successes / n
        centre = p + cls.Z**2 / (2 * n)
        spread = cls.Z * math.sqrt(p * (1 - p) / n + cls.Z**2 / (4 * n * n))
        return (centre - spread) / (1 + cls.Z**2 / n)

    @classmethod
    def upper(cls, successes: int, n: int) -> float:
        return 1 - cls.lower(n - successes, n)


class Side(Frozen):
    """One threshold and how well it does."""

    threshold: float
    decided: int  # answers on this side of the threshold
    correct: int
    coverage: float  # decided / all answers for the question

    @property
    def precision(self) -> float:
        return self.correct / self.decided

    @property
    def lower_bound(self) -> float:
        return Wilson.lower(self.correct, self.decided)


class QuestionTuning(Frozen):
    point: str
    judge: str
    judge_version: str
    current: bool  # whether the judge in the catalog still asks this wording
    field: str
    n: int
    passing: Side | None
    failing: Side | None
    ready: bool

    def render(self) -> str:
        wording = "current wording" if self.current else "OLD wording, not for tuning"
        version = self.judge_version or "unversioned"
        lines = [f"{self.point} · {self.field} ({self.judge} {version}, {wording}): n={self.n}"]
        for name, side in (("pass", self.passing), ("fail", self.failing)):
            if side is None:
                lines.append(f"  {name}: no threshold reaches the target precision")
            else:
                lines.append(
                    f"  {name} at {side.threshold:.2f}: decides {side.coverage:.0%}, "
                    f"right {side.precision:.0%} (≥ {side.lower_bound:.0%} at 95% confidence, "
                    f"{side.decided} answers)"
                )
        lines.append("  ready to go active" if self.ready else "  not ready")
        return "\n".join(lines)


class ThresholdTuner:
    def __init__(self, precision: float = 0.95, min_support: int = 20, min_decided: int = 5) -> None:
        self._precision = precision
        self._min_support = min_support
        self._min_decided = min_decided

    def tune(self, store: DecisionStore, judges: JudgeCatalog) -> list[QuestionTuning]:
        answers = LabelledAnswers.of(store)
        tunings: list[QuestionTuning] = []
        for (point, version, field), rows in answers.groups.items():
            judge = answers.judges[(point, version)]
            current = version == judges.get(judge).version
            failing = self.failing(rows)
            passing = self.passing(rows, above=failing.threshold if failing is not None else None)
            separable = passing is not None and failing is not None
            tunings.append(
                QuestionTuning(
                    point=point,
                    judge=judge,
                    judge_version=version,
                    current=current,
                    field=field,
                    n=len(rows),
                    passing=passing,
                    failing=failing,
                    ready=current and separable and len(rows) >= self._min_support,
                )
            )
        return tunings

    def passing(self, rows: Sequence[Labelled], above: float | None = None) -> Side | None:
        """`above`: the fail threshold, which the pass threshold must stay strictly above."""
        for t in sorted({r.p_yes for r in rows if above is None or r.p_yes > above}):
            side = [r for r in rows if r.p_yes >= t]
            correct = sum(r.truth for r in side)
            if len(side) >= self._min_decided and correct / len(side) >= self._precision:
                snapped = min(r.p_yes for r in side if r.truth)  # the lowest "yes" it passes
                return self._side(rows, snapped, lambda r, s=snapped: r.p_yes >= s, lambda r: r.truth)
        return None

    def failing(self, rows: Sequence[Labelled]) -> Side | None:
        for t in sorted({r.p_yes for r in rows}, reverse=True):
            side = [r for r in rows if r.p_yes <= t]
            correct = sum(not r.truth for r in side)
            if len(side) >= self._min_decided and correct / len(side) >= self._precision:
                snapped = max(r.p_yes for r in side if not r.truth)  # the highest "no" it fails
                return self._side(rows, snapped, lambda r, s=snapped: r.p_yes <= s, lambda r: not r.truth)
        return None

    @staticmethod
    def _side(
        rows: Sequence[Labelled],
        threshold: float,
        decides: Callable[[Labelled], bool],
        right: Callable[[Labelled], bool],
    ) -> Side:
        side = [r for r in rows if decides(r)]
        return Side(
            threshold=threshold,
            decided=len(side),
            correct=sum(right(r) for r in side),
            coverage=len(side) / len(rows),
        )


class TuningReport(Frozen):
    questions: tuple[QuestionTuning, ...]

    @classmethod
    def of(cls, tunings: Sequence[QuestionTuning]) -> Self:
        return cls(questions=tuple(tunings))

    def thresholds_yaml(self) -> str:
        """The `thresholds:` for each gate whose current-wording questions all have both thresholds."""
        blocks: list[str] = []
        for point in sorted({q.point for q in self.questions if q.point.startswith("gate:")}):
            current = [q for q in self.questions if q.point == point and q.current]
            if not current or any(q.passing is None or q.failing is None for q in current):
                continue
            entries = ", ".join(
                f"{q.field}: {{pass: {q.passing.threshold:.2f}, fail: {q.failing.threshold:.2f}}}"
                for q in current
                if q.passing is not None and q.failing is not None
            )
            blocks.append(f"# {point}\nthresholds: {{{entries}}}")
        return "\n".join(blocks) or "# no gate has enough data for thresholds yet"

    def render(self) -> str:
        return "\n\n".join([*(q.render() for q in self.questions), self.thresholds_yaml()])
