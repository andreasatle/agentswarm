"""Gate audit: how often an active gate was wrong, measured against outcomes (§19, Phase 4 exit criterion).

For each active gate decision, the verdict is compared with the ground truths of its questions (all
phrased so yes is good):

- **false promotion:** promoted, but some question's truth is "no" (one known "no" is enough);
- **false rejection:** rejected, but every question's truth is "yes" (needs them all);
- **escalation:** neither; a person decided.

Decisions whose truths don't settle it are counted as unknown, not guessed. Each rate comes with its 95%
Wilson upper bound, which is what is compared with the bound: with 60 decisions, a 0% observed rate
still means "up to about 6%".
"""

from typing import ClassVar, Self

from pydantic import BaseModel, ConfigDict

from agentswarm.core.config import JevMode
from agentswarm.judging.decisions import Decision, DecisionStore, Outcomes
from agentswarm.judging.policy import Verdict
from agentswarm.judging.tuning import Wilson


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Rate(Frozen):
    name: str
    errors: int
    of: int
    bound: float  # the agreed maximum (Phase 4 design)

    @property
    def observed(self) -> float:
        return self.errors / self.of if self.of else 0.0

    @property
    def upper(self) -> float:
        return Wilson.upper(self.errors, self.of) if self.of else 1.0

    @property
    def within(self) -> bool:
        return self.of > 0 and self.upper <= self.bound

    def render(self) -> str:
        if not self.of:
            return f"{self.name}: no labelled decisions"
        verdict = "within" if self.within else "NOT within"
        return (
            f"{self.name}: {self.errors}/{self.of} = {self.observed:.0%} "
            f"(95% upper bound {self.upper:.0%}; bound {self.bound:.0%}: {verdict})"
        )


class GateAudit(Frozen):
    """How often a gate's active rulings were wrong, for one cohort (CODEX_AUDIT.md F10).

    A cohort is one judge wording and one concrete model; `None` means pooled over all of them, which
    `cohorts` never does. This audits the judge's rulings, not the promotions that followed: a ruling to
    promote may still fail the merge checks or wait for a person.
    """

    point: str
    judge_version: str | None
    model: str | None
    decisions: int
    false_promotions: Rate
    false_rejections: Rate
    escalations: Rate
    unknown: int  # decisions whose truths don't settle whether the gate was right

    BOUNDS: ClassVar[dict[str, float]] = {"promotion": 0.05, "rejection": 0.10, "escalation": 0.30}

    @classmethod
    def cohorts(cls, store: DecisionStore, point: str = "gate:raw->experimental") -> list[Self]:
        """One audit per judge wording and model; LLM-fallback decisions form their own cohorts."""
        keys = {
            (d.judge_version, d.model) for d in store.all() if d.point == point and d.mode is JevMode.ACTIVE
        }
        return [cls.of(store, point, version, model) for version, model in sorted(keys)]

    @classmethod
    def of(
        cls,
        store: DecisionStore,
        point: str = "gate:raw->experimental",
        judge_version: str | None = None,
        model: str | None = None,
    ) -> Self:
        decisions = [
            d
            for d in store.all()
            if d.point == point
            and d.mode is JevMode.ACTIVE
            and judge_version in (None, d.judge_version)
            and model in (None, d.model)
        ]
        promotions = [cls._wrong(store, d) for d in decisions if d.ruling.verdict is Verdict.PROMOTE]
        rejections = [cls._wrong(store, d) for d in decisions if d.ruling.verdict is Verdict.REJECT]
        escalations = sum(d.ruling.verdict is Verdict.ESCALATE for d in decisions)
        known_promotions = [w for w in promotions if w is not None]
        known_rejections = [w for w in rejections if w is not None]
        return cls(
            point=point,
            judge_version=judge_version,
            model=model,
            decisions=len(decisions),
            false_promotions=Rate(
                name="false promotions",
                errors=sum(known_promotions),
                of=len(known_promotions),
                bound=cls.BOUNDS["promotion"],
            ),
            false_rejections=Rate(
                name="false rejections",
                errors=sum(known_rejections),
                of=len(known_rejections),
                bound=cls.BOUNDS["rejection"],
            ),
            escalations=Rate(
                name="escalations", errors=escalations, of=len(decisions), bound=cls.BOUNDS["escalation"]
            ),
            unknown=len(promotions) - len(known_promotions) + len(rejections) - len(known_rejections),
        )

    @staticmethod
    def _wrong(store: DecisionStore, decision: Decision) -> bool | None:
        """Whether the verdict was wrong: True, False, or None when the truths don't settle it."""
        outcomes = store.outcomes(decision.id)
        truths = {q.field: Outcomes.strongest(outcomes, q.field) for q in decision.ruling.questions}
        known = [o.truth for o in truths.values() if o is not None]
        if decision.ruling.verdict is Verdict.PROMOTE:
            if False in known:
                return True
            return False if len(known) == len(truths) else None
        if len(known) == len(truths) and all(known):
            return True
        return False if False in known else None

    @staticmethod
    def render_all(audits: "list[GateAudit]") -> str:
        note = "Rulings of the judge, not promotions: merge checks and people decide after it."
        return (
            "\n\n".join([a.render() for a in audits] or ["No active decisions at this point."]) + f"\n{note}"
        )

    @property
    def within(self) -> bool:
        return all(r.within for r in (self.false_promotions, self.false_rejections, self.escalations))

    def render(self) -> str:
        cohort = (
            "all wordings and models, pooled"
            if self.judge_version is None and self.model is None
            else f"judge {self.judge_version or 'unversioned'}, {self.model or 'any model'}"
        )
        lines = [
            f"{self.point} ({cohort}): {self.decisions} active decisions "
            f"({self.unknown} not settled by the outcomes)",
            *(f"  {r.render()}" for r in (self.false_promotions, self.false_rejections, self.escalations)),
            "  all within bounds" if self.within else "  not all within bounds",
        ]
        return "\n".join(lines)
