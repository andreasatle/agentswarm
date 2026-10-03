"""The finding router: blocking findings above the lowest level become fix artifacts (proposal §20, Q5).

Teams only report findings. This plain-code loop turns each open MAJOR or BLOCKER finding on an artifact
above the lowest level into a fix artifact at the lowest level, once, and resolves the finding when the
fix has reached the finding's level. At the lowest level the executor fixes its own artifact's findings,
so nothing is routed there.
"""

from agentswarm.board.artifacts import ArtifactFactory
from agentswarm.board.blackboard import Blackboard
from agentswarm.core.config import LevelLadder
from agentswarm.core.domain import Event, Finding, FindingResolved, Severity, TeamName


class FindingRouter:
    NAME = TeamName("router")
    BLOCKING = (Severity.MAJOR, Severity.BLOCKER)

    def __init__(self, board: Blackboard, artifacts: ArtifactFactory, ladder: LevelLadder) -> None:
        self._board = board
        self._artifacts = artifacts
        self._ladder = ladder

    def step(self) -> int:
        """Create missing fix artifacts and resolve fixed findings. Returns how many actions it took."""
        return self._create_fixes() + self._resolve_fixed()

    def _create_fixes(self) -> int:
        views = self._board.current()
        created = 0
        for artifact in views.artifacts():
            if artifact.level == self._ladder.lowest:
                continue
            for finding in views.open_findings(artifact.id):
                if finding.severity in self.BLOCKING and views.fix_for(finding.id) is None:
                    self._artifacts.create(
                        self.fix_task(artifact.task, finding), self.NAME, artifact.id, finding.id
                    )
                    created += 1
        return created

    def _resolve_fixed(self) -> int:
        views = self._board.current()
        events: list[Event] = []
        for artifact in views.artifacts():
            for finding in views.open_findings(artifact.id):
                fix = views.fix_for(finding.id)
                if fix is not None and self._ladder.reached(views.artifact(fix).level, finding.level):
                    events.append(
                        FindingResolved(
                            artifact=artifact.id,
                            finding=finding.id,
                            resolved_by=fix,
                            reason=f"{fix} reached {finding.level}",
                        )
                    )
        self._board.append(events)
        return len(events)

    @staticmethod
    def fix_task(original: str, finding: Finding) -> str:
        return (
            f"Fix a problem found by {finding.team} after this task was done: {original}\n\n"
            f"The problem ({finding.kind}):\n{finding.detail}"
        )
