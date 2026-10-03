"""One judgement: ask a judge, rule on its answers, record the decision (proposal §11.2 steps 2-3, §15.1)."""

from agentswarm.core.clock import Clock
from agentswarm.core.config import JevMode
from agentswarm.core.domain import ArtifactContext
from agentswarm.judging.deciders import Decider
from agentswarm.judging.decisions import Decision, DecisionDraft, DecisionStore
from agentswarm.judging.judges import Judge, JudgeState
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

    def ask(self, point: str, ctx: ArtifactContext, commit: str, base: str, diff: str) -> Decision:
        """Ask, rule and record. Raises DeciderError if the decider gives no answers."""
        state = JudgeState.of(ctx, diff)
        answered = self._decider.decide(state, self.judge.spec)
        return self._decisions.record(
            DecisionDraft(
                point=point,
                artifact=ctx.artifact.id,
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
