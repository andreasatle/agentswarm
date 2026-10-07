"""One project with its north star: the board, the repository, its teams and its gates, wired together."""

import asyncio
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Protocol, Self

from agentswarm.board.artifacts import ArtifactFactory
from agentswarm.board.blackboard import Blackboard, BoardViews
from agentswarm.board.claims import ClaimRegistry
from agentswarm.board.context import ContextBuilder
from agentswarm.board.eventlog import SqliteEventLog
from agentswarm.board.spend import SpendLedger
from agentswarm.control.budget import Budget
from agentswarm.control.outcomes import OutcomeRecorder
from agentswarm.control.router import FindingRouter
from agentswarm.control.scheduler import Scheduler
from agentswarm.control.supervisor import Supervisor
from agentswarm.control.worklist import Worklist
from agentswarm.core.clock import Clock, SystemClock
from agentswarm.core.config import ProjectConfig
from agentswarm.core.domain import Artifact, Level, ProjectId, TeamName, Usage
from agentswarm.gating.checks import CheckSet, RatchetCheck
from agentswarm.gating.gate import Gate
from agentswarm.gating.join import JoinPromoter
from agentswarm.gating.promotion import Promoter, PromotionLoop
from agentswarm.gating.publication import LevelPublisher
from agentswarm.gating.train import TrainPromoter
from agentswarm.jev.client import JevClient
from agentswarm.judging.deciders import Decider, FallbackDecider, JevDecider, LLMDecider
from agentswarm.judging.decisions import DecisionStore, SqliteDecisions
from agentswarm.judging.judgement import Judgements, ModeTable
from agentswarm.judging.judges import JudgeCatalog
from agentswarm.llm.models import ChatModels
from agentswarm.llm.workers import LangChainWorkers, WorkerProvider
from agentswarm.teams.catalog import TeamCatalog, TeamResources
from agentswarm.teams.runner import TeamRunner
from agentswarm.workspace.git import Git, Repository
from agentswarm.workspace.lock import RootIdentity, RootLock
from agentswarm.workspace.sandbox import Sandboxes


class StillBusy(RuntimeError):
    """The project was still taking actions when the round limit was reached."""


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
    scheduler: Scheduler | None = None  # shared by several projects in one process (agentswarm swarm)
    settle_plans: bool = True  # False: plan outcomes come from outside, e.g. a comparison's hidden tests


class Project:
    """Layout on disk under `root`: blackboard.sqlite, decisions.sqlite, repo/ and worktrees/."""

    def __init__(
        self,
        config: ProjectConfig,
        board: Blackboard,
        repository: Repository,
        runners: list[TeamRunner],
        promoters: list[PromotionLoop],
        decisions: DecisionStore,
        artifacts: ArtifactFactory,
        supervisor: Supervisor,
        owned: list[Closeable],
        outcomes: OutcomeRecorder | None = None,
        worklist: Worklist | None = None,
    ) -> None:
        self._worklist = worklist
        self.config = config
        self.board = board
        self.repository = repository
        self._runners = runners
        self._promoters = promoters
        self.decisions = decisions
        self._owned = owned  # resources this project opened, closed with it
        self._artifacts = artifacts
        self._router = FindingRouter(board, self._artifacts, config.levels)
        self._publisher = LevelPublisher(board, repository)  # settles branch moves a crash interrupted
        self._supervisor = supervisor
        self.outcomes = outcomes or OutcomeRecorder(board, {}, {})  # settles routed calls for the ledger

    @classmethod
    def open(cls, config: ProjectConfig, root: Path, services: ProjectServices | None = None) -> Self:
        """Open the project at `root`, creating its log and repository on first use."""
        services = services or ProjectServices()
        cls.validate(config, services)  # before anything touches the disk
        root.mkdir(parents=True, exist_ok=True)
        owned: list[Closeable] = [RootLock.acquire(root)]
        try:
            RootIdentity(project=config.project, levels=tuple(config.levels.levels)).claim(root)
            return cls._build(config, root, services, owned)
        except BaseException:
            for resource in reversed(owned):  # whatever was opened before the failure
                resource.close()
            raise

    @staticmethod
    def validate(config: ProjectConfig, services: ProjectServices) -> None:
        """Every team and judge the configuration names exists (CODEX_AUDIT.md F15)."""
        unknown = [t.use for t in config.teams if not services.teams.knows(t.use)]
        if unknown:
            raise ValueError(f"unknown teams {unknown}; known: {services.teams.names()}")
        named = [g.judge for g in config.gates if g.judge] + [
            t.relevance for t in config.teams if t.relevance
        ]
        for judge in named:
            services.judges.get(judge)  # raises, naming the known judges
        worklist = config.worklist
        if worklist is not None:
            check = config.checks.get(worklist.check)
            if check is None or check.ratchet is None:
                raise ValueError(
                    f"worklist.check {worklist.check!r} must name a check with `ratchet: <level>`"
                )

    @classmethod
    def _build(
        cls, config: ProjectConfig, root: Path, services: ProjectServices, owned: list[Closeable]
    ) -> Self:
        clock, lease = services.clock, services.lease
        log = SqliteEventLog(root / "blackboard.sqlite", ProjectId(config.project), clock)
        owned.append(log)
        board = Blackboard(log)
        claims = ClaimRegistry(board, clock)
        repository = Repository.open_or_create(
            root / "repo", root / "worktrees", config.levels, Git(), config.seed
        )
        contexts = ContextBuilder(board, repository, config.north_star)
        sandbox = Sandboxes.build(config.sandbox)
        if services.scheduler is not None:
            sandbox = services.scheduler.throttle(sandbox)
        checks = CheckSet(config.checks, sandbox)
        budget = Budget(config.budgets.usd_per_day, clock)

        decisions = services.decisions
        if decisions is None:
            decisions = SqliteDecisions(root / "decisions.sqlite")
            owned.append(decisions)
        decider = services.decider
        decomposes = any(t.use in TeamResources.PLANNERS for t in config.teams)
        judged_gates = any(g.judge for g in config.gates)
        relevance_filters = any(t.relevance for t in config.teams)
        routes = any(t.routing for t in config.teams)
        needs_judges = judged_gates or relevance_filters or decomposes or routes
        workers = services.workers or LangChainWorkers(ChatModels(config.models))
        ledger = SpendLedger(board)
        if decider is None and needs_judges:
            jev = JevClient.from_env(config.jev.model, config.jev.timeout_s)
            owned.append(jev)
            decider = JevDecider(jev, services.scheduler.jev if services.scheduler is not None else None)
            if config.jev.fallback is not None:
                fallback = config.models[config.jev.fallback]
                meter = ledger.meter("jev-fallback", config.jev.fallback, fallback.usd_per_million)
                llm = LLMDecider(workers.worker(config.jev.fallback, 3, meter), fallback.model)
                decider = FallbackDecider(decider, llm)

        def jev_spend(by: str, model: str, usage: Usage) -> None:
            ledger.record(by, model, usage, config.jev.usd_per_million)

        judgements = Judgements(
            services.judges, decider, decisions, ModeTable.of(config.jev), clock, jev_spend
        )

        artifacts = ArtifactFactory(board, config.levels)
        resources = TeamResources(
            workers,
            checks,
            judgements,
            artifacts,
            config.levels,
            [t.use for t in config.teams],
            lambda team, model: ledger.meter(team, model, config.models[model].usd_per_million),
            {name: model.model for name, model in config.models.items()},
            config.project,
        )
        runners = [
            TeamRunner(
                services.teams.build(team, resources),
                board,
                claims,
                repository,
                contexts,
                clock,
                lease,
                budget,
                services.scheduler,
                ProjectId(config.project),
            )
            for team in config.teams
        ]
        promoters: list[PromotionLoop] = []
        gates: dict[Level, Gate] = {}
        for gate_config in config.gates:
            judgement = (
                judgements.build(gate_config.judge, gate_config.thresholds) if gate_config.judge else None
            )
            gate = Gate.from_config(gate_config, checks, judgement)
            gates[gate.from_level] = gate
            if gate_config.from_level == config.levels.lowest:
                promoters.append(Promoter(gate, board, claims, repository, contexts, clock, lease))
            else:
                requires = [TeamName(team) for team in gate_config.requires]
                promoters.append(
                    TrainPromoter(gate, requires, board, claims, repository, contexts, clock, lease)
                )
        if decomposes:
            join = judgements.build("JoinCheck")
            promoters.append(
                JoinPromoter(config.levels, gates, join, board, claims, repository, contexts, clock, lease)
            )
        drift = judgements.build("DriftCheck") if decider is not None else None
        supervisor = Supervisor(config, board, repository, decisions, drift, clock, budget)
        outcomes = OutcomeRecorder(board, resources.routers, resources.model_ids, services.settle_plans)
        worklist = None
        if config.worklist is not None:
            check = checks.check(config.worklist.check)
            assert isinstance(check, RatchetCheck)  # checked by validate()
            worklist = Worklist(board, artifacts, repository, config.levels, check, config.worklist)
        return cls(
            config,
            board,
            repository,
            runners,
            promoters,
            decisions,
            artifacts,
            supervisor,
            owned,
            outcomes,
            worklist,
        )

    def create_artifact(self, task: str, created_by: TeamName = TeamName("human")) -> Artifact:
        """Put a new artifact on the board at the lowest level, with the next free id."""
        return self._artifacts.create(task, created_by)

    async def step(self) -> int:
        """Run every team, gate and controller (outcome recorder included) once. Returns the actions taken."""
        actions = 0
        for runner in self._runners:
            actions += await runner.step()
        # Gates, the router and the supervisor block (git, sandboxes, synchronous Jev): in a thread, so other
        # projects in the same process keep going meanwhile.
        actions += await asyncio.to_thread(self._publisher.recover)
        for promoter in self._promoters:
            actions += await asyncio.to_thread(promoter.step)
        actions += await asyncio.to_thread(self._router.step)
        if self._worklist is not None:
            actions += await asyncio.to_thread(self._worklist.step)
        actions += await asyncio.to_thread(self._supervisor.step)
        actions += await asyncio.to_thread(self.outcomes.step)
        return actions

    async def run_until_idle(self, max_rounds: int = 50) -> int:
        """Step until a round takes no action. Returns the number of rounds that took action."""
        for round in range(max_rounds):
            if await self.step() == 0:
                return round
        raise StillBusy(f"project {self.config.project!r} still busy after {max_rounds} rounds")

    def views(self) -> BoardViews:
        return self.board.current()

    def close(self) -> None:
        for resource in reversed(self._owned):
            resource.close()
