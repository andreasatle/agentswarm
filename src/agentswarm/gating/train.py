"""Release-train promotion for gates above the base level (Phase 3 design, "release train").

Levels above the base move as prefixes of the level below. The merge queue makes the base level a
first-parent line of merge commits, one per artifact; a gate above it fast-forwards its upper level to
one of those commits, moving every artifact up to it in one batch:

    1. candidates: the lower level's first-parent commits the upper level doesn't have, oldest first;
    2. the ready prefix: stop at the first artifact that isn't ready (at the lower level, signed off by
       every required team for its current version, no open blocking findings, unclaimed);
    3. hard checks on the prefix's last commit; if they fail, find the first commit that fails, promote
       what comes before it, and give that artifact a blocker finding (the router makes the fix);
    4. fast-forward the upper level (compare-and-swap) and append one Promoted per artifact.

A gate that requires a human escalates once per target commit instead of promoting.
"""

from collections.abc import Sequence
from datetime import timedelta

from agentswarm.board.blackboard import Blackboard, BoardViews
from agentswarm.board.claims import ClaimRegistry
from agentswarm.board.context import ContextBuilder
from agentswarm.core.clock import Clock
from agentswarm.core.domain import (
    Artifact,
    Claim,
    Escalated,
    Event,
    Finding,
    FindingAdded,
    Promoted,
    Severity,
    TeamName,
)
from agentswarm.gating.gate import Gate
from agentswarm.workspace.git import Repository


class TrainPromoter:
    BLOCKING = (Severity.MAJOR, Severity.BLOCKER)

    def __init__(
        self,
        gate: Gate,
        requires: Sequence[TeamName],
        board: Blackboard,
        claims: ClaimRegistry,
        repository: Repository,
        contexts: ContextBuilder,
        clock: Clock,
        lease: timedelta = timedelta(minutes=10),
    ) -> None:
        self._gate = gate
        self._requires = tuple(requires)
        self._board = board
        self._claims = claims
        self._repository = repository
        self._contexts = contexts
        self._clock = clock
        self._lease = lease
        self._checked: dict[str, tuple[Finding, ...]] = {}  # hard-check findings per commit; empty = passed

    @property
    def gate(self) -> Gate:
        return self._gate

    def step(self) -> int:
        ready = self._ready_prefix()
        if not ready:
            return 0
        if self._gate.requires_human:
            return self._ask_human(*ready[-1])
        held = self._claim([artifact for _, artifact in ready])
        if held is None:
            return 0
        try:
            return self._promote(ready)
        finally:
            for claim in held:
                self._claims.release(claim)

    def _ready_prefix(self) -> list[tuple[str, Artifact]]:
        views = self._board.current()
        upper = self._repository.tip(self._gate.to_level)
        commits = self._repository.first_parent_commits(upper, self._repository.tip(self._gate.from_level))
        by_commit = {c: a for a in views.artifacts() if (c := views.merge_commit(a.id)) is not None}
        ready: list[tuple[str, Artifact]] = []
        for commit in commits:
            artifact = by_commit.get(commit)
            if artifact is None or not self._is_ready(views, artifact):
                break
            ready.append((commit, artifact))
        return ready

    def _is_ready(self, views: BoardViews, artifact: Artifact) -> bool:
        return (
            artifact.level == self._gate.from_level
            and all(views.signed_off(artifact.id, team) for team in self._requires)
            and not any(f.severity in self.BLOCKING for f in views.open_findings(artifact.id))
            and views.claim_on(artifact.id, self._clock.now()) is None
        )

    def _claim(self, artifacts: list[Artifact]) -> list[Claim] | None:
        """Claim the whole batch, so no analyzer adds a finding mid-promotion; None if any is taken."""
        held: list[Claim] = []
        for artifact in artifacts:
            claim = self._claims.claim(artifact.id, self._gate.team, self._lease)
            if claim is None:
                for taken in held:
                    self._claims.release(taken)
                return None
            held.append(claim)
        return held

    def _promote(self, ready: list[tuple[str, Artifact]]) -> int:
        prefix, failure = self._checked_prefix(ready)
        events: list[Event] = [FindingAdded(finding=failure)] if failure is not None else []
        if prefix:
            target = prefix[-1][0]
            self._repository.fast_forward(
                self._gate.to_level, target, self._repository.tip(self._gate.to_level)
            )
            events += [
                Promoted(
                    artifact=artifact.id,
                    from_level=self._gate.from_level,
                    to_level=self._gate.to_level,
                    gate_decision=None,
                    commit=target,
                )
                for _, artifact in prefix
            ]
        self._board.append(events)
        return len(prefix) + (failure is not None)

    def _checked_prefix(
        self, ready: list[tuple[str, Artifact]]
    ) -> tuple[list[tuple[str, Artifact]], Finding | None]:
        """The longest prefix whose last commit passes the hard checks, and the finding that stopped it."""
        if not self._findings(*ready[-1]):
            return ready, None
        for index, (commit, artifact) in enumerate(ready):
            findings = self._findings(commit, artifact)
            if findings:
                return ready[:index], findings[0]
        return ready, None  # unreachable: the last commit failed above

    def _findings(self, commit: str, artifact: Artifact) -> tuple[Finding, ...]:
        if commit not in self._checked:
            worktree = self._repository.checkout_detached(commit, f"_train-{self._gate.to_level}")
            try:
                self._checked[commit] = self._gate.run_checks(
                    worktree.path, self._contexts.build(artifact.id)
                )
            finally:
                worktree.remove()
        return self._checked[commit]

    def _ask_human(self, commit: str, artifact: Artifact) -> int:
        """Escalate once per target commit; a person approves with `agentswarm resolve ... --accept`."""
        reason = f"{self._gate.name}: needs human approval to promote up to {commit[:12]}"
        asked = any(
            isinstance(e, Escalated) and e.reason == reason
            for e in self._board.current().history(artifact.id)
        )
        if asked:
            return 0
        self._board.append([Escalated(artifact=artifact.id, reason=reason)])
        return 1
