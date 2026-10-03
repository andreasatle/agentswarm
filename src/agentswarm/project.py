"""One project with its north star: the board, the repository, its teams and its gates, wired together."""

import re
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Protocol, Self

from agentswarm.board.blackboard import Blackboard, BoardViews
from agentswarm.board.claims import ClaimRegistry
from agentswarm.board.context import ContextBuilder
from agentswarm.board.eventlog import ConcurrencyConflict, SqliteEventLog
from agentswarm.core.clock import Clock, SystemClock
from agentswarm.core.config import ProjectConfig
from agentswarm.core.domain import Artifact, ArtifactCreated, ArtifactId, ProjectId, TeamName
from agentswarm.gating.checks import CheckSet
from agentswarm.gating.gate import Gate
from agentswarm.gating.promotion import Promoter
from agentswarm.jev.client import JevClient
from agentswarm.judging.deciders import Decider, JevDecider
from agentswarm.judging.decisions import DecisionStore, SqliteDecisions
from agentswarm.judging.judgement import Judgement
from agentswarm.judging.judges import JudgeCatalog
from agentswarm.judging.policy import Policy
from agentswarm.llm.models import ChatModels
from agentswarm.llm.workers import LangChainWorkers, WorkerProvider
from agentswarm.teams.catalog import TeamCatalog, TeamResources
from agentswarm.teams.runner import TeamRunner
from agentswarm.workspace.git import Git, Repository
from agentswarm.workspace.sandbox import Sandboxes


class Closeable(Protocol):
    def close(self) -> None: ...


@dataclass(frozen=True)
class ProjectServices:
    """The collaborators a project is built from. Defaults are the real ones; tests replace what they need."""

    teams: TeamCatalog = field(default_factory=TeamCatalog.standard)
    judges: JudgeCatalog = field(default_factory=JudgeCatalog.standard)
    workers: WorkerProvider | None = None  # None: the configured models, through LangChain
    decider: Decider | None = None  # None: Jev, built only if some gate has a judge
    decisions: DecisionStore | None = None  # None: decisions.sqlite in the project root
    clock: Clock = field(default_factory=SystemClock)
    lease: timedelta = timedelta(minutes=10)


class Project:
    """Layout on disk under `root`: blackboard.sqlite, decisions.sqlite, repo/ and worktrees/."""

    def __init__(
        self,
        config: ProjectConfig,
        board: Blackboard,
        repository: Repository,
        runners: list[TeamRunner],
        promoters: list[Promoter],
        decisions: DecisionStore,
        owned: list[Closeable],
    ) -> None:
        self.config = config
        self.board = board
        self.repository = repository
        self._runners = runners
        self._promoters = promoters
        self.decisions = decisions
        self._owned = owned  # resources this project opened, closed with it

    @classmethod
    def open(cls, config: ProjectConfig, root: Path, services: ProjectServices | None = None) -> Self:
        """Open the project at `root`, creating its log and repository on first use."""
        services = services or ProjectServices()
        clock, lease = services.clock, services.lease
        root.mkdir(parents=True, exist_ok=True)
        log = SqliteEventLog(root / "blackboard.sqlite", ProjectId(config.project), clock)
        board = Blackboard(log)
        claims = ClaimRegistry(board, clock)
        repository = Repository.open_or_create(
            root / "repo", root / "worktrees", config.levels, Git(), config.seed
        )
        contexts = ContextBuilder(board, repository, config.north_star)
        checks = CheckSet(config.checks, Sandboxes.build(config.sandbox))
        workers = services.workers or LangChainWorkers(ChatModels(config.models))
        resources = TeamResources(workers, checks)
        runners = [
            TeamRunner(
                services.teams.build(team, resources), board, claims, repository, contexts, clock, lease
            )
            for team in config.teams
        ]

        owned: list[Closeable] = [log]
        decisions = services.decisions
        if decisions is None:
            decisions = SqliteDecisions(root / "decisions.sqlite")
            owned.append(decisions)
        decider = services.decider
        if decider is None and any(gate.judge for gate in config.gates):
            jev = JevClient.from_env(config.jev.model, config.jev.timeout_s)
            owned.append(jev)
            decider = JevDecider(jev)
        promoters: list[Promoter] = []
        for gate in config.gates:
            judgement = None
            if gate.judge is not None and decider is not None:
                judge = services.judges.get(gate.judge)
                policy = Policy(judge, gate.thresholds)
                judgement = Judgement(judge, policy, decider, decisions, config.jev.mode, clock)
            promoters.append(
                Promoter(
                    Gate.from_config(gate, checks, judgement),
                    board,
                    claims,
                    repository,
                    contexts,
                    clock,
                    lease,
                )
            )
        return cls(config, board, repository, runners, promoters, decisions, owned)

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
        for resource in reversed(self._owned):
            resource.close()

    @staticmethod
    def slug(task: str, max_length: int = 40) -> str:
        """A branch-safe slug from a task description."""
        words = re.sub(r"[^a-z0-9]+", "-", task.lower()).strip("-")
        return words[:max_length].rstrip("-") or "task"
