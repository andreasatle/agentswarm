"""The escalation queue: what a person sees and decides (proposal §14.1, "escalations go to a human").

Needs only the board and the decision log, so reading and resolving escalations works without models or
a Jev key.
"""

from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict

from agentswarm.board.blackboard import Blackboard
from agentswarm.core.domain import (
    ArtifactId,
    EscalationResolved,
    Event,
    Level,
    Resolution,
    Resumed,
    Sized,
    Unbuildable,
)
from agentswarm.judging.decisions import DecisionStore


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class DecisionSummary(Frozen):
    id: str
    point: str
    verdict: str
    mode: str


class OpenEscalation(Frozen):
    artifact: ArtifactId
    level: Level
    task: str
    reason: str
    decisions: tuple[DecisionSummary, ...]  # the judgements about this artifact, oldest first

    def render(self) -> str:
        lines = [f"{self.artifact} [{self.level}] {self.task}", f"  reason: {self.reason}"]
        lines += [f"  {d.id} {d.point}: {d.verdict} ({d.mode})" for d in self.decisions]
        return "\n".join(lines)


class Escalations:
    def __init__(self, board: Blackboard, decisions: DecisionStore | None) -> None:
        self._board = board
        self._decisions = decisions

    def open(self) -> list[OpenEscalation]:
        views = self._board.current()
        decisions = self._decisions.all() if self._decisions is not None else []
        return [
            OpenEscalation(
                artifact=escalation.artifact,
                level=views.artifact(escalation.artifact).level,
                task=views.artifact(escalation.artifact).task,
                reason=escalation.reason,
                decisions=tuple(
                    DecisionSummary(id=d.id, point=d.point, verdict=d.ruling.verdict, mode=d.mode)
                    for d in decisions
                    if d.artifact == escalation.artifact
                ),
            )
            for escalation in views.open_escalations()
            if escalation.artifact is not None
        ]

    def resolve(self, artifact: ArtifactId, resolution: Resolution, note: str = "") -> None:
        """Settles an open escalation, or a step every builder model failed before the planner splits it (a
        person may retry it instead, e.g. when a framework rule, not the work, made it fail)."""
        views = self._board.current()
        if views.open_escalation(artifact) is None and not self.awaiting_split(views.history(artifact)):
            raise ValueError(f"{artifact} has no open escalation and isn't waiting to be split")
        self._board.append([EscalationResolved(artifact=artifact, resolution=resolution, note=note)])

    def drop(self, artifact: ArtifactId, note: str = "") -> list[ArtifactId]:
        """A person retires a task and every step split from it, escalated or still queued, that hasn't moved
        up a level (2026-10-11: an over-eager split left 50 steps that would each have failed)."""
        views = self._board.current()
        dropped: list[ArtifactId] = []
        pending = [views.artifact(artifact)]
        while pending:
            current = pending.pop()
            if not views.dropped(current.id) and current.level == views.artifact(artifact).level:
                dropped.append(current.id)
            pending.extend(views.children(current.id))
        self._board.append(
            [EscalationResolved(artifact=id, resolution=Resolution.DROP, note=note) for id in dropped]
        )
        return dropped

    @staticmethod
    def awaiting_split(history: Sequence[Event]) -> bool:
        """Every builder model failed it, and neither the planner nor a person has answered yet."""
        answers = [
            e
            for e in history
            if isinstance(e, Sized | Unbuildable)
            or (isinstance(e, EscalationResolved) and e.resolution is Resolution.RETRY)
        ]
        return bool(answers) and isinstance(answers[-1], Unbuildable)

    def resume(self, note: str = "") -> None:
        if not self._board.current().paused:
            raise ValueError("the project is not paused")
        self._board.append([Resumed(reason=note or "resumed by a person")])

    @staticmethod
    def render(escalations: Sequence[OpenEscalation], paused: bool) -> str:
        blocks = [e.render() for e in escalations]
        if paused:
            blocks.insert(0, "The project is PAUSED (drift). Resume with `agentswarm resume`.")
        return "\n\n".join(blocks) or "No open escalations."
