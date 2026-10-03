"""One judgement: ask a judge, rule on its answers, record the decision (proposal §11.2 steps 2-3, §15.1)."""

from collections.abc import Mapping, Sequence
from fnmatch import fnmatchcase
from typing import Self

from agentswarm.core.clock import Clock
from agentswarm.core.config import JevConfig, JevMode, Threshold
from agentswarm.core.domain import ArtifactContext, ArtifactId
from agentswarm.judging.deciders import Decider
from agentswarm.judging.decisions import Decision, DecisionDraft, DecisionStore
from agentswarm.judging.judges import Judge, JudgeCatalog, JudgeState
from agentswarm.judging.policy import Policy


class ModeTable:
    """Which decision points act on Jev's ruling (active) and which only log it (shadow), per point."""

    def __init__(self, default: JevMode, active: Sequence[str] = ()) -> None:
        self._default = default
        self._active = tuple(active)

    @classmethod
    def of(cls, config: JevConfig) -> Self:
        return cls(config.mode, config.active)

    def mode_for(self, point: str) -> JevMode:
        if any(fnmatchcase(point, pattern) for pattern in self._active):
            return JevMode.ACTIVE
        return self._default


class Judgement:
    """Asks a judge about an artifact, rules on the answers with the policy, and records the decision.

    Used wherever Jev decides something: at gates, and in teams' relevance filters.
    """

    def __init__(
        self,
        judge: Judge,
        policy: Policy,
        decider: Decider,
        decisions: DecisionStore,
        modes: ModeTable | JevMode,  # a single mode applies to every point
        clock: Clock,
    ) -> None:
        self.judge = judge
        self._policy = policy
        self._decider = decider
        self._decisions = decisions
        self._modes = modes if isinstance(modes, ModeTable) else ModeTable(modes)
        self._clock = clock

    def mode_for(self, point: str) -> JevMode:
        return self._modes.mode_for(point)

    def ask(
        self,
        point: str,
        ctx: ArtifactContext,
        commit: str,
        base: str,
        diff: str,
        proposal: Sequence[str] = (),
    ) -> Decision:
        """Ask, rule and record. Raises DeciderError if the decider gives no answers."""
        return self.ask_state(point, ctx.artifact.id, commit, base, JudgeState.of(ctx, diff, proposal))

    def ask_state(
        self, point: str, artifact: ArtifactId, commit: str, base: str, state: JudgeState
    ) -> Decision:
        """Ask about a state built by the caller, e.g. the supervisor's view of recent work."""
        answered = self._decider.decide(state, self.judge.spec)
        return self._decisions.record(
            DecisionDraft(
                point=point,
                artifact=artifact,
                commit=commit,
                base=base,
                mode=self.mode_for(point),
                judge=self.judge.name,
                state=state,
                answers=answered.answers.model_dump(mode="json"),
                ruling=self._policy.rule(answered.answers),
                model=answered.model,
                at=self._clock.now(),
            )
        )


class Judgements:
    """Builds judgements by judge name, all sharing one decider, decision log, mode and clock."""

    def __init__(
        self,
        judges: JudgeCatalog,
        decider: Decider | None,  # None when the project has no judges at all
        decisions: DecisionStore,
        modes: ModeTable | JevMode,
        clock: Clock,
    ) -> None:
        self._judges = judges
        self._decider = decider
        self.decisions = decisions
        self._modes = modes
        self.clock = clock

    def build(self, judge: str, thresholds: Mapping[str, Threshold] | None = None) -> Judgement:
        if self._decider is None:
            raise ValueError(f"judge {judge!r} needs a decider, but the project has none")
        spec = self._judges.get(judge)
        return Judgement(
            spec, Policy(spec, thresholds), self._decider, self.decisions, self._modes, self.clock
        )
