"""A campaign: critics attack the merged product, part by part and focus by focus, in rounds.

What hardening's red teams (teams/hardening.py) and a document's reviewers (teams/review.py) share: plain
code that, once the builders are idle, picks the next (part, focus) pair of the current round, lets the
critic attack it on the base level's tip, records the attack, and measures the product when a round is
whole. Done after `quiet_rounds` whole rounds in a row confirm nothing; no focus is ever switched off (the
user, 2026-10-15: a quiet critic can still find a rare failure). What differs is the parts (spec sections, a
document's sections), the focuses, and how an attack gathers evidence and turns it into tasks.
"""

from abc import ABC, abstractmethod
from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict

from agentswarm.board.blackboard import Blackboard, BoardViews
from agentswarm.control.budget import Budget
from agentswarm.core.config import LevelLadder
from agentswarm.core.domain import Attacked, Event, Focus, TeamName
from agentswarm.evaluation.score import Measure
from agentswarm.workspace.git import Repository, Worktree


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Part(Frozen):
    """What one attack is about: a section of a spec, or of a document."""

    title: str
    text: str


class Campaign(ABC):
    NAME: TeamName

    def __init__(
        self,
        board: Blackboard,
        repository: Repository,
        ladder: LevelLadder,
        quiet_rounds: int,
        budget: Budget | None = None,
        progress: Measure | None = None,
    ) -> None:
        self._board = board
        self._repository = repository
        self._ladder = ladder
        self._quiet_rounds = quiet_rounds
        self._budget = budget
        self._progress = progress

    @property
    @abstractmethod
    def focuses(self) -> tuple[Focus, ...]:
        """What each part is attacked for, in order."""

    @abstractmethod
    def parts(self, worktree: Worktree) -> list[Part]:
        """The parts of the product to attack, in order, on its checkout."""

    @abstractmethod
    async def attack(
        self, worktree: Worktree, tip: str, round: int, part: Part, focus: Focus, views: BoardViews
    ) -> list[Event]:
        """One attack: its tasks and their records, and the `Attacked` event last."""

    async def prepare(self, views: BoardViews) -> int:
        """Work to do before any attack (looking again at a failed fix, say). Returns the actions taken."""
        return 0

    async def step(self) -> int:
        """One attack, when the builders are idle and the campaign isn't done. Returns the actions taken."""
        views = self._board.current()
        if views.paused or (self._budget is not None and self._budget.exhausted(views)):
            return 0
        prepared = await self.prepare(views)
        if prepared:
            return prepared
        if self.building(views):
            return 0  # the product is still changing: attack it once the work at the lowest level is done
        attacks = self.mine(views)
        tip = self._repository.tip(self._ladder.base)
        worktree = self._repository.checkout_detached(tip, f"_attack-{len(views.attacks()) + 1}")
        try:
            parts = self.parts(worktree)
            pairs = [(p.title, f) for p in parts for f in self.focuses]
            if not pairs or self.done(attacks, len(pairs)):
                return 0
            round, (title, focus) = self.next_attack(attacks, pairs)
            part = next(p for p in parts if p.title == title)
            events = await self.attack(worktree, tip, round, part, focus, views)
        finally:
            worktree.remove()
        self._board.append(events)
        attacked = [e for e in [*attacks, *events] if isinstance(e, Attacked) and e.round == round]
        if len(attacked) == len(pairs) and self._progress is not None:  # a round is complete: measure it
            self._board.append([await self._progress.measure(self._board.current())])
        return 1

    def mine(self, views: BoardViews) -> list[Attacked]:
        """This campaign's attacks: those on its focuses (a campaign whose focuses changed starts its rounds
        over, rather than counting the old focuses' attacks into the new rounds)."""
        return [a for a in views.attacks() if a.focus in self.focuses]

    def building(self, views: BoardViews) -> bool:
        """Work is open at the lowest level. A task split into steps does no work itself: its steps count
        (2026-10-16: YAML's first task stayed open with a step escalated, and no attack ever started)."""
        return any(
            a.level == self._ladder.lowest and views.active(a.id) and not views.children(a.id)
            for a in views.artifacts()
        )

    def done(self, attacks: Sequence[Attacked], pairs: int) -> bool:
        """Whether the last `quiet_rounds` whole rounds each confirmed nothing."""
        rounds = sorted({a.round for a in attacks})
        complete = [r for r in rounds if sum(a.round == r for a in attacks) >= pairs]
        quiet = complete[-self._quiet_rounds :]
        return len(quiet) == self._quiet_rounds and all(
            sum(a.confirmed for a in attacks if a.round == r) == 0 for r in quiet
        )

    @staticmethod
    def next_attack(
        attacks: Sequence[Attacked], pairs: Sequence[tuple[str, Focus]]
    ) -> tuple[int, tuple[str, Focus]]:
        """The current round and its first pair not yet attacked in it."""
        round = max((a.round for a in attacks), default=1)
        done: set[tuple[str, Focus]] = {(a.section, a.focus) for a in attacks if a.round == round}
        if len(done) >= len(pairs):
            round, done = round + 1, set[tuple[str, Focus]]()
        return round, next(p for p in pairs if p not in done)
