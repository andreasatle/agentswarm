import random
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import BaseModel

from agentswarm.core.config import JevMode, Price, ProjectConfig
from agentswarm.core.domain import AttemptFailed, Profiled, Rejected, Routed
from agentswarm.judging.deciders import FakeDecider
from agentswarm.judging.judgement import Judgement
from agentswarm.judging.judges import CallProfile, Judge
from agentswarm.judging.policy import Policy
from agentswarm.llm.workers import FakeWorker, Step, Toolbox
from agentswarm.teams.executor import ExecutorReport, ExecutorTeam, Tier, WorktreeToolbox
from agentswarm.teams.routing import Bands, Beliefs, CallKind, Prior, RoutedCall, Router, RoutingLedger
from tests.support.harness import Harness
from tests.support.samples import Profiles, Samples

SMALL, LARGE = "ollama:small", "ollama:large"
AT = datetime(2026, 10, 5, tzinfo=UTC)


class Outcomes:
    """A ledger: the small model succeeds on easy tasks and fails on hard ones; the large one always works."""

    @staticmethod
    def call(model: str, band: int, success: bool) -> RoutedCall:
        return RoutedCall(
            kind=CallKind.PRODUCE, band=band, model_id=model, success=success, source="t", at=AT
        )

    @classmethod
    def ledger(cls) -> RoutingLedger:
        ledger = RoutingLedger(":memory:")
        for _ in range(30):
            ledger.record(cls.call(SMALL, 0, True))
            ledger.record(cls.call(SMALL, 2, False))
            ledger.record(cls.call(LARGE, 0, True))
            ledger.record(cls.call(LARGE, 2, True))
        return ledger


class Routing:
    """A harness with one artifact, and a router whose CallProfile answer is scripted."""

    def __init__(self, tmp_path: Path, simple: float, mode: JevMode, ledger: RoutingLedger | None) -> None:
        tmp_path.mkdir(parents=True, exist_ok=True)
        self.h = Harness(tmp_path)
        self.artifact = self.h.create()
        self.decider = FakeDecider([Profiles.make(simple)])
        judge = Judge(CallProfile)
        policy = Policy(judge)
        self.judgement = Judgement(judge, policy, self.decider, self.h.decisions, mode, self.h.clock)
        self.ledger = ledger
        self.router = self.make((SMALL, LARGE))

    def make(
        self,
        model_ids: tuple[str, ...],
        target: float = 0.8,
        estimates: tuple[float, ...] | None = None,
        explore_usd: float | None = None,
    ) -> Router:
        rng = random.Random(0)
        return Router(
            self.judgement,
            CallKind.PRODUCE,
            model_ids,
            self.ledger,
            target,
            self.h.clock,
            rng=rng,
            estimates=estimates,
            explore_usd=explore_usd,
        )

    def choose(self) -> int:
        ctx = self.h.contexts.build(self.artifact.id)
        return self.router.choose(ctx, "c0ffee").tier


class TestBeliefs:
    def test_counts_outcomes_on_top_of_a_small_guess_per_band(self) -> None:
        beliefs = Beliefs(Outcomes.ledger().calls(), Prior(pooling=0.0))
        easy = beliefs.of(CallKind.PRODUCE, 0, SMALL)
        assert (easy.successes, easy.failures) == (30, 0)
        assert easy.rate == pytest.approx((30 + 1.6) / (30 + 2.0))  # prior: 0.8 of 2 imagined runs
        unknown = beliefs.of(CallKind.CRITIQUE, 1, SMALL)
        assert (unknown.successes, unknown.failures) == (0, 0) and unknown.rate == pytest.approx(0.6)

    def test_draws_spread_widely_without_outcomes_and_settle_with_them(self) -> None:
        beliefs = Beliefs(Outcomes.ledger().calls(), Prior(pooling=0.0))
        rng = random.Random(1)
        fresh = [beliefs.of(CallKind.PRODUCE, 1, SMALL).draw(rng) for _ in range(200)]
        proven = [beliefs.of(CallKind.PRODUCE, 0, SMALL).draw(rng) for _ in range(200)]
        assert max(fresh) - min(fresh) > 0.6
        assert min(proven) > 0.8

    def test_a_band_without_outcomes_starts_from_the_models_record_elsewhere(self) -> None:
        # LARGE: 30 of 30 in bands 0 and 2; SMALL: 30 of 30 in band 0, 0 of 30 in band 2; neither has band 1
        pooled = Beliefs(Outcomes.ledger().calls(), Prior())
        plain = Beliefs(Outcomes.ledger().calls(), Prior(pooling=0.0))
        assert plain.of(CallKind.PRODUCE, 1, LARGE).rate == pytest.approx(0.6)  # every model's guess
        assert pooled.of(CallKind.PRODUCE, 1, LARGE).prior == pytest.approx(
            (4.0 * 61.2 / 62.0, 4.0 * 0.8 / 62.0)  # band 1's guess (0.6 of 2) moved by 60 of 60, worth 4 runs
        )
        assert pooled.of(CallKind.PRODUCE, 1, LARGE).rate > 0.95
        assert (
            0.4 < pooled.of(CallKind.PRODUCE, 1, SMALL).rate < 0.6
        )  # a mixed record elsewhere: no strong lean

    def test_renders_successes_runs_and_rate_per_band(self) -> None:
        table = Beliefs(Outcomes.ledger().calls(), Prior()).render()
        assert "ollama:small" in table and "30/30" in table and " 0/30" in table


class TestRoutingLedger:
    def test_keeps_calls_across_openings_and_filters_by_kind(self, tmp_path: Path) -> None:
        path = tmp_path / "ledger" / "routing.sqlite"
        ledger = RoutingLedger(path)
        ledger.record(Outcomes.call(SMALL, 1, True))
        critique = RoutedCall(
            kind=CallKind.CRITIQUE, band=2, model_id=LARGE, success=False, source="p", at=AT
        )
        ledger.record(critique)
        ledger.close()
        again = RoutingLedger(path)
        assert [c.kind for c in again.calls()] == [CallKind.PRODUCE, CallKind.CRITIQUE]
        assert again.calls(CallKind.CRITIQUE) == [critique]
        assert Bands.of(1.0) == 2 and Bands.of(0.34) == 1


class TestCosts:
    def test_an_old_ledger_gains_the_cost_column_and_keeps_its_calls(self, tmp_path: Path) -> None:
        path = tmp_path / "routing.sqlite"
        old = sqlite3.connect(path)
        old.execute(
            "CREATE TABLE calls (kind TEXT, band INTEGER, model_id TEXT, success INTEGER, attributes TEXT, "
            "source TEXT, at TEXT)"
        )
        old.execute(
            "INSERT INTO calls VALUES ('produce', 0, 'ollama:small', 1, '{}', 't', ?)", (AT.isoformat(),)
        )
        old.commit()
        old.close()
        ledger = RoutingLedger(path)
        ledger.record(Outcomes.call(SMALL, 0, True).model_copy(update={"usd": 0.3}))
        assert [c.usd for c in ledger.calls()] == [None, 0.3]

    def test_a_models_cost_is_measured_with_its_price_estimate_as_one_attempt(self) -> None:
        calls = [Outcomes.call(SMALL, 0, True).model_copy(update={"usd": 0.2}) for _ in range(2)]
        beliefs = Beliefs(calls, Prior())
        assert beliefs.cost(CallKind.PRODUCE, 0, SMALL, estimate=1.0) == pytest.approx(1.4 / 3)
        assert beliefs.cost(CallKind.PRODUCE, 0, LARGE, estimate=1.0) == 1.0  # nothing measured yet

    def test_a_price_gives_an_estimate_and_a_local_model_costs_nothing(self) -> None:
        assert Router.estimate(None) == 0.0
        sol = Router.estimate(Price(input=2.0, output=10.0, cache_read=0.1))
        assert sol == pytest.approx(0.15 * 2.0 + 0.85 * 0.1 + 0.02 * 10.0)


class TestDiscountedBeliefs:
    def test_outcomes_count_less_as_later_calls_come_in_and_a_pause_changes_nothing(self) -> None:
        old = RoutedCall(kind=CallKind.PRODUCE, band=0, model_id=SMALL, success=False, source="t", at=AT)
        later = [
            RoutedCall(kind=CallKind.PRODUCE, band=2, model_id=LARGE, success=True, source="t", at=AT)
            for _ in range(10)
        ]
        review = RoutedCall(kind=CallKind.CRITIQUE, band=0, model_id=LARGE, success=True, source="t", at=AT)
        beliefs = Beliefs([old, *later, review], Prior(), half_life_calls=10)
        belief = beliefs.of(CallKind.PRODUCE, 0, SMALL)
        assert (belief.failures, belief.seen) == (0.5, 1)  # ten later builds; the review is another kind
        faded = Beliefs([old, *later * 10], Prior(), half_life_calls=10).of(CallKind.PRODUCE, 0, SMALL)
        assert faded.failures < 0.001 and faded.rate == pytest.approx(Prior().rates[0], abs=0.01)


class TestRouter:
    def test_starts_easy_tasks_on_the_small_model_and_hard_ones_on_the_large(self, tmp_path: Path) -> None:
        easy = Routing(tmp_path / "easy", simple=0.9, mode=JevMode.ACTIVE, ledger=Outcomes.ledger())
        hard = Routing(tmp_path / "hard", simple=0.1, mode=JevMode.ACTIVE, ledger=Outcomes.ledger())
        assert easy.choose() == 0
        assert hard.choose() == 1

    def test_shadow_or_no_ledger_keeps_the_cheapest_tier_and_records_what_jev_said(
        self, tmp_path: Path
    ) -> None:
        shadow = Routing(tmp_path / "shadow", simple=0.1, mode=JevMode.SHADOW, ledger=Outcomes.ledger())
        ctx = shadow.h.contexts.build(shadow.artifact.id)
        choice = shadow.router.choose(ctx, "c0ffee")
        assert choice.profile is not None and choice.fresh
        assert (choice.tier, choice.profile.difficulty, choice.acted) == (0, 0.9, False)
        assert set(choice.profile.attributes) == {
            "simple",
            "design",
            "underspecified",
            "algorithmic",
            "several_files",
        }
        no_ledger = Routing(tmp_path / "none", simple=0.1, mode=JevMode.ACTIVE, ledger=None)
        assert no_ledger.choose() == 0

    def test_asks_jev_once_per_artifact_whichever_team_asks_first(self, tmp_path: Path) -> None:
        r = Routing(tmp_path, simple=0.1, mode=JevMode.ACTIVE, ledger=Outcomes.ledger())
        ctx = r.h.contexts.build(r.artifact.id)
        first = r.router.choose(ctx, "c0ffee")
        assert first.profile is not None
        r.h.board.append([first.profile])
        again = r.make((SMALL, LARGE)).choose(r.h.contexts.build(r.artifact.id), "c0ffee")
        assert again.profile == first.profile and not again.fresh
        assert len(r.decider.calls) == 1

    def test_ranks_by_expected_cost_per_success_not_by_the_config_order(self, tmp_path: Path) -> None:
        ledger = RoutingLedger(":memory:")
        for n in range(20):  # the first model is cheaper per token but reads more and fails more
            ledger.record(Outcomes.call(SMALL, 2, n % 2 == 0).model_copy(update={"usd": 1.65}))
            ledger.record(Outcomes.call(LARGE, 2, True).model_copy(update={"usd": 0.24}))
        r = Routing(tmp_path, simple=0.1, mode=JevMode.ACTIVE, ledger=ledger)
        router = r.make((SMALL, LARGE), target=0.3)
        assert router.rank(0.9) == [1, 0]
        assert router.rank(0.9, failed=[1]) == [0]  # the cascade, after the best one failed
        assert router.rank(0.9, failed=[0, 1]) == []

    def test_below_the_target_the_likeliest_model_comes_first_however_cheap_the_others(
        self, tmp_path: Path
    ) -> None:
        ledger = RoutingLedger(":memory:")
        for n in range(20):
            ledger.record(Outcomes.call(SMALL, 2, False).model_copy(update={"usd": 0.0}))  # free, never works
            ledger.record(Outcomes.call(LARGE, 2, n % 2 == 0).model_copy(update={"usd": 0.9}))
        r = Routing(tmp_path, simple=0.1, mode=JevMode.ACTIVE, ledger=ledger)
        assert r.make((SMALL, LARGE), target=0.95).rank(0.9) == [1, 0]

    def test_an_expensive_model_not_yet_measured_starts_nothing_while_another_is_left(
        self, tmp_path: Path
    ) -> None:
        ledger = RoutingLedger(":memory:")
        for _ in range(20):
            ledger.record(Outcomes.call(SMALL, 2, True).model_copy(update={"usd": 0.5}))
        r = Routing(tmp_path, simple=0.1, mode=JevMode.ACTIVE, ledger=ledger)
        router = r.make((SMALL, "ollama:new"), target=0.3, estimates=(0.5, 0.01), explore_usd=1.0)
        assert router.rank(0.9)[0] == 1  # cheap and unmeasured: tried
        dear = r.make((SMALL, "ollama:new"), target=0.3, estimates=(0.5, 5.0), explore_usd=1.0)
        assert dear.rank(0.9) == [0, 1]  # dear and unmeasured: only when nothing else is left

    def test_records_each_outcome_with_its_band_and_model(self, tmp_path: Path) -> None:
        r = Routing(tmp_path, simple=0.1, mode=JevMode.ACTIVE, ledger=RoutingLedger(":memory:"))
        profile = r.router.choose(r.h.contexts.build(r.artifact.id), "c0ffee").profile
        assert profile is not None
        assert r.router.record(LARGE, profile, success=True)
        (call,) = r.ledger.calls() if r.ledger is not None else []
        assert (call.model_id, call.band, call.success, call.source) == (LARGE, 2, True, "project")


class Recorder:
    """A fake model that writes a file, notes it was used, and reports done."""

    def __init__(self, name: str, used: list[str]) -> None:
        self._name = name
        self._used = used

    def step(self) -> Step[BaseModel]:
        def work(toolbox: Toolbox | None) -> ExecutorReport:
            assert isinstance(toolbox, WorktreeToolbox)
            self._used.append(self._name)
            toolbox.write_file(f"{self._name}.txt", f"{len(self._used)}\n")
            return ExecutorReport(summary=f"by {self._name}", complete=True)

        return work


class TestRoutedExecutor:
    async def test_starts_on_the_routed_tier_and_steps_up_after_a_rejection(self, tmp_path: Path) -> None:
        used: list[str] = []
        r = Routing(tmp_path, simple=0.1, mode=JevMode.ACTIVE, ledger=Outcomes.ledger())
        cheap, middle, strong = Recorder("small", used), Recorder("medium", used), Recorder("large", used)
        tiers = [
            Tier("small", FakeWorker([cheap.step()])),
            Tier("medium", FakeWorker([middle.step()])),
            Tier("large", FakeWorker([strong.step(), strong.step()])),
        ]
        router = r.make((SMALL, "ollama:medium", LARGE))
        executor = ExecutorTeam(tiers, [], Samples.RAW, router=router)
        runner = r.h.runner(executor)
        assert await runner.step() == 1
        history = r.h.board.current().history(r.artifact.id)
        routed = [e for e in history if isinstance(e, Routed)]
        assert len(routed) == 1 and routed[0].model == "large" and routed[0].acted
        assert routed[0].attributes["simple"] == 0.1
        assert used == ["large"]  # hard task: past the small model, whose record on hard tasks is poor
        rejected = Rejected(
            artifact=r.artifact.id, level=Samples.RAW, findings=(Samples.finding(),), gate_decision=None
        )
        r.h.board.append([rejected])
        assert await runner.step() == 1
        history = r.h.board.current().history(r.artifact.id)
        assert sum(isinstance(e, Routed) for e in history) == 1  # routed once per artifact and team
        assert sum(isinstance(e, Profiled) for e in history) == 1  # Jev is asked once per artifact
        assert used == ["large", "large"]  # stays on the strongest after a rejection


class TestCascade:
    async def test_a_reopened_step_skips_the_model_that_failed_it_however_well_it_ranks(
        self, tmp_path: Path
    ) -> None:
        ledger = RoutingLedger(":memory:")
        for _ in range(20):  # large is the better buy: it would start the step
            ledger.record(Outcomes.call(SMALL, 2, False).model_copy(update={"usd": 1.0}))
            ledger.record(Outcomes.call(LARGE, 2, True).model_copy(update={"usd": 0.2}))
        r = Routing(tmp_path, simple=0.1, mode=JevMode.ACTIVE, ledger=ledger)
        used: list[str] = []
        tiers = [
            Tier("small", FakeWorker([Recorder("small", used).step()])),
            Tier("large", FakeWorker([Recorder("large", used).step()])),
        ]
        executor = ExecutorTeam(tiers, [], Samples.RAW, router=r.make((SMALL, LARGE)), climb=True)
        failed = AttemptFailed(
            artifact=r.artifact.id, team=executor.name, model="large", reason="checks fail"
        )
        r.h.board.append([failed])
        assert await r.h.runner(executor).step() == 1
        assert used == ["small"]


class TestRoutingConfig:
    def test_a_relative_ledger_path_is_resolved_against_the_config(self, tmp_path: Path) -> None:
        text = (
            "project: p\ndomain: software\nnorth_star: n\nlevels: [raw, experimental]\n"
            "models: {coder: {model: 'ollama:x'}}\n"
            "teams: [{use: executor, watches: raw, model: coder, routing: {ledger: data/routing.sqlite}}]\n"
            "gates: [{from: raw, to: experimental}]\n"
        )
        (tmp_path / "project.yaml").write_text(text)
        config = ProjectConfig.load(tmp_path / "project.yaml")
        routing = config.teams[0].routing
        assert routing is not None and routing.ledger == (tmp_path / "data" / "routing.sqlite").resolve()
        assert routing.target == 0.8
