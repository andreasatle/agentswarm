from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel

from agentswarm.board.artifacts import ArtifactFactory
from agentswarm.board.spend import Meter, SpendLedger, SpendScope
from agentswarm.control.outcomes import OutcomeRecorder
from agentswarm.core.config import JevMode
from agentswarm.core.domain import (
    ArtifactCreated,
    ArtifactId,
    AttemptFailed,
    CallKind,
    CommitPushed,
    DecisionId,
    Escalated,
    Event,
    FindingKind,
    OutcomeRecorded,
    Profiled,
    Promoted,
    Rejected,
    Repaired,
    Routed,
    Sized,
    Spent,
    TeamName,
    Unbuildable,
    Usage,
)
from agentswarm.judging.deciders import FakeDecider
from agentswarm.judging.judgement import Judgement
from agentswarm.judging.judges import CallProfile, Judge
from agentswarm.judging.policy import Policy
from agentswarm.llm.workers import FakeWorker, Toolbox
from agentswarm.teams.executor import ExecutorReport, ExecutorTeam
from agentswarm.teams.routing import Router, RoutingLedger, Tier
from tests.support.harness import Harness
from tests.support.samples import Samples
from tests.teams.test_executor import Steps, Toolboxes

SMALL, LARGE = "ollama:small", "ollama:large"
MODELS = {"small": SMALL, "large": LARGE}
PLANNER = TeamName("decomposer")


class Recording:
    """A board with one profiled artifact, routers for an executor and a planner, and a ledger."""

    def __init__(self, tmp_path: Path, settle_plans: bool = True) -> None:
        tmp_path.mkdir(parents=True, exist_ok=True)
        self.h = Harness(tmp_path)
        self.artifact = self.h.create()
        self.ledger = RoutingLedger(":memory:")
        judge = Judge(CallProfile)
        judgement = Judgement(
            judge, Policy(judge), FakeDecider([]), self.h.decisions, JevMode.ACTIVE, self.h.clock
        )
        self.routers = {
            Samples.EXECUTOR: Router(
                judgement, CallKind.PRODUCE, (SMALL, LARGE), self.ledger, 0.8, self.h.clock
            ),
            PLANNER: Router(judgement, CallKind.PLAN, (SMALL, LARGE), self.ledger, 0.8, self.h.clock),
        }
        self.recorder = OutcomeRecorder(self.h.board, self.routers, MODELS, settle_plans)
        profile = Profiled(
            artifact=self.artifact.id,
            difficulty=0.9,
            attributes={"simple": 0.1},
            decision=DecisionId("d"),
            active=True,
        )
        self.h.board.append([profile])

    def routed(self, team: TeamName, kind: CallKind, model: str = "small") -> Routed:
        return Routed(
            artifact=self.artifact.id,
            team=team,
            call_kind=kind,
            model=model,
            model_id=MODELS[model],
            difficulty=0.9,
            decision=None,
            acted=True,
        )

    def promoted(self, artifact: ArtifactId | None = None) -> Promoted:
        return Promoted(
            artifact=artifact or self.artifact.id,
            from_level=Samples.RAW,
            to_level=Samples.EXPERIMENTAL,
            gate_decision=None,
        )

    def rejected(self) -> Rejected:
        return Rejected(
            artifact=self.artifact.id, level=Samples.RAW, findings=(Samples.finding(),), gate_decision=None
        )

    def commit(self, model: str, sha: str = "c0ffee") -> CommitPushed:
        return CommitPushed(artifact=self.artifact.id, commit=sha, model=model)

    def step(self, *events: Event) -> list[tuple[str, str, bool]]:
        """Append events, run the recorder, and return what reached the ledger since the last step."""
        before = len(self.ledger.calls())
        self.h.board.append(list(events))
        self.recorder.step()
        return [(c.kind.value, c.model_id, c.success) for c in self.ledger.calls()[before:]]


class TestProducerOutcomes:
    def test_failed_attempts_fail_and_a_promoted_commit_succeeds_once(self, tmp_path: Path) -> None:
        r = Recording(tmp_path)
        failed = AttemptFailed(
            artifact=r.artifact.id, team=Samples.EXECUTOR, model="small", reason="checks fail"
        )
        routed = r.routed(Samples.EXECUTOR, CallKind.PRODUCE)
        assert r.step(routed, failed, r.commit("large")) == [("produce", SMALL, False)]  # commit undecided
        assert r.step(r.promoted()) == [("produce", LARGE, True)]
        assert r.step() == []  # never recorded twice
        recorded = [e for e in r.h.board.current().history(r.artifact.id) if isinstance(e, OutcomeRecorded)]
        assert [(e.team, e.call_kind, e.success) for e in recorded] == [
            (Samples.EXECUTOR, CallKind.PRODUCE, False),
            (Samples.EXECUTOR, CallKind.PRODUCE, True),
        ]

    def test_each_attempt_is_recorded_with_its_own_cost(self, tmp_path: Path) -> None:
        r = Recording(tmp_path)

        def spent(model: str, usd: float) -> Spent:
            return Spent(
                by=Samples.EXECUTOR,
                model=model,
                input_tokens=1,
                output_tokens=1,
                usd=usd,
                priced=True,
                artifact=r.artifact.id,
            )

        failed = AttemptFailed(
            artifact=r.artifact.id, team=Samples.EXECUTOR, model="small", reason="checks fail"
        )
        routed = r.routed(Samples.EXECUTOR, CallKind.PRODUCE)
        r.step(routed, spent("small", 0.5), spent("small", 0.25), failed)
        r.step(spent("large", 0.3), r.commit("large"), r.promoted())
        assert [(c.model_id, c.success, c.usd) for c in r.ledger.calls()] == [
            (SMALL, False, 0.75),
            (LARGE, True, 0.3),
        ]

    def test_a_rejected_reworked_or_escalated_commit_fails(self, tmp_path: Path) -> None:
        r = Recording(tmp_path)
        r.step(r.routed(Samples.EXECUTOR, CallKind.PRODUCE))
        assert r.step(r.commit("small", "c1"), r.rejected()) == [("produce", SMALL, False)]
        assert r.step(r.commit("small", "c2"), r.commit("large", "c3")) == [("produce", SMALL, False)]
        escalated = Escalated(artifact=r.artifact.id, reason="checks still fail")
        assert r.step(escalated) == [("produce", LARGE, False)]

    def test_a_commit_handed_back_to_be_split_failed(self, tmp_path: Path) -> None:
        r = Recording(tmp_path)
        back = Unbuildable(artifact=r.artifact.id, team=Samples.EXECUTOR, reason="checks still fail")
        routed = r.routed(Samples.EXECUTOR, CallKind.PRODUCE)
        assert r.step(routed, r.commit("large"), back) == [("produce", LARGE, False)]
        assert r.step(r.promoted()) == []  # the umbrella's promotion later is not this model's success

    def test_repair_rounds_are_recorded_as_repairs_by_whether_the_checks_passed(self, tmp_path: Path) -> None:
        r = Recording(tmp_path)
        routed = r.routed(Samples.EXECUTOR, CallKind.PRODUCE)

        def repaired(passed: bool) -> Repaired:
            return Repaired(artifact=r.artifact.id, team=Samples.EXECUTOR, model="small", passed=passed)

        assert r.step(routed, repaired(False), repaired(True)) == [
            ("repair", SMALL, False),
            ("repair", SMALL, True),
        ]

    def test_a_rejection_for_conflicts_alone_is_not_the_models_failure(self, tmp_path: Path) -> None:
        r = Recording(tmp_path)
        conflict = Samples.finding().model_copy(update={"kind": FindingKind.CONFLICT})
        rejected = Rejected(
            artifact=r.artifact.id, level=Samples.RAW, findings=(conflict,), gate_decision=None
        )
        merged = CommitPushed(artifact=r.artifact.id, commit="c2")  # the base merged in by plain code
        routed = r.routed(Samples.EXECUTOR, CallKind.PRODUCE)
        assert r.step(routed, r.commit("small", "c1"), rejected, merged) == []
        assert r.step(r.promoted()) == [("produce", SMALL, True)]

    def test_nothing_without_a_profile_a_router_or_for_reviews(self, tmp_path: Path) -> None:
        r = Recording(tmp_path)
        other = r.h.create(ArtifactId("a-0002"))
        unprofiled = Routed(
            artifact=other.id,
            team=Samples.EXECUTOR,
            model="small",
            model_id=SMALL,
            difficulty=None,
            decision=None,
            acted=False,
        )
        commit = CommitPushed(artifact=other.id, commit="c1", model="small")
        review = r.routed(TeamName("red_team"), CallKind.CRITIQUE)
        assert r.step(unprofiled, commit, r.promoted(other.id), review) == []


class TestPlanOutcomes:
    def split(self, r: Recording) -> list[Event]:
        factory = ArtifactFactory(r.h.board, Harness.LADDER)
        children = [factory.child(r.artifact, i, f"part {i}", PLANNER) for i in (1, 2)]
        sized = Sized(artifact=r.artifact.id, fits=False, children=tuple(c.id for c in children))
        return [*(ArtifactCreated(artifact=c) for c in children), sized]

    def test_a_split_succeeds_when_the_umbrella_is_promoted(self, tmp_path: Path) -> None:
        r = Recording(tmp_path)
        assert r.step(r.routed(PLANNER, CallKind.PLAN), *self.split(r)) == []
        assert r.step(r.promoted()) == [("plan", SMALL, True)]

    def test_a_split_fails_when_a_child_is_escalated_or_the_planner_gives_up(self, tmp_path: Path) -> None:
        r = Recording(tmp_path)
        events = self.split(r)
        escalated = Escalated(artifact=ArtifactId(f"{r.artifact.id}.1"), reason="stuck")
        assert r.step(r.routed(PLANNER, CallKind.PLAN), *events, escalated) == [("plan", SMALL, False)]
        gave_up = Recording(tmp_path / "gave-up")
        stopped = Escalated(artifact=gave_up.artifact.id, reason="decomposition failed")
        assert gave_up.step(gave_up.routed(PLANNER, CallKind.PLAN), stopped) == [("plan", SMALL, False)]

    def test_a_comparison_settles_plans_from_what_was_delivered(self, tmp_path: Path) -> None:
        r = Recording(tmp_path, settle_plans=False)
        assert r.step(r.routed(PLANNER, CallKind.PLAN), *self.split(r), r.promoted()) == []
        assert r.recorder.settle_plans(r.h.board.current(), success=False) == 1
        assert [(c.kind, c.success) for c in r.ledger.calls()] == [(CallKind.PLAN, False)]
        assert r.recorder.settle_plans(r.h.board.current(), success=False) == 0


class MeteredWorker:
    """A scripted worker that reports a fixed cost per run to its meter, as LangChainWorker does on exit."""

    def __init__(self, worker: FakeWorker, meter: Meter, usd: float) -> None:
        self._worker = worker
        self._meter = meter
        self._usd = usd

    async def run[T: BaseModel](
        self, instructions: str, payload: BaseModel, output: type[T], toolbox: Toolbox | None = None
    ) -> T:
        answer = await self._worker.run(instructions, payload, output, toolbox)
        self._meter(Usage(input_tokens=1, model_calls=1, billed_usd=self._usd, billed_calls=1))
        return answer


class TestAttemptCostThroughTheExecutor:
    """Audit A04: a runner publishes an action's events after all of its spend, so attempts are matched to
    their spend by id, not by position on the board."""

    @staticmethod
    async def act(r: Recording, tiers: list[Tier], climb: bool) -> list[Event]:
        worktree = r.h.repository.checkout_artifact(r.artifact)
        team = ExecutorTeam(tiers, [Toolboxes.CHECK], Samples.RAW, attempts=2, climb=climb)
        with SpendScope.on(r.artifact.id):
            return await team.act(r.h.contexts.build(r.artifact.id), worktree)

    @staticmethod
    def worker(ledger: SpendLedger, model: str, usd: float, *steps: Callable[..., ExecutorReport]) -> Tier:
        return Tier(
            model, MeteredWorker(FakeWorker(list(steps)), ledger.meter(Samples.EXECUTOR, model, None), usd)
        )

    async def test_each_model_is_charged_its_own_attempt(self, tmp_path: Path) -> None:
        r = Recording(tmp_path)
        ledger = SpendLedger(r.h.board)
        tiers = [
            self.worker(ledger, "small", 0.5, Steps.write({"greeting.py": "def hello(): ...\n"})),
            self.worker(ledger, "large", 0.3, Steps.write({"greeting.py": "def farewell(name): ...\n"})),
        ]
        r.step(r.routed(Samples.EXECUTOR, CallKind.PRODUCE))
        events = await self.act(r, tiers, climb=True)
        r.step(*events, r.promoted())  # the runner's batch lands after both attempts' spend
        assert [(c.model_id, c.success, c.usd) for c in r.ledger.calls()] == [
            (SMALL, False, 0.5),
            (LARGE, True, 0.3),
        ]

    async def test_two_attempts_on_one_model_are_charged_separately(self, tmp_path: Path) -> None:
        r = Recording(tmp_path)
        ledger = SpendLedger(r.h.board)
        failing, passing = (
            Steps.write({"greeting.py": "def hello(): ...\n"}),
            Steps.write({"greeting.py": "def farewell(name): ...\n"}),
        )
        meter = ledger.meter(Samples.EXECUTOR, "small", None)
        costs = iter([0.5, 0.25])

        class Varying(MeteredWorker):
            async def run[T: BaseModel](
                self, instructions: str, payload: BaseModel, output: type[T], toolbox: Toolbox | None = None
            ) -> T:
                self._usd = next(costs)
                return await super().run(instructions, payload, output, toolbox)

        tier = Tier("small", Varying(FakeWorker([failing, passing]), meter, 0.0))
        r.step(r.routed(Samples.EXECUTOR, CallKind.PRODUCE))
        events = await self.act(r, [tier], climb=False)
        r.step(*events, r.promoted())
        assert [(c.model_id, c.success, c.usd) for c in r.ledger.calls()] == [
            (SMALL, False, 0.5),
            (SMALL, True, 0.25),
        ]
