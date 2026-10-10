"""The supervisor: watches a project for stalls, wasted rounds and drift (proposal §14.1).

A plain-code loop the project runs every step, after teams, gates and the router:

- **Stalls:** an artifact below the top level, not an umbrella, not already escalated, with no event for
  `supervisor.stall_after_minutes`, is escalated. Time counts only while this supervisor runs (the hours
  between runs are no stall), and an artifact waiting its turn while a team works on another isn't stalled.
- **Rejections:** rejected `supervisor.max_rejections` times in a row: escalated.
- **Round budget:** more than `budgets.max_rounds_per_artifact` commits: escalated.
- **Drift:** after every `supervisor.drift_every` changes merged into the first shared level, Jev is
  asked whether the recent work advances the north star. In active mode a clear "no" pauses new work
  (teams that start work wait; analyzers and gates carry on) until a person resumes the project.

Each cause escalates an artifact once; a person takes it from there (`agentswarm escalations`).
Leases need no supervision: expired claims are ignored by the views.
"""

from datetime import datetime, timedelta

from agentswarm.board.blackboard import Blackboard, BoardViews
from agentswarm.control.budget import Budget
from agentswarm.core.clock import Clock
from agentswarm.core.config import JevMode, LevelLadder, ProjectConfig
from agentswarm.core.domain import (
    Artifact,
    CommitPushed,
    Escalated,
    EscalationResolved,
    Event,
    Paused,
    Promoted,
    Rejected,
    Unbuildable,
)
from agentswarm.judging.deciders import DeciderError
from agentswarm.judging.decisions import DecisionStore
from agentswarm.judging.judgement import Judgement
from agentswarm.judging.judges import JudgeState
from agentswarm.judging.policy import Verdict
from agentswarm.workspace.git import Repository


class Supervisor:
    DRIFT_POINT = "drift"

    def __init__(
        self,
        config: ProjectConfig,
        board: Blackboard,
        repository: Repository,
        decisions: DecisionStore,
        drift: Judgement | None,  # None: no decider, so no drift checks
        clock: Clock,
        budget: Budget | None = None,
    ) -> None:
        self._budget = budget
        self._budget_escalated: datetime | None = None  # the day it was last reported
        self._config = config
        self._ladder: LevelLadder = config.levels
        self._board = board
        self._repository = repository
        self._decisions = decisions
        self._drift = drift
        self._clock = clock
        self._started = clock.now()  # idle time between runs is no stall (CommonMark, 2026-10-09)

    def step(self) -> int:
        views = self._board.current()
        events: list[Event] = [e for a in views.artifacts() if (e := self._escalation(views, a)) is not None]
        drift = self._drift_check(views)
        if drift is not None:
            events.append(drift)
        budget = self._budget_check(views)
        if budget is not None:
            events.append(budget)
        self._board.append(events)
        return len(events)

    def _escalation(self, views: BoardViews, artifact: Artifact) -> Escalated | None:
        if artifact.level == self._ladder.levels[-1] or views.is_umbrella(artifact.id):
            return None
        history = views.history(artifact.id)
        if self._escalated_since_last_change(history):
            return None
        reason = self._reason(views, artifact, history)
        return Escalated(artifact=artifact.id, reason=f"supervisor: {reason}") if reason else None

    def _reason(self, views: BoardViews, artifact: Artifact, history: list[Event]) -> str | None:
        rejections = 0
        for event in reversed(history):  # rejections since the artifact last moved up
            if isinstance(event, Promoted | EscalationResolved):
                break
            rejections += isinstance(event, Rejected)
        if rejections >= self._config.supervisor.max_rejections:
            return f"rejected {rejections} times in a row"
        since = max((i for i, e in enumerate(history) if isinstance(e, EscalationResolved)), default=-1)
        rounds = sum(isinstance(e, CommitPushed) for e in history[since + 1 :])  # a person reset the count
        budget = self._config.budgets.max_rounds_per_artifact
        if budget is not None and rounds > budget:
            return f"used {rounds} rounds, over the budget of {budget}"
        now = self._clock.now()
        if views.waits_for(artifact.id) is not None:
            return None  # waiting for an earlier sibling of its split: its turn comes when that one merges
        if views.claim_on(artifact.id, now) is None and views.active_claims(now):
            return None  # waiting its turn: a team is busy with another artifact
        idle = now - max(views.last_activity(artifact.id), self._started)
        if idle > timedelta(minutes=self._config.supervisor.stall_after_minutes):
            return (
                f"stalled at {artifact.level}: nothing happened for {int(idle.total_seconds() // 60)} minutes"
            )
        return None

    @staticmethod
    def _escalated_since_last_change(history: list[Event]) -> bool:
        """Escalate once per state: an escalation stands until something changes the artifact."""
        for event in reversed(history):
            if isinstance(event, Escalated):
                return True
            if isinstance(event, CommitPushed | Promoted | Rejected | EscalationResolved | Unbuildable):
                return False
        return False

    def _budget_check(self, views: BoardViews) -> Escalated | None:
        """Once a day, when the project's budget runs out (its teams wait until the next day)."""
        if self._budget is None or not self._budget.exhausted(views):
            return None
        today = self._budget.day_start()
        if self._budget_escalated == today:
            return None
        self._budget_escalated = today
        return Escalated(artifact=None, reason=f"supervisor: {self._budget.describe(views)}")

    def _drift_check(self, views: BoardViews) -> Paused | None:
        if self._drift is None or views.paused:
            return None
        merged = [a for a in views.artifacts() if views.merge_commit(a.id) is not None]
        asked = sum(d.point == self.DRIFT_POINT for d in self._decisions.all())
        if len(merged) // self._config.supervisor.drift_every <= asked:
            return None
        recent = sorted(merged, key=lambda a: views.last_activity(a.id))[
            -self._config.supervisor.drift_every :
        ]
        state = JudgeState(
            north_star=self._config.north_star,
            task="Recent work:\n" + "\n".join(f"- {a.task}" for a in recent),
            must_fix=(),
            diff=JudgeState.truncate("\n".join(self._change(views, a) for a in recent)),
        )
        tip = self._repository.tip(self._ladder.base)
        try:
            decision = self._drift.ask_state(self.DRIFT_POINT, recent[-1].id, tip, tip, state)
        except DeciderError:
            return None
        if decision.mode is JevMode.ACTIVE and decision.ruling.verdict is Verdict.REJECT:
            return Paused(reason=f"drift: recent work doesn't advance the north star ({decision.id})")
        return None

    def _change(self, views: BoardViews, artifact: Artifact) -> str:
        merge = views.merge_commit(artifact.id)
        assert merge is not None
        return f"# {artifact.id}: {artifact.task}\n{self._repository.diff(*Repository.merged_change(merge))}"
