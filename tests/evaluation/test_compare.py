from pathlib import Path

import pytest
import yaml
from pydantic import BaseModel

from agentswarm.board.spend import Meter
from agentswarm.core.domain import Usage
from agentswarm.evaluation.compare import (
    ComparisonReport,
    ComparisonRun,
    Experiment,
    RunMeasure,
    RunOrder,
    SetupSummary,
)
from agentswarm.evaluation.suite import EvalTask
from agentswarm.judging.deciders import FakeDecider
from agentswarm.llm.workers import Toolbox, Worker
from agentswarm.project import ProjectServices
from agentswarm.teams.executor import ExecutorReport, WorktreeToolbox
from agentswarm.teams.routing import CallKind, RoutingLedger
from tests.support.samples import Profiles
from tests.support.suites import TinySuite


class FarewellWorker:
    """A fake executor model that writes farewell() with the given word and reports the tokens it 'used'."""

    def __init__(self, word: str, meter: Meter | None) -> None:
        self._word = word
        self._meter = meter

    async def run[T: BaseModel](
        self, instructions: str, payload: BaseModel, output: type[T], toolbox: Toolbox | None = None
    ) -> T:
        assert isinstance(toolbox, WorktreeToolbox)
        source = (
            'def hello(name: str) -> str:\n    return f"Hello, {name}!"\n\n\n'
            f'def farewell(name: str) -> str:\n    return f"{self._word}, {{name}}!"\n'
        )
        toolbox.write_file("greeter.py", source)
        usage = Usage(input_tokens=100, output_tokens=20)
        if self._meter is not None:
            self._meter(usage)
        report = ExecutorReport(summary="add farewell", complete=True)
        assert isinstance(report, output)
        return report


class FarewellWorkers:
    def __init__(self, word: str) -> None:
        self._word = word

    def worker(
        self, model: str, max_model_calls: int, meter: Meter | None = None, trim: bool = True
    ) -> Worker:
        return FarewellWorker(self._word, meter)


class Comparison:
    """The tiny suite under two setups sharing one config: 'careful' writes the right word, 'sloppy' not."""

    def __init__(self, tmp_path: Path, tasks: tuple[str, ...] = ()) -> None:
        TinySuite.write(tmp_path)
        experiment: dict[str, object] = {
            "suite": "suite.yaml",
            "setups": {"careful": "project.yaml", "sloppy": "project.yaml"},
        }
        if tasks:
            experiment["tasks"] = list(tasks)
        path = tmp_path / "experiment.yaml"
        text = yaml.safe_dump(experiment)
        path.write_text(text)
        self.experiment = Experiment.load(path)
        self.run = ComparisonRun(self.experiment, tmp_path / "work", self.services)

    @staticmethod
    def services(setup: str, task: EvalTask) -> ProjectServices:
        word = "Goodbye" if setup == "careful" else "Bye"
        workers = FarewellWorkers(word)
        return ProjectServices(workers=workers)


class TestExperiment:
    def test_paths_are_resolved_against_the_experiment_file(self, tmp_path: Path) -> None:
        c = Comparison(tmp_path)
        assert c.experiment.suite == (tmp_path / "suite.yaml").resolve()
        assert set(c.experiment.setups) == {"careful", "sloppy"}

    def test_missing_files_and_unknown_tasks_are_errors(self, tmp_path: Path) -> None:
        TinySuite.write(tmp_path)
        path = tmp_path / "experiment.yaml"
        path.write_text(yaml.safe_dump({"suite": "suite.yaml", "setups": {"x": "nowhere.yaml"}}))
        with pytest.raises(ValueError, match="missing files"):
            Experiment.load(path)
        with pytest.raises(ValueError, match="not in the suite"):
            Comparison(tmp_path, tasks=("no-such-task",))


class TestComparisonRun:
    def test_runs_one_setup_at_a_time_unless_asked_to_go_task_by_task(self, tmp_path: Path) -> None:
        c = Comparison(tmp_path)
        by_setup = [(setup, task.id) for setup, task in c.run.schedule()]
        assert by_setup == [
            ("careful", "done"),
            ("careful", "half-done"),
            ("sloppy", "done"),
            ("sloppy", "half-done"),
        ]
        by_task_experiment = c.experiment.model_copy(update={"order": RunOrder.BY_TASK})
        by_task_run = ComparisonRun(by_task_experiment, tmp_path / "work", Comparison.services)
        by_task = [(setup, task.id) for setup, task in by_task_run.schedule()]
        assert by_task == [
            ("careful", "done"),
            ("sloppy", "done"),
            ("careful", "half-done"),
            ("sloppy", "half-done"),
        ]

    async def test_scores_what_each_setup_delivered_and_counts_its_tokens(self, tmp_path: Path) -> None:
        c = Comparison(tmp_path, tasks=("done",))
        seen: list[RunMeasure] = []
        report = await c.run.run(seen.append)
        by_setup = {m.setup: m for m in report.measures}
        assert [m.setup for m in seen] == [
            "careful",
            "sloppy",
        ]  # reported as they finish, one setup at a time
        assert by_setup["careful"].solved is True
        assert (
            by_setup["sloppy"].solved is False
        )  # merged (its unit tests pass) but wrong: hidden tests catch it
        assert by_setup["careful"].tokens == {"ollama:unused": Usage(input_tokens=100, output_tokens=20)}
        assert by_setup["careful"].model_runs == 1
        careful, sloppy = report.summaries
        assert (careful.setup, careful.solved, careful.scored) == ("careful", 1, 1)
        assert (sloppy.solved, sloppy.tokens_per_solved) == (0, None)
        assert careful.tokens_per_solved == 120

    async def test_writes_every_run_and_the_summary(self, tmp_path: Path) -> None:
        c = Comparison(tmp_path, tasks=("done",))
        report = await c.run.run()
        path = tmp_path / "results.json"
        report.write(path)
        text = path.read_text()
        loaded = ComparisonReport.model_validate_json(text)
        assert loaded == report
        assert report.render().splitlines()[0] == ComparisonReport.HEADER

    async def test_a_used_workdir_is_refused(self, tmp_path: Path) -> None:
        c = Comparison(tmp_path, tasks=("done",))
        await c.run.run()
        again = ComparisonRun(c.experiment, tmp_path / "work", Comparison.services)
        with pytest.raises(FileExistsError):
            await again.run()


class TestSetupSummary:
    def test_runs_whose_tests_could_not_run_are_left_out_of_the_rate(self) -> None:
        def measure(solved: bool | None) -> RunMeasure:
            tokens = {"m": Usage(input_tokens=10, output_tokens=0)}
            return RunMeasure(
                setup="s", task="t", solved=solved, model_runs=1, tokens=tokens, seconds=1.0, escalations=0
            )

        summary = SetupSummary.of("s", [measure(True), measure(False), measure(None)])
        assert (summary.solved, summary.scored, summary.runs) == (1, 2, 3)
        assert summary.tokens_per_task == 10 and summary.tokens_per_solved == 30


class TestRoutingFromComparisons:
    async def test_routed_runs_teach_the_routing_ledger(self, tmp_path: Path) -> None:
        TinySuite.write(tmp_path)
        config_path = tmp_path / "project.yaml"
        config = yaml.safe_load(config_path.read_text())
        config["teams"][0]["routing"] = {"ledger": "routing.sqlite"}  # relative to the project file
        config_path.write_text(yaml.safe_dump(config))
        experiment_path = tmp_path / "experiment.yaml"
        experiment_text = yaml.safe_dump({"suite": "suite.yaml", "setups": {"careful": "project.yaml"}})
        experiment_path.write_text(experiment_text)
        experiment = Experiment.load(experiment_path)

        def services(setup: str, task: EvalTask) -> ProjectServices:
            profile = Profiles.make(simple=0.75)
            decider = FakeDecider([profile])
            workers = FarewellWorkers("Goodbye")
            return ProjectServices(workers=workers, decider=decider)

        workdir = tmp_path / "work"
        await ComparisonRun(experiment, workdir, services).run()

        ledger = RoutingLedger(tmp_path / "routing.sqlite")
        calls = ledger.calls()
        assert [(c.kind, c.band, c.model_id, c.success) for c in calls] == [
            (CallKind.PRODUCE, 0, "ollama:unused", True),
            (CallKind.PRODUCE, 0, "ollama:unused", True),
        ]
        assert calls[0].attributes["simple"] == 0.75 and calls[0].source.startswith("project:")
