"""The outcome recorder: settles routed calls from later events and teaches the routing ledger (Phase 8).

See docs/design/routing-per-role.md. A team never grades its own calls (RESEARCH.md P1, P12): this
plain-code loop folds each artifact's history and, once later events have decided how a routed call went,
writes one outcome to that team's router and an `OutcomeRecorded` event, so it is never written twice.

    produce   a commit succeeds when the gate promotes it (acceptance tests included, which the producer
              can't change); it fails when it is rejected, escalated, or reworked before promotion; a
              discarded attempt fails at once
    plan      a split succeeds when the umbrella is promoted; it fails when the planner escalates, a child
              is escalated, or the umbrella is rejected. Comparisons settle plans from hidden tests instead
    critique  not settled in projects: there is no truth about a review without an answer key (probes)
    accept    likewise
"""

from collections.abc import Iterator, Mapping, Sequence

from pydantic import BaseModel, ConfigDict

from agentswarm.board.blackboard import Blackboard, BoardViews
from agentswarm.core.domain import (
    AttemptFailed,
    CallKind,
    CommitPushed,
    Escalated,
    Event,
    OutcomeRecorded,
    Profiled,
    Promoted,
    Rejected,
    Routed,
    Sized,
    TeamName,
)
from agentswarm.teams.routing import Router


class Settled(BaseModel):
    """One routed call whose outcome is known: which team, which model, the event it settles, and how."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    routed: Routed
    model_id: str
    settles: int  # its position in the artifact's history
    success: bool


class OutcomeRecorder:
    def __init__(
        self,
        board: Blackboard,
        routers: Mapping[TeamName, Router],
        model_ids: Mapping[str, str],  # model name -> "<provider>:<model>"
        settle_plans: bool = True,  # False when a comparison settles plans from hidden tests
    ) -> None:
        self._board = board
        self._routers = routers
        self._model_ids = model_ids
        self._settle_plans = settle_plans

    def step(self) -> int:
        """Record every outcome settled since the last step. Returns how many it recorded."""
        if not self._routers:
            return 0
        views = self._board.current()
        events: list[Event] = []
        for artifact in views.artifacts():
            history = views.history(artifact.id)
            settled = [*self.produce(history)]
            if self._settle_plans:
                settled.extend(self.plan(views, history))
            events.extend(self._record(history, settled))
        self._board.append(events)
        return len(events)

    def settle_plans(self, views: BoardViews, success: bool) -> int:
        """Every unrecorded plan call settled at once, e.g. by hidden tests on what was delivered."""
        events: list[Event] = []
        for artifact in views.artifacts():
            history = views.history(artifact.id)
            settled = [
                Settled(routed=e, model_id=e.model_id, settles=i, success=success)
                for i, e in enumerate(history)
                if isinstance(e, Routed) and e.call_kind is CallKind.PLAN
            ]
            events.extend(self._record(history, settled))
        self._board.append(events)
        return len(events)

    def _record(self, history: Sequence[Event], settled: Sequence[Settled]) -> list[Event]:
        profile = next((e for e in history if isinstance(e, Profiled)), None)
        if profile is None:
            return []  # no difficulty, so no band to learn in
        done = {(e.team, e.settles) for e in history if isinstance(e, OutcomeRecorded)}
        events: list[Event] = []
        for outcome in settled:
            team = outcome.routed.team
            router = self._routers.get(team)
            if router is None or (team, outcome.settles) in done:
                continue
            if router.record(outcome.model_id, profile, outcome.success):
                events.append(
                    OutcomeRecorded(
                        artifact=profile.artifact,
                        team=team,
                        call_kind=router.kind,
                        model_id=outcome.model_id,
                        success=outcome.success,
                        settles=outcome.settles,
                    )
                )
        return events

    def produce(self, history: Sequence[Event]) -> Iterator[Settled]:
        routed = next((e for e in history if isinstance(e, Routed) and e.call_kind is CallKind.PRODUCE), None)
        if routed is None:
            return
        for index, event in enumerate(history):
            if isinstance(event, AttemptFailed) and event.team == routed.team:
                model_id = self.model_id(event.model, routed)
                yield Settled(routed=routed, model_id=model_id, settles=index, success=False)
            elif isinstance(event, CommitPushed) and event.model is not None:
                success = self.commit_outcome(history[index + 1 :])
                if success is not None:
                    model_id = self.model_id(event.model, routed)
                    yield Settled(routed=routed, model_id=model_id, settles=index, success=success)

    @staticmethod
    def commit_outcome(after: Sequence[Event]) -> bool | None:
        """How a commit went, from the events after it; None while undecided."""
        for event in after:
            match event:
                case Promoted():
                    return True
                case Rejected():
                    return False
                case Escalated(parks=True, approval_for=None):
                    return False  # checks still failing, incomplete, or stalled
                case CommitPushed(model=str()):
                    return False  # reworked before it was promoted: the reviewers found a problem
                case _:
                    pass
        return None

    def plan(self, views: BoardViews, history: Sequence[Event]) -> Iterator[Settled]:
        for index, event in enumerate(history):
            if not (isinstance(event, Routed) and event.call_kind is CallKind.PLAN):
                continue
            after = history[index + 1 :]
            split = next((e for e in after if isinstance(e, Sized) and not e.fits), None)
            if split is None:
                if any(self.parks(e) for e in after):
                    # the planner gave up, or its split was stopped
                    yield Settled(routed=event, model_id=event.model_id, settles=index, success=False)
                continue
            if any(isinstance(e, Promoted) for e in after):
                yield Settled(routed=event, model_id=event.model_id, settles=index, success=True)
            elif any(isinstance(e, Rejected) for e in after) or any(
                self.parks(e) for child in split.children for e in views.history(child)
            ):
                yield Settled(routed=event, model_id=event.model_id, settles=index, success=False)

    @staticmethod
    def parks(event: Event) -> bool:
        return isinstance(event, Escalated) and event.parks and event.approval_for is None

    def model_id(self, name: str | None, routed: Routed) -> str:
        if name is None:
            return routed.model_id
        return self._model_ids.get(name, name)
