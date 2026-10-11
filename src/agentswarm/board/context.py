"""Builds the typed context that teams, gates and models see (proposal §8.3)."""

from agentswarm.board.blackboard import Blackboard
from agentswarm.core.domain import AcceptanceWritten, ArtifactContext, ArtifactId
from agentswarm.workspace.git import Repository


class ContextBuilder:
    def __init__(self, board: Blackboard, repository: Repository, north_star: str) -> None:
        self._board = board
        self._repository = repository
        self._north_star = north_star

    def build(self, id: ArtifactId) -> ArtifactContext:
        views = self._board.current()
        artifact = views.artifact(id)
        merge_commit = views.merge_commit(id)
        closes = views.closes(id)
        written = [e for e in views.history(closes) if isinstance(e, AcceptanceWritten)] if closes else []
        return ArtifactContext(
            north_star=self._north_star,
            artifact=artifact,
            history=tuple(views.history(id)),
            open_findings=tuple(views.open_findings(id)),
            diff_summary=self._repository.diff_stat(artifact, merge_commit),
            version=views.version(id),
            signed_off_by=views.signed_off_by(id),
            merge_commit=merge_commit,
            depth=views.depth(id),
            waits_for=views.waits_for(id),
            closes=closes,
            parent_acceptance=written[0] if written else None,
            completes=views.completes(id),
        )
