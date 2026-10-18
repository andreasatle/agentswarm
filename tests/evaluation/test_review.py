from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import BaseModel

from agentswarm.board.spend import Meter
from agentswarm.core.config import ProjectConfig
from agentswarm.core.domain import Usage
from agentswarm.evaluation.compare import Experiment, ExperimentKind
from agentswarm.evaluation.mutation import ChangedLines
from agentswarm.evaluation.review import BugPlanter, ReviewRun
from agentswarm.evaluation.suite import EvalTask, TaskSuite
from agentswarm.llm.workers import Toolbox, Worker
from agentswarm.project import ProjectServices
from agentswarm.teams.analyzer import AnalysisReport, AnalysisToolbox, ReportedProblem
from agentswarm.workspace.sandbox import LocalSandbox
from tests.support.suites import EXAMPLES, PYTEST, GreeterSuite


class ProbingReviewer:
    """A fake reviewer model that probes with the task's own hidden test and reports it if it fails."""

    PROBE = "test_probe.py"

    def __init__(self, probe: str, meter: Meter | None) -> None:
        self._probe = probe
        self._meter = meter

    async def run[T: BaseModel](
        self, instructions: str, payload: BaseModel, output: type[T], toolbox: Toolbox | None = None
    ) -> T:
        assert isinstance(toolbox, AnalysisToolbox)
        toolbox.run_test(self.PROBE, self._probe)
        problem = ReportedProblem(
            title="farewell", detail="wrong greeting", severity="major", test=self.PROBE
        )
        report = AnalysisReport(summary="probed", problems=(problem,))
        usage = Usage(input_tokens=50, output_tokens=10)
        if self._meter is not None:
            self._meter(usage)
        assert isinstance(report, output)
        return report


class ProbingReviewers:
    def __init__(self, probe: str) -> None:
        self._probe = probe

    def worker(
        self, model: str, max_model_calls: int, meter: Meter | None = None, trim: bool = True
    ) -> Worker:
        return ProbingReviewer(self._probe, meter)


class Benchmark:
    """The farewell task, and one setup whose red team reviews what the planted executor merges."""

    def __init__(self, tmp_path: Path) -> None:
        config: dict[str, Any] = {
            "project": "greeter",
            "domain": "software",
            "north_star": "A small greeting library",
            "levels": ["raw", "experimental"],
            "seed": str(EXAMPLES / "seed"),
            "sandbox": {"kind": "local"},
            "models": {"coder": {"model": "ollama:unused"}},
            "checks": {"unit_tests": {"command": list(PYTEST)}},
            "teams": [
                {"use": "executor", "watches": "raw", "model": "coder", "checks": ["unit_tests"]},
                {"use": "red_team", "watches": "experimental", "model": "coder", "checks": ["unit_tests"]},
            ],
            "gates": [{"from": "raw", "to": "experimental", "hard_checks": ["unit_tests"]}],
        }
        (tmp_path / "project.yaml").write_text(yaml.safe_dump(config))
        self.task = GreeterSuite.task("farewell")
        suite = {
            "project": "project.yaml",
            "test_command": list(PYTEST),
            "tasks": [
                {
                    "id": "farewell",
                    "task": self.task.task,
                    "hidden_tests": [str(p) for p in self.task.hidden_tests],
                    "reference": {k: str(v) for k, v in self.task.reference.items()},
                }
            ],
        }
        (tmp_path / "suite.yaml").write_text(yaml.safe_dump(suite))
        experiment = {"suite": "suite.yaml", "setups": {"red": "project.yaml"}, "kind": "review"}
        (tmp_path / "experiment.yaml").write_text(yaml.safe_dump(experiment))
        self.experiment = Experiment.load(tmp_path / "experiment.yaml")
        self.suite = TaskSuite.load(tmp_path / "suite.yaml")
        self.config = ProjectConfig.load(tmp_path / "project.yaml")

    def services(self, setup: str, task: EvalTask) -> ProjectServices:
        probe = "\n\n".join(t.read_text() for t in task.hidden_tests)
        reviewers = ProbingReviewers(probe)
        return ProjectServices(workers=reviewers)


class TestChangedLines:
    def test_lines_added_or_changed_in_the_new_text(self) -> None:
        old = "a\nb\nc\n"
        new = "a\nB\nc\nd\n"
        assert ChangedLines.between(old, new) == {2, 4}


class TestBugPlanter:
    def test_plants_only_bugs_the_hidden_tests_catch(self, tmp_path: Path) -> None:
        b = Benchmark(tmp_path)
        planter = BugPlanter(b.suite, LocalSandbox(), tmp_path / "scratch")
        bugs = planter.plant(b.suite.tasks[0], b.config.seed, 3)
        assert bugs
        for bug in bugs:
            assert planter.caught_by_hidden_tests(b.suite.tasks[0], b.config.seed, bug)
            assert bug.path == "greeter.py" and bug.label.startswith("greeter.py:")


class TestReviewRun:
    async def test_a_reviewer_catches_the_planted_bug_and_stays_quiet_on_clean_code(
        self, tmp_path: Path
    ) -> None:
        b = Benchmark(tmp_path)
        assert b.experiment.kind is ExperimentKind.REVIEW
        run = ReviewRun(b.experiment, tmp_path / "work", b.services)
        report = await run.run()
        control, planted = report.measures
        assert control.bug is None and not control.caught  # the reference is right: the probe passes
        assert planted.bug is not None and planted.finders == ("red_team",)
        summary = report.summaries[0]
        assert (summary.caught, summary.bugs, summary.false_alarms, summary.controls) == (1, 1, 0, 1)
        assert summary.by_team == {"red_team": 1}
        assert planted.tokens == {"ollama:unused": Usage(input_tokens=50, output_tokens=10)}

    def test_setups_without_reviewers_are_refused(self, tmp_path: Path) -> None:
        b = Benchmark(tmp_path)
        config = yaml.safe_load((tmp_path / "project.yaml").read_text())
        config["teams"] = config["teams"][:1]
        (tmp_path / "project.yaml").write_text(yaml.safe_dump(config))
        experiment = Experiment.load(tmp_path / "experiment.yaml")
        with pytest.raises(ValueError, match="no reviewers"):
            ReviewRun(experiment, tmp_path / "work", b.services)
