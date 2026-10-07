import random
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import BaseModel

from agentswarm.core.config import JevMode, ProjectConfig
from agentswarm.core.domain import Profiled, Rejected, Routed
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

    def make(self, model_ids: tuple[str, ...]) -> Router:
        rng = random.Random(0)
        return Router(
            self.judgement,
            CallKind.PRODUCE,
            model_ids,
            self.ledger,
            0.8,
            self.h.clock,
            rng=rng,
        )

    def choose(self) -> int:
        ctx = self.h.contexts.build(self.artifact.id)
        return self.router.choose(ctx, "c0ffee").tier


class TestBeliefs:
    def test_counts_outcomes_on_top_of_a_small_guess_per_band(self) -> None:
        beliefs = Beliefs(Outcomes.ledger().calls(), Prior())
        easy = beliefs.of(CallKind.PRODUCE, 0, SMALL)
        assert (easy.successes, easy.failures) == (30, 0)
        assert easy.rate == pytest.approx((30 + 1.6) / (30 + 2.0))  # prior: 0.8 of 2 imagined runs
        unknown = beliefs.of(CallKind.CRITIQUE, 1, SMALL)
        assert (unknown.successes, unknown.failures) == (0, 0) and unknown.rate == pytest.approx(0.6)

    def test_draws_spread_widely_without_outcomes_and_settle_with_them(self) -> None:
        beliefs = Beliefs(Outcomes.ledger().calls(), Prior())
        rng = random.Random(1)
        fresh = [beliefs.of(CallKind.PRODUCE, 1, SMALL).draw(rng) for _ in range(200)]
        proven = [beliefs.of(CallKind.PRODUCE, 0, SMALL).draw(rng) for _ in range(200)]
        assert max(fresh) - min(fresh) > 0.6
        assert min(proven) > 0.8

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
