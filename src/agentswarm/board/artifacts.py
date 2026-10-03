"""Creating artifacts: the next free id, a branch name, and an append that is safe against races."""

import re

from agentswarm.board.blackboard import Blackboard
from agentswarm.board.eventlog import ConcurrencyConflict
from agentswarm.core.config import LevelLadder
from agentswarm.core.domain import Artifact, ArtifactCreated, ArtifactId, FindingId, TeamName


class ArtifactFactory:
    """Puts new artifacts on the board at the lowest level. Used by people, the router and decomposition."""

    def __init__(self, board: Blackboard, ladder: LevelLadder) -> None:
        self._board = board
        self._ladder = ladder

    def create(
        self,
        task: str,
        created_by: TeamName,
        parent: ArtifactId | None = None,
        fixes: FindingId | None = None,
    ) -> Artifact:
        while True:
            views = self._board.current()
            id = ArtifactId(f"a-{len(views.artifacts()) + 1:04d}")
            artifact = Artifact(
                id=id,
                task=task,
                branch=f"{self._ladder.lowest}/{id}-{self.slug(task)}",
                level=self._ladder.lowest,
                parent=parent,
                fixes=fixes,
                created_by=created_by,
            )
            try:
                self._board.append([ArtifactCreated(artifact=artifact)], expected_head=views.seq)
            except ConcurrencyConflict:
                continue  # someone else appended; the id may be taken
            return artifact

    @staticmethod
    def slug(task: str, max_length: int = 40) -> str:
        """A branch-safe slug from a task description."""
        words = re.sub(r"[^a-z0-9]+", "-", task.lower()).strip("-")
        return words[:max_length].rstrip("-") or "task"
