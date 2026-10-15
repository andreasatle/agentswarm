"""Routing: which model a call starts on, learned from outcomes as the system works (Phase 8).

Three parts that never depend on each other's internals:

- **Jev describes the task.** One question set (`CallProfile`) at the `route:profile` point, asked once per
  artifact and shared by every routed team (`Profiled`): how simple the task is, plus attributes that are
  only logged until data shows they matter. The difficulty is 1 - p(simple), and its third (`Bands`) is what
  routing uses.
- **The ledger remembers outcomes.** Every routed call's model, band and success go to one SQLite file
  shared by projects and probes, so they all teach the same router.
- **The router is plain code.** Per (kind of call, band, model) it counts successes and failures on top of a
  small starting guess (`Prior`), draws a plausible success rate from those counts (Thompson sampling), and
  ranks the models whose draw meets the target by expected cost per success: the model's cost per attempt
  (measured; from its price until measured) divided by its drawn success rate (FrugalGPT; cost-aware bandits,
  RESEARCH.md section 9). Uncertain models still get tried now and then; proven ones are used most of the
  time. After a failure the cascade asks again, among the models that haven't failed the step. The order
  of models in the config matters only when nothing is known.
- **Outcomes are settled by an observer** (`control/outcomes.py`), from events after the call: the team
  that made a call never grades it.

In shadow mode, or without a ledger, the router only records what Jev said and keeps the cheapest tier.
"""

import json
import random
import sqlite3
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field

from agentswarm.core.clock import Clock
from agentswarm.core.config import JevMode, Price
from agentswarm.core.domain import ArtifactContext, CallKind, Event, Profiled, Routed, TeamName, Usage
from agentswarm.judging.deciders import DeciderError
from agentswarm.judging.judgement import Judgement
from agentswarm.llm.workers import Worker


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


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
    usd: float | None = None  # what the attempt cost; None where it wasn't recorded (before 2026-10-10)


class RoutingLedger:
    """The shared record of routed calls: one SQLite file per machine (or ":memory:" in tests)."""

    def __init__(self, path: Path | str) -> None:
        if isinstance(path, Path):
            path.parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False: the outcome recorder runs with the controllers in a worker thread, one step
        # at a time, so the connection is never used by two threads at once.
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS calls (kind TEXT, band INTEGER, model_id TEXT, success INTEGER, "
            "attributes TEXT, source TEXT, at TEXT, usd REAL)"
        )
        columns = {row[1] for row in self._db.execute("PRAGMA table_info(calls)")}
        if "usd" not in columns:  # a ledger from before costs were recorded
            with self._db:
                self._db.execute("ALTER TABLE calls ADD COLUMN usd REAL")

    def record(self, call: RoutedCall) -> None:
        row = (
            call.kind.value,
            call.band,
            call.model_id,
            int(call.success),
            json.dumps(call.attributes, sort_keys=True),
            call.source,
            call.at.isoformat(),
            call.usd,
        )
        with self._db:
            self._db.execute("INSERT INTO calls VALUES (?, ?, ?, ?, ?, ?, ?, ?)", row)

    def calls(self, kind: CallKind | None = None) -> list[RoutedCall]:
        rows = self._db.execute(
            "SELECT kind, band, model_id, success, attributes, source, at, usd FROM calls"
        )
        found = [
            RoutedCall(
                kind=CallKind(k),
                band=b,
                model_id=m,
                success=bool(s),
                attributes=json.loads(a),
                source=src,
                at=datetime.fromisoformat(at),
                usd=usd,
            )
            for k, b, m, s, a, src, at, usd in rows
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
    # Partial pooling (hierarchical priors): in a band, a model starts from its record in the other bands,
    # worth this many imagined runs, instead of from the same guess as every other model. Without it a
    # band's few outcomes were all the router knew: gemma4:26b, 0 for 21 in other bands, was tried again in
    # the middle band ahead of GPT-6.1 Sol, 93 for 108 elsewhere (2026-10-15). 0: no pooling.
    pooling: float = Field(default=4.0, ge=0.0)

    def counts(self, band: int) -> tuple[float, float]:
        rate = self.rates[band]
        return rate * self.strength, (1.0 - rate) * self.strength


class Belief(Frozen):
    """What is known about one model on one kind of call in one band."""

    successes: float  # weighted: with a half-life, older outcomes count less
    failures: float
    prior: tuple[float, float]  # imagined successes and failures
    seen: int = 0  # outcomes recorded, unweighted

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
    """Every model's belief per (kind, band), folded from the ledger like a view.

    With a half-life, an outcome counts 0.5 ** (later / half-life), where `later` is how many calls of the
    same kind were recorded after it: discounted Thompson sampling, the standard remedy when what is learned
    changes (models, prompts and the framework itself evolve). Recent outcomes dominate, like an exponential
    moving average; a model left unused drifts back to the prior as others are used, so it gets tried again;
    and since age is counted in calls, not days, a pause in the work forgets nothing (the user's choice).
    """

    def __init__(
        self, calls: Sequence[RoutedCall], prior: Prior, half_life_calls: float | None = None
    ) -> None:
        self._prior = prior
        self._counts: dict[tuple[CallKind, int, str], list[float]] = {}
        self._seen: dict[tuple[CallKind, int, str], int] = {}
        self._costs: dict[tuple[CallKind, int, str], list[float]] = {}  # weighted dollars, weights
        later: dict[CallKind, int] = {}
        for call in reversed(calls):  # the ledger lists calls in the order they were recorded
            key = (call.kind, call.band, call.model_id)
            counts = self._counts.setdefault(key, [0.0, 0.0])
            weight = self.weight(later.get(call.kind, 0), half_life_calls)
            counts[0 if call.success else 1] += weight
            if call.usd is not None:
                costs = self._costs.setdefault(key, [0.0, 0.0])
                costs[0] += weight * call.usd
                costs[1] += weight
            self._seen[key] = self._seen.get(key, 0) + 1
            later[call.kind] = later.get(call.kind, 0) + 1

    @staticmethod
    def weight(later: int, half_life_calls: float | None) -> float:
        return 1.0 if half_life_calls is None else 0.5 ** (later / half_life_calls)

    def of(self, kind: CallKind, band: int, model_id: str) -> Belief:
        key = (kind, band, model_id)
        successes, failures = self._counts.get(key, [0.0, 0.0])
        return Belief(
            successes=successes,
            failures=failures,
            prior=self.prior(kind, band, model_id),
            seen=self._seen.get(key, 0),
        )

    def prior(self, kind: CallKind, band: int, model_id: str) -> tuple[float, float]:
        """The band's guess, moved toward the model's record in the other bands (partial pooling)."""
        if self._prior.pooling == 0.0:
            return self._prior.counts(band)
        elsewhere = [self._counts.get((kind, other, model_id), [0.0, 0.0]) for other in range(Bands.COUNT)]
        successes = sum(c[0] for o, c in enumerate(elsewhere) if o != band)
        failures = sum(c[1] for o, c in enumerate(elsewhere) if o != band)
        if successes + failures == 0.0:
            return self._prior.counts(band)
        guess_s, guess_f = self._prior.counts(band)
        rate = (successes + guess_s) / (successes + failures + guess_s + guess_f)
        return rate * self._prior.pooling, (1.0 - rate) * self._prior.pooling

    def cost(self, kind: CallKind, band: int, model_id: str, estimate: float) -> float:
        """Dollars per attempt: the measured mean, with the price-based estimate counting as one attempt."""
        spent, weight = self._costs.get((kind, band, model_id), [0.0, 0.0])
        return (estimate + spent) / (1.0 + weight)

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
                shown = f"{f'{belief.successes:.3g}':>5}/{f'{runs:.3g}':<5} {belief.rate:>4.0%}"  # weighted
                cells.append(shown if belief.seen else f"{'-':>16}")
            lines.append(f"{kind:<9} {model:<32} " + "  ".join(cells))
        return "\n".join(lines)


class RouteChoice(Frozen):
    tier: int  # the index of the starting tier, cheapest first
    profile: Profiled | None  # None when Jev couldn't answer
    fresh: bool  # whether Jev was asked now, so the profile still has to go on the board
    acted: bool


@dataclass(frozen=True)
class Tier:
    model: str  # the name in the project's `models`
    worker: Worker


class Router:
    """Chooses the starting tier for one team's calls, from the artifact's profile and the ledger.

    The profile is asked once per artifact, at one decision point, whichever team needs it first; the others
    read it from the artifact's history. Outcomes are recorded by `control.outcomes.OutcomeRecorder`, which
    settles them from later events: a team never grades its own calls.
    """

    POINT: ClassVar[str] = "route:profile"

    def __init__(
        self,
        judgement: Judgement,  # asks CallProfile
        kind: CallKind,
        model_ids: Sequence[str],  # the team's tiers, cheapest first, as "<provider>:<model>"
        ledger: RoutingLedger | None,
        target: float,
        clock: Clock,
        prior: Prior | None = None,
        source: str = "project",
        rng: random.Random | None = None,
        half_life_calls: float | None = None,  # older outcomes count less (discounted Thompson sampling)
        estimates: Sequence[float]
        | None = None,  # dollars per attempt from each model's price, until measured
        explore_usd: float | None = None,  # a model costing more per attempt starts a step only once measured
    ) -> None:
        self._half_life_calls = half_life_calls
        self._estimates = tuple(estimates) if estimates is not None else tuple(0.0 for _ in model_ids)
        self._explore_usd = explore_usd
        self._clock = clock
        self._judgement = judgement
        self._kind = kind
        self._model_ids = tuple(model_ids)
        self._ledger = ledger
        self._target = target
        self._prior = prior or Prior()
        self._source = source
        self._rng = rng or random.Random()

    @property
    def kind(self) -> CallKind:
        return self._kind

    @property
    def acts(self) -> bool:
        """Whether it has a ledger to rank by (without one it only records what Jev says)."""
        return self._ledger is not None

    def model_id(self, tier: int) -> str:
        return self._model_ids[tier]

    def profile(self, ctx: ArtifactContext, head: str) -> tuple[Profiled | None, bool]:
        """The artifact's profile, and whether it was asked just now."""
        known = [e for e in ctx.history if isinstance(e, Profiled)]
        if known:
            return known[0], False
        try:
            decision = self._judgement.ask(self.POINT, ctx, head, head, "")
        except DeciderError:
            return None, False
        attributes = {field: round(decision.p_yes(field), 4) for field in self._judgement.judge.fields()}
        profile = Profiled(
            artifact=ctx.artifact.id,
            difficulty=round(1.0 - attributes["simple"], 4),
            attributes=attributes,
            decision=decision.id,
            active=decision.mode is JevMode.ACTIVE,
        )
        return profile, True

    def choose(self, ctx: ArtifactContext, head: str) -> RouteChoice:
        profile, fresh = self.profile(ctx, head)
        active = profile is not None and profile.active and self._ledger is not None
        tier = self.rank(profile.difficulty)[0] if active and profile is not None else 0
        return RouteChoice(tier=tier, profile=profile, fresh=fresh, acted=active)

    # A typical builder attempt, from the boards of 2026-10-09 (GPT-6.1 Sol: 0.6-0.9M input tokens, over 90%
    # cached; DeepSeek: 2.8-4.5M): prices a model's attempts until its own costs are measured.
    TYPICAL_ATTEMPT: ClassVar[Usage] = Usage(
        input_tokens=1_000_000, cache_read_tokens=850_000, output_tokens=20_000
    )

    @classmethod
    def estimate(cls, price: Price | None) -> float:
        """Dollars per attempt from a model's price; 0 for an unpriced (local) model."""
        return price.usd_of(cls.TYPICAL_ATTEMPT) if price is not None else 0.0

    MEASURED: ClassVar[int] = 3  # outcomes after which a model's cost counts as known (`explore_usd`)

    def rank(self, difficulty: float, failed: Collection[int] = ()) -> list[int]:
        """The tiers to try, best first, leaving out those that failed: one Thompson draw per model, then the
        models whose draw meets the target by expected cost per success (cost per attempt / drawn rate), then
        the rest by drawn rate, the likeliest first (by cost, a free model with a poor record would always
        come first: gemma4:26b, 0 for 6 on 2026-10-10). A model dearer than `explore_usd` per attempt, not
        yet measured, starts nothing while another is left. Empty when every tier has failed."""
        assert self._ledger is not None
        beliefs = Beliefs(self._ledger.calls(self._kind), self._prior, self._half_life_calls)
        band = Bands.of(difficulty)
        scored: list[tuple[bool, bool, float, int]] = []
        for index, model_id in enumerate(self._model_ids):
            if index in failed:
                continue
            belief = beliefs.of(self._kind, band, model_id)
            rate = max(belief.draw(self._rng), 1e-6)
            cost = beliefs.cost(self._kind, band, model_id, self._estimates[index])
            unproven = (
                self._explore_usd is not None and belief.seen < self.MEASURED and cost > self._explore_usd
            )
            below = rate < self._target
            scored.append((unproven, below, -rate if below else cost / rate, index))
        return [index for *_, index in sorted(scored)]

    def record(
        self,
        model_id: str,
        profile: Profiled,
        success: bool,
        usd: float | None = None,
        kind: CallKind | None = None,  # another kind than the router's own, e.g. a builder's repair round
    ) -> bool:
        """One settled call's outcome, for the ledger. False when there is no ledger to teach."""
        if self._ledger is None:
            return False
        call = RoutedCall(
            kind=kind or self._kind,
            band=Bands.of(profile.difficulty),
            model_id=model_id,
            success=success,
            attributes=profile.attributes,
            source=self._source,
            at=self._clock.now(),
            usd=usd,
        )
        self._ledger.record(call)
        return True


class RoutedTiers:
    """A team's model tiers and, optionally, its router: which tier its calls on an artifact start on.

    The choice is made once per artifact and team, and recorded as a `Routed` event (with the artifact's
    `Profiled` event the first time anyone asks Jev about it).
    """

    def __init__(self, team: TeamName, tiers: Sequence[Tier], router: Router | None) -> None:
        if not tiers:
            raise ValueError("a team needs at least one model tier")
        self._team = team
        self._tiers = tuple(tiers)
        self._router = router

    @property
    def tiers(self) -> tuple[Tier, ...]:
        return self._tiers

    @property
    def router(self) -> Router | None:
        return self._router

    def routed(self, ctx: ArtifactContext) -> Routed | None:
        earlier = [e for e in ctx.history if isinstance(e, Routed) and e.team == self._team]
        return earlier[0] if earlier else None

    def next(self, ctx: ArtifactContext, failed: Collection[str]) -> int | None:
        """After failures, the tier to try next among those that haven't failed this step: by the router's
        ranking once the step is profiled and routing acts, else the config's order. None when all failed."""
        names = [t.model for t in self._tiers]
        left = [i for i, name in enumerate(names) if name not in failed]
        if not left:
            return None
        profile = next((e for e in ctx.history if isinstance(e, Profiled)), None)
        if self._router is not None and self._router.acts and profile is not None and profile.active:
            ranked = self._router.rank(profile.difficulty, [i for i in range(len(names)) if i not in left])
            return ranked[0] if ranked else None
        return left[0]

    def start(self, ctx: ArtifactContext, head: str) -> tuple[int, list[Event]]:
        """The starting tier's index, and the events recording the choice when it was made just now."""
        names = [t.model for t in self._tiers]
        earlier = self.routed(ctx)
        if earlier is not None:
            return (names.index(earlier.model) if earlier.model in names else 0), []
        if self._router is None:
            return 0, []
        choice = self._router.choose(ctx, head)
        profile = choice.profile
        routed = Routed(
            artifact=ctx.artifact.id,
            team=self._team,
            call_kind=self._router.kind,
            model=self._tiers[choice.tier].model,
            model_id=self._router.model_id(choice.tier),
            difficulty=profile.difficulty if profile is not None else None,
            decision=profile.decision if profile is not None else None,
            acted=choice.acted,
            attributes=profile.attributes if profile is not None else {},
        )
        fresh: list[Event] = [profile] if choice.fresh and profile is not None else []
        return choice.tier, [*fresh, routed]
