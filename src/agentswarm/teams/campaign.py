"""A campaign: critics attack the merged product, part by part and focus by focus, in rounds.

What hardening's red teams (teams/hardening.py) and a document's reviewers (teams/review.py) share: plain
code that, once the builders are idle, picks the next (part, focus) pair of the current round, lets the
critic attack it on the base level's tip, records the attack, and measures the product when a round is
whole. Done after `quiet_rounds` whole rounds in a row confirm nothing; no focus is ever switched off (the
user, 2026-10-15: a quiet critic can still find a rare failure). What differs is the parts (spec sections, a
document's sections), the focuses, and how an attack gathers evidence and turns it into tasks.
"""

import asyncio
from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import ClassVar

from pydantic import BaseModel, ConfigDict

from agentswarm.board.blackboard import Blackboard, BoardViews
from agentswarm.control.budget import Budget
from agentswarm.core.config import LevelLadder
from agentswarm.core.domain import Attacked, Escalated, Event, Focus, TeamName
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
    FAILURES: ClassVar[int] = 3  # failed attempts in a row after which a run stops attacking

    def __init__(
        self,
        board: Blackboard,
        repository: Repository,
        ladder: LevelLadder,
        quiet_rounds: int,
        budget: Budget | None = None,
        progress: Measure | None = None,
        max_rounds: int | None = None,  # whole rounds at most, quiet or not (a document run's review: two)
        parallel: int = 4,  # attacks of one round made at the same time, each on its own checkout
    ) -> None:
        self._parallel = parallel
        self._max_rounds = max_rounds
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
        """Up to `parallel` attacks of the current round at once, when the builders are idle and the campaign
        isn't done. Returns the actions taken.

        A batch never reaches into the next round: that round attacks the product after this round's fixes.
        Each attack has its own checkout of the same tip; the tasks it creates go through the artifact
        factory, which is safe against races, and its events are appended when the batch is through."""
        views = self._board.current()
        if views.paused or (self._budget is not None and self._budget.exhausted(views)):
            return 0
        prepared = await self.prepare(views)
        if prepared:
            return prepared
        if self.building(views):
            return 0  # the product is still changing: attack it once the work at the lowest level is done
        if self.failing(views):
            return self.halt(views)
        attacks = self.mine(views)
        tip = self._repository.tip(self._ladder.base)
        number = len(views.attacks()) + 1
        worktrees = [self._repository.checkout_detached(tip, f"_attack-{number}")]
        try:
            parts = self.parts(worktrees[0])
            pairs = [(p.title, f) for p in parts for f in self.focuses]
            if not pairs or self.done(attacks, len(pairs)):
                return 0
            round, batch = self.next_attacks(attacks, pairs, self._parallel)
            worktrees += [
                self._repository.checkout_detached(tip, f"_attack-{number + i}") for i in range(1, len(batch))
            ]
            outcomes = await asyncio.gather(
                *(
                    self.attack(
                        worktree, tip, round, next(p for p in parts if p.title == title), focus, views
                    )
                    for worktree, (title, focus) in zip(worktrees, batch, strict=False)
                ),
                return_exceptions=True,
            )
        finally:
            for worktree in worktrees:
                worktree.remove()
        events = [e for outcome in outcomes if not isinstance(outcome, BaseException) for e in outcome]
        self._board.append(events)  # what the finished attacks found stays, even when another one raised
        failed = next((o for o in outcomes if isinstance(o, BaseException)), None)
        if failed is not None:
            raise failed  # e.g. OverBudget: the project's step reports it
        attacked = [e for e in [*attacks, *events] if isinstance(e, Attacked) and e.round == round]
        if len(attacked) == len(pairs) and self._progress is not None:  # a round is complete: measure it
            self._board.append([await self._progress.measure(self._board.current())])
        return len(batch)

    def mine(self, views: BoardViews) -> list[Attacked]:
        """This campaign's attacks: those on its focuses (a campaign whose focuses changed starts its rounds
        over, rather than counting the old focuses' attacks into the new rounds), and only those that took
        place: a failed model call is no attack, and never a quiet one."""
        return [a for a in views.attacks() if a.focus in self.focuses and not a.failed]

    def failing(self, views: BoardViews) -> bool:
        """The last `FAILURES` attempts since the latest score (a run is scored when it starts) all failed:
        the critic's model can't be reached (no credits, an outage), so attacking on would only record
        failures. The next run tries again."""
        tried = [a for a in views.attacks_since_score() if a.focus in self.focuses]
        return len(tried) >= self.FAILURES and all(a.failed for a in tried[-self.FAILURES :])

    def halt(self, views: BoardViews) -> int:
        """Stops this run's attacks with a notice for a person, once; the next run tries again."""
        reason = f"{self.NAME}: the critic's model failed {self.FAILURES} times in a row; attacks stopped"
        if not any(e.reason == reason for e in views.project_escalations()):
            self._board.append([Escalated(artifact=None, reason=reason, parks=False)])
        return 0

    def building(self, views: BoardViews) -> bool:
        """Work is open at the lowest level. A task split into steps does no work itself: its steps count
        (2026-10-16: YAML's first task stayed open with a step escalated, and no attack ever started)."""
        return any(
            a.level == self._ladder.lowest and views.active(a.id) and not views.children(a.id)
            for a in views.artifacts()
        )

    def done(self, attacks: Sequence[Attacked], pairs: int) -> bool:
        """Whether the last `quiet_rounds` whole rounds each confirmed nothing, or `max_rounds` are whole."""
        rounds = sorted({a.round for a in attacks})
        complete = [r for r in rounds if sum(a.round == r for a in attacks) >= pairs]
        if self._max_rounds is not None and len(complete) >= self._max_rounds:
            return True
        quiet = complete[-self._quiet_rounds :]
        return len(quiet) == self._quiet_rounds and all(
            sum(a.confirmed for a in attacks if a.round == r) == 0 for r in quiet
        )

    @classmethod
    def next_attack(
        cls, attacks: Sequence[Attacked], pairs: Sequence[tuple[str, Focus]]
    ) -> tuple[int, tuple[str, Focus]]:
        """The current round and its first pair not yet attacked in it."""
        round, batch = cls.next_attacks(attacks, pairs, 1)
        return round, batch[0]

    @staticmethod
    def next_attacks(
        attacks: Sequence[Attacked], pairs: Sequence[tuple[str, Focus]], most: int
    ) -> tuple[int, list[tuple[str, Focus]]]:
        """The current round and up to `most` of its pairs not yet attacked in it, in order, at most one per
        part: two attacks on one part at once could each make a fix task for it, and the fixes conflict."""
        round = max((a.round for a in attacks), default=1)
        done: set[tuple[str, Focus]] = {(a.section, a.focus) for a in attacks if a.round == round}
        if len(done) >= len(pairs):
            round, done = round + 1, set[tuple[str, Focus]]()
        batch: list[tuple[str, Focus]] = []
        for pair in pairs:
            if pair not in done and all(pair[0] != title for title, _ in batch) and len(batch) < most:
                batch.append(pair)
        return round, batch
