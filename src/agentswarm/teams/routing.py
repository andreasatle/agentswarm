"""Routing: which model tier an executor starts a task on (Phase 8).

Two parts that never depend on each other:

- **Jev scores the task.** One model-agnostic question (`TaskDifficulty`) at the `route:<team>` point;
  the difficulty is 1 - p(simple). It only has to order tasks, easy before hard.
- **Each model has a measured curve:** its success rate per difficulty band, fitted from runs where the
  outcome is known (see evaluation/curves.py), never from descriptions.

The router starts on the cheapest tier whose curve predicts success at or above the target at this
difficulty, else on the strongest. In shadow mode, or without curves, it only records the difficulty and
keeps the cheapest tier: that is how the data for the curves is collected. A rejection still steps up a
tier from wherever the route started.
"""

import math
from collections.abc import Sequence
from pathlib import Path
from typing import ClassVar, Self

from pydantic import BaseModel, ConfigDict, Field

from agentswarm.core.config import JevMode
from agentswarm.core.domain import ArtifactContext, DecisionId
from agentswarm.judging.deciders import DeciderError
from agentswarm.judging.judgement import Judgement


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Observation(Frozen):
    """One run with a known outcome: a model, the task's difficulty, and whether the model succeeded."""

    model_id: str
    difficulty: float = Field(ge=0.0, le=1.0)
    success: bool


class CurveBand(Frozen):
    low: float  # difficulty band [low, high); the last band includes 1.0
    high: float
    runs: int
    successes: int

    @property
    def predicted(self) -> float | None:
        """The success rate with one imagined success and one failure added (Laplace), so a band with few
        runs isn't taken at face value; None when the band has no runs at all."""
        if self.runs == 0:
            return None
        return (self.successes + 1) / (self.runs + 2)


class ModelCurve(Frozen):
    model_id: str
    bands: tuple[CurveBand, ...]

    def band_for(self, difficulty: float) -> CurveBand:
        count = len(self.bands)
        index = min(math.floor(difficulty * count), count - 1)
        return self.bands[index]

    def predicted(self, difficulty: float) -> float | None:
        band = self.band_for(difficulty)
        return band.predicted

    @property
    def overall(self) -> float:
        runs = sum(b.runs for b in self.bands)
        successes = sum(b.successes for b in self.bands)
        return successes / runs if runs else 0.0


class ModelCurves(Frozen):
    """Every model's curve, with the same difficulty bands."""

    bands: int = Field(ge=1)
    curves: dict[str, ModelCurve]  # by model id

    CROSSING_MARGIN: ClassVar[float] = 0.2  # a weaker model this much better in a band counts as a crossing
    CROSSING_MIN_RUNS: ClassVar[int] = 3  # both models need this many runs in the band

    @classmethod
    def fit(cls, observations: Sequence[Observation], bands: int = 3) -> Self:
        curves: dict[str, ModelCurve] = {}
        models = sorted({o.model_id for o in observations})
        for model_id in models:
            mine = [o for o in observations if o.model_id == model_id]
            fitted: list[CurveBand] = []
            for index in range(bands):
                low = index / bands
                high = (index + 1) / bands
                last = index == bands - 1
                inside = [o for o in mine if low <= o.difficulty < high or (last and o.difficulty == 1.0)]
                successes = sum(o.success for o in inside)
                band = CurveBand(low=low, high=high, runs=len(inside), successes=successes)
                fitted.append(band)
            curves[model_id] = ModelCurve(model_id=model_id, bands=tuple(fitted))
        return cls(bands=bands, curves=curves)

    def predicted(self, model_id: str, difficulty: float) -> float | None:
        curve = self.curves.get(model_id)
        if curve is None:
            return None
        return curve.predicted(difficulty)

    def crossings(self) -> list[str]:
        """Bands where a model that is weaker overall clearly beats a stronger one: then one difficulty
        score can't route correctly between them (condition 1 in the Phase 8 design)."""
        ranked = sorted(self.curves.values(), key=lambda c: c.overall, reverse=True)
        found: list[str] = []
        for i, stronger in enumerate(ranked):
            for weaker in ranked[i + 1 :]:
                for strong_band, weak_band in zip(stronger.bands, weaker.bands, strict=True):
                    enough = min(strong_band.runs, weak_band.runs) >= self.CROSSING_MIN_RUNS
                    if not enough:
                        continue
                    strong_rate = strong_band.successes / strong_band.runs
                    weak_rate = weak_band.successes / weak_band.runs
                    if weak_rate > strong_rate + self.CROSSING_MARGIN:
                        found.append(
                            f"{weaker.model_id} beats {stronger.model_id} at difficulty "
                            f"{weak_band.low:.2f}-{weak_band.high:.2f} ({weak_rate:.0%} vs {strong_rate:.0%})"
                        )
        return found

    @classmethod
    def load(cls, path: Path) -> Self:
        text = path.read_text()
        return cls.model_validate_json(text)

    def write(self, path: Path) -> None:
        text = self.model_dump_json(indent=2)
        path.write_text(text + "\n")

    def render(self) -> str:
        header_bands = "  ".join(
            f"{i / self.bands:.2f}-{(i + 1) / self.bands:.2f}" for i in range(self.bands)
        )
        lines = [f"{'model':<28} {header_bands}   overall"]
        for curve in self.curves.values():
            cells = "  ".join(f"{b.successes:>3}/{b.runs:<5}" if b.runs else f"{'-':>9}" for b in curve.bands)
            lines.append(f"{curve.model_id:<28} {cells}   {curve.overall:.0%}")
        crossings = self.crossings()
        lines += [f"! curves cross: {c}" for c in crossings] or [
            "no crossings: one difficulty score can route"
        ]
        return "\n".join(lines)


class RouteChoice(Frozen):
    tier: int  # the index of the starting tier, cheapest first
    difficulty: float | None
    decision: DecisionId | None
    acted: bool


class Router:
    """Chooses the starting tier for one team's tasks."""

    def __init__(
        self,
        judgement: Judgement,
        point: str,
        model_ids: Sequence[str],  # the team's tiers, cheapest first, as "<provider>:<model>"
        curves: ModelCurves | None,
        target: float,
    ) -> None:
        self._judgement = judgement
        self._point = point
        self._model_ids = tuple(model_ids)
        self._curves = curves
        self._target = target

    def model_id(self, tier: int) -> str:
        return self._model_ids[tier]

    def choose(self, ctx: ArtifactContext, head: str) -> RouteChoice:
        try:
            decision = self._judgement.ask(self._point, ctx, head, head, "")
        except DeciderError:
            return RouteChoice(tier=0, difficulty=None, decision=None, acted=False)
        simple = decision.p_yes("simple")
        difficulty = round(1.0 - simple, 4)
        active = decision.mode is JevMode.ACTIVE and self._curves is not None
        if not active:
            return RouteChoice(tier=0, difficulty=difficulty, decision=decision.id, acted=False)
        tier = self.tier_for(difficulty)
        return RouteChoice(tier=tier, difficulty=difficulty, decision=decision.id, acted=True)

    def tier_for(self, difficulty: float) -> int:
        """The cheapest tier predicted to succeed at least `target` of the time; else the strongest."""
        assert self._curves is not None
        for index, model_id in enumerate(self._model_ids):
            predicted = self._curves.predicted(model_id, difficulty)
            if predicted is not None and predicted >= self._target:
                return index
        return len(self._model_ids) - 1
