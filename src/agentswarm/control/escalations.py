"""The escalation queue: what a person sees and decides (proposal §14.1, "escalations go to a human").

Needs only the board and the decision log, so reading and resolving escalations works without models or
a Jev key.
"""

from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict

from agentswarm.board.blackboard import Blackboard
from agentswarm.core.domain import ArtifactId, EscalationResolved, Level, Resolution, Resumed
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
        if self._board.current().open_escalation(artifact) is None:
            raise ValueError(f"{artifact} has no open escalation")
        self._board.append([EscalationResolved(artifact=artifact, resolution=resolution, note=note)])

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
