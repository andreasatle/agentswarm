"""A wired-up project in a temporary directory, for tests that need the board and git together."""

import sys
from collections.abc import Sequence
from datetime import timedelta
from pathlib import Path

from agentswarm.board.blackboard import Blackboard
from agentswarm.board.claims import ClaimRegistry
from agentswarm.board.context import ContextBuilder
from agentswarm.board.eventlog import SqliteEventLog
from agentswarm.control.budget import Budget
from agentswarm.core.clock import FakeClock
from agentswarm.core.config import CheckConfig, JevMode, LevelLadder
from agentswarm.core.domain import Artifact, ArtifactCreated, ArtifactId, CommitPushed, FindingKind, TeamName
from agentswarm.gating.checks import CommandCheck, HardCheck
from agentswarm.gating.gate import Gate
from agentswarm.gating.promotion import Promoter
from agentswarm.gating.train import TrainPromoter
from agentswarm.judging.deciders import FakeDecider
from agentswarm.judging.decisions import InMemoryDecisions
from agentswarm.judging.judgement import Judgement
from agentswarm.judging.judges import ChangeReadiness, Judge
from agentswarm.judging.policy import Policy
from agentswarm.teams.runner import Team, TeamRunner
from agentswarm.workspace.git import Git, Repository
from agentswarm.workspace.sandbox import LocalSandbox
from tests.support.samples import Samples


class Checks:
    """Hard checks built from small Python programs, so they run anywhere the tests run."""

    @staticmethod
    def python(name: str, code: str, kind: FindingKind = FindingKind.TEST_FAILURE) -> CommandCheck:
        config = CheckConfig(command=(sys.executable, "-c", code), timeout_s=30, kind=kind)
        return CommandCheck(name, config, LocalSandbox())

    @classmethod
    def no_file_named(cls, filename: str) -> CommandCheck:
        """Fails if `filename` exists in the checked tree."""
        return cls.python(
            f"no-{filename}",
            f"import pathlib, sys; sys.exit(1 if pathlib.Path({filename!r}).exists() else 0)",
        )


class Harness:
    LADDER = LevelLadder(levels=(Samples.RAW, Samples.EXPERIMENTAL))
    WITH_CANDIDATE = LevelLadder(levels=(Samples.RAW, Samples.EXPERIMENTAL, Samples.CANDIDATE))
    NORTH_STAR = "a small demo project"

    def __init__(
        self,
        root: Path,
        checks: Sequence[HardCheck] = (),
        answers: Sequence[ChangeReadiness] | None = None,  # None: the gate has no judge
        mode: JevMode = JevMode.ACTIVE,
        ladder: LevelLadder = LADDER,
    ) -> None:
        self.clock = FakeClock(Samples.AT)
        self.log = SqliteEventLog(root / "events.sqlite", Samples.PROJECT, self.clock)
        self.board = Blackboard(self.log)
        self.claims = ClaimRegistry(self.board, self.clock)
        self.repository = Repository.create(root / "repo", root / "worktrees", ladder, Git())
        self.contexts = ContextBuilder(self.board, self.repository, self.NORTH_STAR)
        self.decider = FakeDecider(answers or ())
        self.decisions = InMemoryDecisions()
        judgement = None
        if answers is not None:
            judge = Judge(ChangeReadiness)
            judgement = Judgement(judge, Policy(judge), self.decider, self.decisions, mode, self.clock)
        self.gate = Gate(Samples.RAW, Samples.EXPERIMENTAL, checks, judgement)
        self.promoter = Promoter(
            self.gate,
            self.board,
            self.claims,
            self.repository,
            self.contexts,
            self.clock,
            timedelta(minutes=10),
        )

    def create(self, id: ArtifactId = Samples.A, task: str = "add retry") -> Artifact:
        artifact = Samples.artifact(id, task)
        self.board.append([ArtifactCreated(artifact=artifact)])
        return artifact

    def push(self, artifact: Artifact, files: dict[str, str], announce: bool = True) -> str:
        """Commit files on the artifact's branch, and announce the commit on the board unless told not to."""
        worktree = self.repository.checkout_artifact(artifact)
        try:
            for name, content in files.items():
                worktree.write(name, content)
            sha = worktree.commit(f"{artifact.id}: update")
        finally:
            worktree.remove()
        if announce:
            self.board.append([CommitPushed(artifact=artifact.id, commit=sha)])
        return sha

    def train(
        self,
        requires: Sequence[TeamName] = (),
        checks: Sequence[HardCheck] = (),
        requires_human: bool = False,
    ) -> TrainPromoter:
        """The release-train promoter for experimental -> candidate (needs ladder=WITH_CANDIDATE)."""
        gate = Gate(Samples.EXPERIMENTAL, Samples.CANDIDATE, checks, requires_human=requires_human)
        return TrainPromoter(
            gate, requires, self.board, self.claims, self.repository, self.contexts, self.clock
        )

    def runner(self, team: Team, budget: Budget | None = None) -> TeamRunner:
        return TeamRunner(
            team, self.board, self.claims, self.repository, self.contexts, self.clock, budget=budget
        )
