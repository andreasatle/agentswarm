"""Routing: which model a call starts on, learned from outcomes as the system works (Phase 8).

Three parts that never depend on each other's internals:

- **Jev describes the task.** One question set (`CallProfile`) at the `route:<team>` point, asked once per
  artifact: how simple the task is, plus attributes that are only logged until data shows they matter. The
  difficulty is 1 - p(simple), and its third (`Bands`) is what routing uses.
- **The ledger remembers outcomes.** Every routed call's model, band and success go to one SQLite file
  shared by projects and probes, so they all teach the same router.
- **The router is plain code.** Per (kind of call, band, model) it counts successes and failures on top of a
  small starting guess (`Prior`), draws a plausible success rate from those counts (Thompson sampling), and
  starts on the cheapest model whose draw meets the target. Uncertain models still get tried now and then;
  proven ones are used most of the time. A rejection steps up a tier from there, as before.

In shadow mode, or without a ledger, the router only records what Jev said and keeps the cheapest tier.
"""

import json
import random
import sqlite3
from collections.abc import Sequence
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field

from agentswarm.core.clock import Clock
from agentswarm.core.config import JevMode
from agentswarm.core.domain import ArtifactContext, DecisionId
from agentswarm.judging.deciders import DeciderError
from agentswarm.judging.judgement import Judgement


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class CallKind(StrEnum):
    """What a call does, whichever team makes it: what a model's record is kept per."""

    PRODUCE = "produce"  # executors, writers, produce probes
    CRITIQUE = "critique"  # analyzers, critique probes
    PLAN = "plan"  # decomposers, outliners


class Bands:
    """Difficulty in thirds: easy below 1/3, medium below 2/3, hard from there to 1."""

    COUNT: ClassVar[int] = 3
    NAMES: ClassVar[tuple[str, ...]] = ("easy", "medium", "hard")

    @classmethod
    def of(cls, difficulty: float) -> int:
        return min(int(difficulty * cls.COUNT), cls.COUNT - 1)


class RoutedCall(Frozen):
    """One call with a known outcome."""

    kind: CallKind
    band: int = Field(ge=0, lt=Bands.COUNT)
    model_id: str  # "<provider>:<model>"
    success: bool
    attributes: dict[str, float] = Field(default_factory=dict[str, float])  # Jev's p(yes) per question
    source: str  # e.g. "project:greeter" or "probe"
    at: datetime


class RoutingLedger:
    """The shared record of routed calls: one SQLite file per machine (or ":memory:" in tests)."""

    def __init__(self, path: Path | str) -> None:
        if isinstance(path, Path):
            path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path))
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS calls (kind TEXT, band INTEGER, model_id TEXT, success INTEGER, "
            "attributes TEXT, source TEXT, at TEXT)"
        )

    def record(self, call: RoutedCall) -> None:
        row = (
            call.kind.value,
            call.band,
            call.model_id,
            int(call.success),
            json.dumps(call.attributes, sort_keys=True),
            call.source,
            call.at.isoformat(),
        )
        with self._db:
            self._db.execute("INSERT INTO calls VALUES (?, ?, ?, ?, ?, ?, ?)", row)

    def calls(self, kind: CallKind | None = None) -> list[RoutedCall]:
        rows = self._db.execute("SELECT kind, band, model_id, success, attributes, source, at FROM calls")
        found = [
            RoutedCall(
                kind=CallKind(k),
                band=b,
                model_id=m,
                success=bool(s),
                attributes=json.loads(a),
                source=src,
                at=datetime.fromisoformat(at),
            )
            for k, b, m, s, a, src, at in rows
        ]
        return [c for c in found if kind is None or c.kind is kind]

    def close(self) -> None:
        self._db.close()


class Prior(Frozen):
    """The starting guess, the same for every model: a success rate per band, worth `strength` imagined runs.

    Small on purpose, so a few real outcomes outweigh it; never 0 or 1, so no model is ruled out before it
    has been tried.
    """

    rates: tuple[float, float, float] = (0.8, 0.6, 0.4)  # easy, medium, hard
    strength: float = Field(default=2.0, gt=0.0)

    def counts(self, band: int) -> tuple[float, float]:
        rate = self.rates[band]
        return rate * self.strength, (1.0 - rate) * self.strength


class Belief(Frozen):
    """What is known about one model on one kind of call in one band."""

    successes: int
    failures: int
    prior: tuple[float, float]  # imagined successes and failures

    @property
    def rate(self) -> float:
        a, b = self.alpha_beta
        return a / (a + b)

    @property
    def alpha_beta(self) -> tuple[float, float]:
        return self.successes + self.prior[0], self.failures + self.prior[1]

    def draw(self, rng: random.Random) -> float:
        """A plausible success rate: near `rate`, spread widely when there are few outcomes."""
        a, b = self.alpha_beta
        return rng.betavariate(a, b)


class Beliefs:
    """Every model's belief per (kind, band), folded from the ledger like a view."""

    def __init__(self, calls: Sequence[RoutedCall], prior: Prior) -> None:
        self._prior = prior
        self._counts: dict[tuple[CallKind, int, str], list[int]] = {}
        for call in calls:
            counts = self._counts.setdefault((call.kind, call.band, call.model_id), [0, 0])
            counts[0 if call.success else 1] += 1

    def of(self, kind: CallKind, band: int, model_id: str) -> Belief:
        successes, failures = self._counts.get((kind, band, model_id), [0, 0])
        return Belief(successes=successes, failures=failures, prior=self._prior.counts(band))

    def render(self) -> str:
        """The table: per kind and model, successes/runs and the believed rate in each band."""
        models = sorted({(kind, model) for kind, _, model in self._counts})
        header = f"{'kind':<9} {'model':<32} " + "  ".join(f"{name:>14}" for name in Bands.NAMES)
        lines = [header]
        for kind, model in models:
            cells: list[str] = []
            for band in range(Bands.COUNT):
                belief = self.of(kind, band, model)
                runs = belief.successes + belief.failures
                cells.append(f"{belief.successes:>3}/{runs:<3} {belief.rate:>5.0%}" if runs else f"{'-':>14}")
            lines.append(f"{kind:<9} {model:<32} " + "  ".join(cells))
        return "\n".join(lines)


class RouteChoice(Frozen):
    tier: int  # the index of the starting tier, cheapest first
    difficulty: float | None
    attributes: dict[str, float]
    decision: DecisionId | None
    acted: bool


class Router:
    """Chooses the starting tier for one team's tasks, and records how each attempt went."""

    def __init__(
        self,
        judgement: Judgement,  # asks CallProfile
        point: str,
        kind: CallKind,
        model_ids: Sequence[str],  # the team's tiers, cheapest first, as "<provider>:<model>"
        ledger: RoutingLedger | None,
        target: float,
        clock: Clock,
        prior: Prior | None = None,
        source: str = "project",
        rng: random.Random | None = None,
    ) -> None:
        self._clock = clock
        self._judgement = judgement
        self._point = point
        self._kind = kind
        self._model_ids = tuple(model_ids)
        self._ledger = ledger
        self._target = target
        self._prior = prior or Prior()
        self._source = source
        self._rng = rng or random.Random()

    def model_id(self, tier: int) -> str:
        return self._model_ids[tier]

    def choose(self, ctx: ArtifactContext, head: str) -> RouteChoice:
        try:
            decision = self._judgement.ask(self._point, ctx, head, head, "")
        except DeciderError:
            return RouteChoice(tier=0, difficulty=None, attributes={}, decision=None, acted=False)
        attributes = {field: round(decision.p_yes(field), 4) for field in self._judgement.judge.fields()}
        difficulty = round(1.0 - attributes["simple"], 4)
        active = decision.mode is JevMode.ACTIVE and self._ledger is not None
        tier = self.tier_for(difficulty) if active else 0
        return RouteChoice(
            tier=tier, difficulty=difficulty, attributes=attributes, decision=decision.id, acted=active
        )

    def tier_for(self, difficulty: float) -> int:
        """The cheapest tier whose drawn success rate meets the target; else the strongest."""
        assert self._ledger is not None
        beliefs = Beliefs(self._ledger.calls(self._kind), self._prior)
        band = Bands.of(difficulty)
        for index, model_id in enumerate(self._model_ids):
            if beliefs.of(self._kind, band, model_id).draw(self._rng) >= self._target:
                return index
        return len(self._model_ids) - 1

    def record(self, tier: int, difficulty: float, attributes: dict[str, float], success: bool) -> None:
        """One attempt's outcome, for the ledger (nothing without one)."""
        if self._ledger is None:
            return
        call = RoutedCall(
            kind=self._kind,
            band=Bands.of(difficulty),
            model_id=self.model_id(tier),
            success=success,
            attributes=attributes,
            source=self._source,
            at=self._clock.now(),
        )
        self._ledger.record(call)
