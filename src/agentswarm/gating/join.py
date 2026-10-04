"""The join gate: an umbrella moves up once all its parts have (proposal §11.4).

An umbrella artifact (split by decomposition) does no work of its own. When every child has reached the
next level, the join gate checks the umbrella there: the hard checks of that level's gate on its current
tip, then a Jev question, "together, do the children accomplish the parent task?". Passing promotes the
umbrella; failing or unsure escalates to a person, who can accept the join (`agentswarm resolve ...
--accept`): that replaces the judgement, never the hard checks. In shadow mode the question is logged and
the umbrella moves up regardless.
"""

from datetime import timedelta

from agentswarm.board.blackboard import Blackboard, BoardViews
from agentswarm.board.claims import ClaimRegistry
from agentswarm.board.context import ContextBuilder
from agentswarm.core.clock import Clock
from agentswarm.core.config import JevMode, LevelLadder
from agentswarm.core.domain import Artifact, DecisionId, Escalated, Event, Level, Promoted, TeamName
from agentswarm.gating.gate import Gate
from agentswarm.judging.deciders import DeciderError
from agentswarm.judging.judgement import Judgement
from agentswarm.judging.policy import Verdict
from agentswarm.workspace.git import Repository


class JoinPromoter:
    TEAM = TeamName("gate:join")

    def __init__(
        self,
        ladder: LevelLadder,
        gates: dict[Level, Gate],  # by the level they promote from; supplies each level's hard checks
        judgement: Judgement | None,
        board: Blackboard,
        claims: ClaimRegistry,
        repository: Repository,
        contexts: ContextBuilder,
        clock: Clock,
        lease: timedelta = timedelta(minutes=10),
    ) -> None:
        self._ladder = ladder
        self._gates = gates
        self._judgement = judgement
        self._board = board
        self._claims = claims
        self._repository = repository
        self._contexts = contexts
        self._clock = clock
        self._lease = lease

    def step(self) -> int:
        views = self._board.current()
        joined = 0
        for umbrella in [a for a in views.artifacts() if views.is_umbrella(a.id) and not views.dropped(a.id)]:
            target = self._ladder.next(umbrella.level)
            if (
                target is None
                or not self._children_reached(views, umbrella, target)
                or self._asked(views, umbrella, target)
            ):
                continue
            claim = self._claims.claim(umbrella.id, self.TEAM, self._lease)
            if claim is None:
                continue
            try:
                self._board.append([self._join(views, umbrella, target)])
                joined += 1
            finally:
                self._claims.release(claim)
        return joined

    def _children_reached(self, views: BoardViews, umbrella: Artifact, target: Level) -> bool:
        children = views.children(umbrella.id)
        return bool(children) and all(self._ladder.reached(c.level, target) for c in children)

    def _asked(self, views: BoardViews, umbrella: Artifact, target: Level) -> bool:
        """Whether a person has an open escalation about this umbrella: they decide from there."""
        return views.open_escalation(umbrella.id) is not None

    def _join(self, views: BoardViews, umbrella: Artifact, target: Level) -> Event:
        tip = self._repository.tip(target)
        ctx = self._contexts.build(umbrella.id)
        gate = self._gates.get(umbrella.level)
        if gate is not None:
            worktree = self._repository.checkout_detached(tip, f"_join-{umbrella.id}")
            try:
                failures = gate.run_checks(worktree.path, ctx)
            finally:
                worktree.remove()
            if failures:
                return self._escalate(umbrella, target, f"hard checks fail on {target}: {failures[0].detail}")

        decision: DecisionId | None = None
        if self._judgement is not None and not views.accepted(umbrella.id):
            try:
                judged = self._judgement.ask(
                    f"join:{umbrella.level}->{target}", ctx, tip, tip, self._children_diff(views, umbrella)
                )
            except DeciderError as error:
                if self._judgement.mode_for(f"join:{umbrella.level}->{target}") is JevMode.ACTIVE:
                    return self._escalate(umbrella, target, f"judge unavailable: {error}")
            else:
                decision = judged.id
                if judged.mode is JevMode.ACTIVE and judged.ruling.verdict is not Verdict.PROMOTE:
                    detail = "; ".join(f.detail for f in judged.ruling.failures) or "the judge is unsure"
                    return self._escalate(umbrella, target, detail)
        return Promoted(
            artifact=umbrella.id,
            from_level=umbrella.level,
            to_level=target,
            gate_decision=decision,
            commit=tip,
        )

    def _children_diff(self, views: BoardViews, umbrella: Artifact) -> str:
        """Every merged part's own change, one after the other (nested umbrellas contribute their parts)."""
        diffs: list[str] = []
        for child in views.children(umbrella.id):
            if views.is_umbrella(child.id):
                diffs.append(self._children_diff(views, child))
            elif (merge := views.merge_commit(child.id)) is not None:
                base, head = Repository.merged_change(merge)
                diffs.append(f"# {child.id}: {child.task}\n{self._repository.diff(base, head)}")
        return "\n".join(diffs)

    def _escalate(self, umbrella: Artifact, target: Level, detail: str) -> Escalated:
        return Escalated(artifact=umbrella.id, reason=self._reason(umbrella.level, target, detail))

    @staticmethod
    def _reason(level: Level, target: Level, detail: str) -> str:
        return f"join {level} -> {target}: {detail}"
