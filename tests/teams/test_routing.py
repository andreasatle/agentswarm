from pathlib import Path

import pytest
from pydantic import BaseModel

from agentswarm.core.config import JevMode, ProjectConfig
from agentswarm.core.domain import Rejected, Routed
from agentswarm.jev.api import NoulAnswer
from agentswarm.judging.deciders import FakeDecider
from agentswarm.judging.judgement import Judgement
from agentswarm.judging.judges import Judge, TaskDifficulty
from agentswarm.judging.policy import Policy
from agentswarm.llm.workers import FakeWorker, Step, Toolbox
from agentswarm.teams.executor import ExecutorReport, ExecutorTeam, Tier, WorktreeToolbox
from agentswarm.teams.routing import ModelCurves, Observation, Router
from tests.support.harness import Harness
from tests.support.samples import Samples

SMALL, LARGE = "ollama:small", "ollama:large"


class Curves:
    """The small model solves easy tasks only; the large one solves everything."""

    @staticmethod
    def make() -> ModelCurves:
        observations: list[Observation] = []
        for difficulty, small_ok in ((0.1, True), (0.5, False), (0.9, False)):
            for _ in range(8):
                observations.append(Observation(model_id=SMALL, difficulty=difficulty, success=small_ok))
                observations.append(Observation(model_id=LARGE, difficulty=difficulty, success=True))
        return ModelCurves.fit(observations, bands=3)


class Routing:
    """A harness with one artifact, and a router whose difficulty answer is scripted."""

    def __init__(self, tmp_path: Path, simple: float, mode: JevMode, curves: ModelCurves | None) -> None:
        tmp_path.mkdir(parents=True, exist_ok=True)
        self.h = Harness(tmp_path)
        self.artifact = self.h.create()
        answer = TaskDifficulty(simple=NoulAnswer(noul=simple))
        self.decider = FakeDecider([answer])
        judge = Judge(TaskDifficulty)
        policy = Policy(judge)
        self.judgement = Judgement(judge, policy, self.decider, self.h.decisions, mode, self.h.clock)
        self.router = Router(self.judgement, "route:executor", (SMALL, LARGE), curves, target=0.8)

    def choose(self) -> int:
        ctx = self.h.contexts.build(self.artifact.id)
        choice = self.router.choose(ctx, "c0ffee")
        return choice.tier


class TestModelCurves:
    def test_fits_success_per_band_and_smooths_small_samples(self) -> None:
        curves = Curves.make()
        small = curves.curves[SMALL]
        assert [(b.successes, b.runs) for b in small.bands] == [(8, 8), (0, 8), (0, 8)]
        assert curves.predicted(SMALL, 0.05) == pytest.approx(9 / 10)  # (8 + 1) / (8 + 2)
        assert curves.predicted(SMALL, 1.0) == pytest.approx(1 / 10)  # 1.0 belongs to the last band
        assert curves.predicted("ollama:unknown", 0.5) is None

    def test_no_crossings_when_the_stronger_model_is_never_clearly_worse(self) -> None:
        assert Curves.make().crossings() == []

    def test_reports_curves_that_cross(self) -> None:
        observations: list[Observation] = []
        for _ in range(4):
            observations.append(
                Observation(model_id=SMALL, difficulty=0.1, success=True)
            )  # strings: small wins
            observations.append(Observation(model_id=LARGE, difficulty=0.1, success=False))
            for difficulty in (0.5, 0.9):
                observations.append(Observation(model_id=SMALL, difficulty=difficulty, success=False))
                observations.append(Observation(model_id=LARGE, difficulty=difficulty, success=True))
        crossings = ModelCurves.fit(observations).crossings()
        assert len(crossings) == 1 and crossings[0].startswith(f"{SMALL} beats {LARGE}")

    def test_round_trips_through_json(self, tmp_path: Path) -> None:
        curves = Curves.make()
        path = tmp_path / "curves.json"
        curves.write(path)
        assert ModelCurves.load(path) == curves


class TestRouter:
    def test_active_routes_easy_tasks_to_the_small_model_and_hard_ones_to_the_large(
        self, tmp_path: Path
    ) -> None:
        easy = Routing(tmp_path / "easy", simple=0.9, mode=JevMode.ACTIVE, curves=Curves.make())
        hard = Routing(tmp_path / "hard", simple=0.2, mode=JevMode.ACTIVE, curves=Curves.make())
        assert easy.choose() == 0
        assert hard.choose() == 1

    def test_shadow_or_no_curves_keeps_the_cheapest_tier_and_records_the_difficulty(
        self, tmp_path: Path
    ) -> None:
        shadow = Routing(tmp_path / "shadow", simple=0.2, mode=JevMode.SHADOW, curves=Curves.make())
        ctx = shadow.h.contexts.build(shadow.artifact.id)
        choice = shadow.router.choose(ctx, "c0ffee")
        assert (choice.tier, choice.difficulty, choice.acted) == (0, 0.8, False)
        no_curves = Routing(tmp_path / "none", simple=0.2, mode=JevMode.ACTIVE, curves=None)
        assert no_curves.choose() == 0

    def test_no_model_good_enough_starts_on_the_strongest(self, tmp_path: Path) -> None:
        observations = [Observation(model_id=m, difficulty=0.9, success=False) for m in (SMALL, LARGE) * 4]
        r = Routing(tmp_path, simple=0.1, mode=JevMode.ACTIVE, curves=ModelCurves.fit(observations))
        assert r.choose() == 1


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
    async def test_starts_on_the_routed_tier_and_steps_up_from_there(self, tmp_path: Path) -> None:
        used: list[str] = []
        r = Routing(tmp_path, simple=0.2, mode=JevMode.ACTIVE, curves=Curves.make())
        cheap = Recorder("small", used)
        middle = Recorder("medium", used)
        strong = Recorder("large", used)
        tiers = [
            Tier("small", FakeWorker([cheap.step()])),
            Tier("medium", FakeWorker([middle.step()])),
            Tier("large", FakeWorker([strong.step(), strong.step()])),
        ]
        router = Router(r.judgement, "route:executor", (SMALL, "ollama:medium", LARGE), Curves.make(), 0.8)
        executor = ExecutorTeam(tiers, [], Samples.RAW, router=router)
        runner = r.h.runner(executor)
        assert await runner.step() == 1
        history = r.h.board.current().history(r.artifact.id)
        routed = [e for e in history if isinstance(e, Routed)]
        assert len(routed) == 1 and routed[0].model == "large" and routed[0].acted
        assert used == ["large"]  # hard task: straight to the strongest tier, no failed cheap attempt first
        r.h.board.append(
            [
                Rejected(
                    artifact=r.artifact.id,
                    level=Samples.RAW,
                    findings=(Samples.finding(),),
                    gate_decision=None,
                )
            ]
        )
        assert await runner.step() == 1
        history = r.h.board.current().history(r.artifact.id)
        assert sum(isinstance(e, Routed) for e in history) == 1  # routed once per artifact
        assert used == ["large", "large"]  # stays on the strongest after a rejection


class TestRoutingConfig:
    def test_a_relative_curves_path_is_resolved_against_the_config(self, tmp_path: Path) -> None:
        text = (
            "project: p\ndomain: software\nnorth_star: n\nlevels: [raw, experimental]\n"
            "models: {coder: {model: 'ollama:x'}}\n"
            "teams: [{use: executor, watches: raw, model: coder, routing: {curves: fits/curves.json}}]\n"
            "gates: [{from: raw, to: experimental}]\n"
        )
        (tmp_path / "project.yaml").write_text(text)
        config = ProjectConfig.load(tmp_path / "project.yaml")
        routing = config.teams[0].routing
        assert routing is not None and routing.curves == (tmp_path / "fits" / "curves.json").resolve()
        assert routing.target == 0.8
