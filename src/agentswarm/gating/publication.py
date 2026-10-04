"""Moving a level's branch and recording it, so that a crash in between can be repaired (CODEX_AUDIT.md F03).

Git and the board can't change in one transaction, so a branch move is a small protocol:

1. append `PromotionStarted` (branch, previous tip, target, the promotions it carries);
2. move the branch with compare-and-swap (only if it still points to the previous tip);
3. append the promotions and `PromotionSettled(moved=True)` together.

`recover` settles a started move that was never settled: if the branch reached the target, the promotions
are recorded; if not, the move is marked as not having happened and the artifacts are judged again.
"""

from collections.abc import Sequence

from agentswarm.board.blackboard import Blackboard, BoardViews
from agentswarm.core.domain import Event, Level, Promoted, PromotionSettled, PromotionStarted
from agentswarm.workspace.git import GitError, Repository


class LevelPublisher:
    def __init__(self, board: Blackboard, repository: Repository) -> None:
        self._board = board
        self._repository = repository

    def record(self, events: Sequence[Event]) -> BoardViews:
        """Events that move no branch, such as a rejection."""
        return self._board.append(events)

    def publish(
        self, branch: Level, target: str, promotions: Sequence[Promoted], also: Sequence[Event] = ()
    ) -> BoardViews:
        """Move `branch` to `target` (a descendant of its tip) and record `promotions` with `also`."""
        previous = self._repository.tip(branch)
        self._board.append(
            [PromotionStarted(branch=branch, previous=previous, target=target, promotions=tuple(promotions))]
        )
        try:
            self._repository.fast_forward(branch, target, previous)
        except GitError:
            self._board.append([PromotionSettled(branch=branch, moved=False)])
            raise
        return self._board.append([*promotions, *also, PromotionSettled(branch=branch, moved=True)])

    def recover(self) -> int:
        """Settle every started move left open by a crash. Returns how many it settled."""
        settled = 0
        for started in self._board.current().moving():
            tip = self._repository.tip(started.branch)
            moved = tip != started.previous and self._repository.contains(tip, started.target)
            self._board.append(
                [*started.promotions, PromotionSettled(branch=started.branch, moved=True)]
                if moved
                else [PromotionSettled(branch=started.branch, moved=False)]
            )
            settled += 1
        return settled
