"""One judgement: ask a judge, rule on its answers, record the decision (proposal §11.2 steps 2-3, §15.1)."""

from collections.abc import Mapping, Sequence

from agentswarm.core.clock import Clock
from agentswarm.core.config import JevMode, Threshold
from agentswarm.core.domain import ArtifactContext, ArtifactId
from agentswarm.judging.deciders import Decider
from agentswarm.judging.decisions import Decision, DecisionDraft, DecisionStore
from agentswarm.judging.judges import Judge, JudgeCatalog, JudgeState
from agentswarm.judging.policy import Policy


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
        mode: JevMode,
        clock: Clock,
    ) -> None:
        self.judge = judge
        self._policy = policy
        self._decider = decider
        self._decisions = decisions
        self.mode = mode
        self._clock = clock

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
                mode=self.mode,
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
        mode: JevMode,
        clock: Clock,
    ) -> None:
        self._judges = judges
        self._decider = decider
        self.decisions = decisions
        self._mode = mode
        self.clock = clock

    def build(self, judge: str, thresholds: Mapping[str, Threshold] | None = None) -> Judgement:
        if self._decider is None:
            raise ValueError(f"judge {judge!r} needs a decider, but the project has none")
        spec = self._judges.get(judge)
        return Judgement(
            spec, Policy(spec, thresholds), self._decider, self.decisions, self._mode, self.clock
        )
