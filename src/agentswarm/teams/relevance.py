"""Relevance filters: Jev decides whether a change is worth a team's effort (proposal §12.1).

The filter asks one question, records the decision, and later records the outcome automatically: if the
team worked on the change, the truth is whether that work found anything (§15.2). In shadow mode the
team always works, so every decision gets an outcome; in active mode a clear "no" skips the work.
"""

from pydantic import BaseModel, ConfigDict

from agentswarm.core.clock import Clock
from agentswarm.core.config import JevMode
from agentswarm.core.domain import ArtifactContext, DecisionId, TeamName
from agentswarm.judging.deciders import DeciderError
from agentswarm.judging.decisions import DecisionStore, Outcome, OutcomeSource
from agentswarm.judging.judgement import Judgement
from agentswarm.judging.policy import Verdict
from agentswarm.teams.runner import Relevance


class RelevanceCheck(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    relevance: Relevance
    work: bool  # whether the team should do the work
    decision: DecisionId | None  # None when the decider was unavailable


class RelevanceFilter:
    FIELD = "relevant"  # every relevance judge asks exactly this one question

    def __init__(self, team: TeamName, judgement: Judgement, decisions: DecisionStore, clock: Clock) -> None:
        if judgement.judge.fields() != (self.FIELD,):
            raise ValueError(
                f"a relevance judge asks one question, {self.FIELD!r}; {judgement.judge.name} doesn't"
            )
        self._team = team
        self._judgement = judgement
        self._decisions = decisions
        self._clock = clock

    @property
    def point(self) -> str:
        return f"relevance:{self._team}"

    def check(self, ctx: ArtifactContext, commit: str, base: str, diff: str) -> RelevanceCheck:
        try:
            decision = self._judgement.ask(self.point, ctx, commit, base, diff)
        except DeciderError:
            # Without an answer the team does the work: a filter may save effort, never block it.
            return RelevanceCheck(relevance=Relevance.UNSURE, work=True, decision=None)
        relevance = {
            Verdict.PROMOTE: Relevance.YES,
            Verdict.REJECT: Relevance.NO,
            Verdict.ESCALATE: Relevance.UNSURE,
        }[decision.ruling.verdict]
        work = decision.mode is JevMode.SHADOW or relevance is not Relevance.NO
        return RelevanceCheck(relevance=relevance, work=work, decision=decision.id)

    def record_result(self, check: RelevanceCheck, found: bool, detail: str) -> None:
        """After the team worked: the change was relevant if the work found something."""
        if check.decision is None or not check.work:
            return
        self._decisions.add_outcome(
            Outcome(
                decision=check.decision,
                field=self.FIELD,
                truth=found,
                source=OutcomeSource.TEAM_RESULT,
                detail=detail,
                at=self._clock.now(),
            )
        )
