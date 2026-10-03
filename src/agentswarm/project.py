"""One project with its north star: the board, the repository, its teams and its gates, wired together."""

import re
from datetime import timedelta
from pathlib import Path
from typing import Self

from agentswarm.blackboard import Blackboard, BoardViews
from agentswarm.claims import ClaimRegistry
from agentswarm.clock import Clock, SystemClock
from agentswarm.config import ProjectConfig
from agentswarm.context import ContextBuilder
from agentswarm.domain import Artifact, ArtifactCreated, ArtifactId, ProjectId, TeamName
from agentswarm.eventlog import ConcurrencyConflict, SqliteEventLog
from agentswarm.gates import Decider, Gate
from agentswarm.git import Git, Repository
from agentswarm.promotion import Promoter
from agentswarm.sandbox import Sandboxes
from agentswarm.teams import TeamCatalog, TeamRunner


class Project:
    """Layout on disk under `root`: blackboard.sqlite, repo/ and worktrees/."""

    def __init__(
        self,
        config: ProjectConfig,
        log: SqliteEventLog,
        board: Blackboard,
        repository: Repository,
        runners: list[TeamRunner],
        promoters: list[Promoter],
    ) -> None:
        self.config = config
        self._log = log
        self.board = board
        self.repository = repository
        self._runners = runners
        self._promoters = promoters

    @classmethod
    def open(
        cls,
        config: ProjectConfig,
        root: Path,
        catalog: TeamCatalog,
        decider: Decider,
        clock: Clock | None = None,
        lease: timedelta = timedelta(minutes=10),
    ) -> Self:
        """Open the project at `root`, creating its log and repository on first use."""
        clock = clock or SystemClock()
        root.mkdir(parents=True, exist_ok=True)
        log = SqliteEventLog(root / "blackboard.sqlite", ProjectId(config.project), clock)
        board = Blackboard(log)
        claims = ClaimRegistry(board, clock)
        repository = Repository.open_or_create(root / "repo", root / "worktrees", config.levels, Git())
        contexts = ContextBuilder(board, repository, config.north_star)
        sandbox = Sandboxes.build(config.sandbox)
        runners = [
            TeamRunner(catalog.build(team), board, claims, repository, contexts, clock, lease)
            for team in config.teams
        ]
        promoters = [
            Promoter(
                Gate.from_config(gate, config.checks, decider, sandbox),
                board,
                claims,
                repository,
                contexts,
                clock,
                lease,
            )
            for gate in config.gates
        ]
        return cls(config, log, board, repository, runners, promoters)

    def create_artifact(self, task: str, created_by: TeamName = TeamName("human")) -> Artifact:
        """Put a new artifact on the board at the lowest level, with the next free id."""
        while True:
            views = self.board.current()
            id = ArtifactId(f"a-{len(views.artifacts()) + 1:04d}")
            artifact = Artifact(
                id=id,
                task=task,
                branch=f"{self.config.levels.lowest}/{id}-{self.slug(task)}",
                level=self.config.levels.lowest,
                created_by=created_by,
            )
            try:
                self.board.append([ArtifactCreated(artifact=artifact)], expected_head=views.seq)
            except ConcurrencyConflict:
                continue  # someone else appended; the id may be taken
            return artifact

    async def step(self) -> int:
        """Run every team once, then every gate once. Returns the number of actions taken."""
        actions = 0
        for runner in self._runners:
            actions += await runner.step()
        for promoter in self._promoters:
            actions += promoter.step()
        return actions

    async def run_until_idle(self, max_rounds: int = 50) -> int:
        """Step until a round takes no action. Returns the number of rounds that took action."""
        for round in range(max_rounds):
            if await self.step() == 0:
                return round
        raise RuntimeError(f"project {self.config.project!r} still busy after {max_rounds} rounds")

    def views(self) -> BoardViews:
        return self.board.current()

    def close(self) -> None:
        self._log.close()

    @staticmethod
    def slug(task: str, max_length: int = 40) -> str:
        """A branch-safe slug from a task description."""
        words = re.sub(r"[^a-z0-9]+", "-", task.lower()).strip("-")
        return words[:max_length].rstrip("-") or "task"
