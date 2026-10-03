"""A wired-up project in a temporary directory, for tests that need the board and git together."""

import sys
from collections.abc import Sequence
from datetime import timedelta
from pathlib import Path

from agentswarm.blackboard import Blackboard
from agentswarm.claims import ClaimRegistry
from agentswarm.clock import FakeClock
from agentswarm.config import CheckConfig, LevelLadder
from agentswarm.context import ContextBuilder
from agentswarm.domain import Artifact, ArtifactCreated, ArtifactId, CommitPushed, FindingKind
from agentswarm.eventlog import SqliteEventLog
from agentswarm.gates import CommandCheck, FakeDecider, Gate, HardCheck, Verdict
from agentswarm.git import Git, Repository
from agentswarm.promotion import Promoter
from agentswarm.teams import Team, TeamRunner
from tests.samples import Samples


class Checks:
    """Hard checks built from small Python programs, so they run anywhere the tests run."""

    @staticmethod
    def python(name: str, code: str, kind: FindingKind = FindingKind.TEST_FAILURE) -> CommandCheck:
        return CommandCheck(name, CheckConfig(command=(sys.executable, "-c", code), timeout_s=30, kind=kind))

    @classmethod
    def no_file_named(cls, filename: str) -> CommandCheck:
        """Fails if `filename` exists in the checked tree."""
        return cls.python(
            f"no-{filename}",
            f"import pathlib, sys; sys.exit(1 if pathlib.Path({filename!r}).exists() else 0)",
        )


class Harness:
    LADDER = LevelLadder(levels=(Samples.RAW, Samples.EXPERIMENTAL))
    NORTH_STAR = "a small demo project"

    def __init__(
        self,
        root: Path,
        checks: Sequence[HardCheck] = (),
        verdicts: Sequence[Verdict] = (Verdict.PROMOTE,),
    ) -> None:
        self.clock = FakeClock(Samples.AT)
        self.log = SqliteEventLog(root / "events.sqlite", Samples.PROJECT, self.clock)
        self.board = Blackboard(self.log)
        self.claims = ClaimRegistry(self.board, self.clock)
        self.repository = Repository.create(root / "repo", root / "worktrees", self.LADDER, Git())
        self.contexts = ContextBuilder(self.board, self.repository, self.NORTH_STAR)
        self.decider = FakeDecider(verdicts)
        self.gate = Gate(Samples.RAW, Samples.EXPERIMENTAL, checks, self.decider)
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

    def runner(self, team: Team) -> TeamRunner:
        return TeamRunner(team, self.board, self.claims, self.repository, self.contexts, self.clock)
