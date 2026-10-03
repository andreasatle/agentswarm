import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

from agentswarm.core.config import JevMode
from agentswarm.evaluation.faults import Variant
from agentswarm.evaluation.mutation import MutationTesting
from agentswarm.evaluation.run import EvaluationRun
from agentswarm.evaluation.suite import EvalTask, HiddenTests, TaskSuite
from agentswarm.judging.deciders import FakeDecider
from agentswarm.judging.decisions import InMemoryDecisions, Outcomes, OutcomeSource
from agentswarm.llm.workers import FakeWorker, FakeWorkers, Toolbox
from agentswarm.project import ProjectServices
from agentswarm.teams.executor import ExecutorReport, WorktreeToolbox
from agentswarm.workspace.sandbox import LocalSandbox
from tests.support.paths import RepoPaths
from tests.support.samples import Judged

EXAMPLES = RepoPaths.GREETER
GREETER_SUITE = EXAMPLES / "evaluation" / "suite.yaml"
EXECUTOR_ONLY = (Variant.EXECUTOR,)
PYTEST = (sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider")


class GreeterSuite:
    """Runs one task's hidden tests against a given greeter.py, on the host."""

    SUITE = TaskSuite.load(GREETER_SUITE)

    @classmethod
    def hidden_tests_pass(cls, tmp_path: Path, task: EvalTask, greeter: str) -> bool:
        work = tmp_path / "work"
        shutil.copytree(EXAMPLES / "seed", work)
        (work / "greeter.py").write_text(greeter)
        (work / HiddenTests.DIRECTORY).mkdir()
        for test in task.hidden_tests:
            shutil.copy(test, work / HiddenTests.DIRECTORY / test.name)
        return LocalSandbox().run((*PYTEST, HiddenTests.DIRECTORY), work, 60).passed

    @classmethod
    def ids(cls) -> list[str]:
        return [t.id for t in cls.SUITE.tasks]

    @classmethod
    def task(cls, id: str) -> EvalTask:
        return next(t for t in cls.SUITE.tasks if t.id == id)


class TestGreeterSuite:
    """Hidden tests are ground truth, so they are checked: right answers pass, the untouched seed fails."""

    @pytest.mark.parametrize("task_id", GreeterSuite.ids())
    def test_reference_solution_passes_and_seed_fails(self, tmp_path: Path, task_id: str) -> None:
        task = GreeterSuite.task(task_id)
        reference = (EXAMPLES / "evaluation" / "reference" / f"{task_id}.py").read_text()
        seed = (EXAMPLES / "seed" / "greeter.py").read_text()
        assert GreeterSuite.hidden_tests_pass(tmp_path / "ref", task, reference)
        assert not GreeterSuite.hidden_tests_pass(tmp_path / "seed", task, seed)

    def test_has_enough_tasks_including_multi_part_ones(self) -> None:
        assert len(GreeterSuite.SUITE.tasks) >= 10
        assert {"farewell-and-shout", "robust-hello"} <= set(GreeterSuite.ids())


class TestTaskSuite:
    def test_load_resolves_paths_against_the_suite_file(self) -> None:
        suite = GreeterSuite.SUITE
        assert suite.project == (EXAMPLES / "project.yaml").resolve()
        assert all(h.is_absolute() and h.is_file() for t in suite.tasks for h in t.hidden_tests)

    def test_project_config_puts_the_judge_on_the_first_gate_in_shadow_mode(self) -> None:
        config = GreeterSuite.SUITE.project_config()
        assert config.gates[0].judge == "RawToExperimental"
        assert config.jev.mode is JevMode.SHADOW

    def test_missing_files_and_duplicate_ids_are_errors(self, tmp_path: Path) -> None:
        (tmp_path / "suite.yaml").write_text(
            yaml.safe_dump(
                {"project": "nope.yaml", "tasks": [{"id": "a", "task": "t", "hidden_tests": ["x.py"]}]}
            )
        )
        with pytest.raises(ValueError, match="missing files"):
            TaskSuite.load(tmp_path / "suite.yaml")
        task = {"id": "a", "task": "t", "hidden_tests": ["x.py"]}
        with pytest.raises(ValueError, match="duplicate task ids"):
            TaskSuite.model_validate({"project": "p.yaml", "tasks": [task, task]})


class TinySuite:
    """A two-task suite on the greeter seed, run on the host with a scripted executor."""

    @staticmethod
    def write(tmp_path: Path) -> TaskSuite:
        config: dict[str, Any] = {
            "project": "greeter",
            "domain": "software",
            "north_star": "A small greeting library",
            "levels": ["raw", "experimental"],
            "seed": str(EXAMPLES / "seed"),
            "sandbox": {"kind": "local"},
            "models": {"coder": {"model": "ollama:unused"}},
            "checks": {"unit_tests": {"command": list(PYTEST)}},
            "teams": [{"use": "executor", "watches": "raw", "model": "coder", "checks": ["unit_tests"]}],
            "gates": [{"from": "raw", "to": "experimental", "hard_checks": ["unit_tests"]}],
        }
        (tmp_path / "project.yaml").write_text(yaml.safe_dump(config))
        farewell = GreeterSuite.task("farewell")
        suite = {
            "project": "project.yaml",
            "test_command": list(PYTEST),
            "tasks": [
                {"id": "done", "task": farewell.task, "hidden_tests": [str(farewell.hidden_tests[0])]},
                {"id": "half-done", "task": farewell.task, "hidden_tests": [str(farewell.hidden_tests[0])]},
            ],
        }
        (tmp_path / "suite.yaml").write_text(yaml.safe_dump(suite))
        return TaskSuite.load(tmp_path / "suite.yaml")

    @staticmethod
    def services(task: EvalTask) -> ProjectServices:
        """'done' writes the reference solution; 'half-done' a farewell with the wrong text."""
        text = "Goodbye" if task.id == "done" else "Bye"

        def work(toolbox: Toolbox | None) -> ExecutorReport:
            assert isinstance(toolbox, WorktreeToolbox)
            toolbox.write_file(
                "greeter.py",
                'def hello(name: str) -> str:\n    return f"Hello, {name}!"\n\n\n'
                f'def farewell(name: str) -> str:\n    return f"{text}, {{name}}!"\n',
            )
            return ExecutorReport(summary="add farewell", complete=True)

        return ProjectServices(
            workers=FakeWorkers({"coder": FakeWorker([work])}), decider=FakeDecider([Judged.passing()])
        )


class TestEvaluationRun:
    async def test_hidden_tests_become_outcomes_for_complete(self, tmp_path: Path) -> None:
        suite = TinySuite.write(tmp_path)
        decisions = InMemoryDecisions()
        done, half = await EvaluationRun(
            suite, tmp_path / "run", decisions, TinySuite.services, EXECUTOR_ONLY
        ).run()

        assert (done.task, done.level, done.hidden_tests_passed) == ("done", "experimental", (True,))
        assert (half.task, half.level, half.hidden_tests_passed) == ("half-done", "experimental", (False,))
        first, second = decisions.all()
        assert first.mode is JevMode.SHADOW
        complete, relevant = decisions.outcomes(first.id)
        assert (complete.field, complete.truth, complete.source) == (
            "complete",
            True,
            OutcomeSource.HIDDEN_TESTS,
        )
        # The scripted executor in this test adds no tests, so mutation testing finds them not relevant.
        assert (relevant.field, relevant.truth, relevant.source) == (
            "tests_relevant",
            False,
            OutcomeSource.MUTATION,
        )
        assert decisions.outcomes(second.id)[0].truth is False

    async def test_hidden_tests_never_reach_the_repository(self, tmp_path: Path) -> None:
        suite = TinySuite.write(tmp_path)
        await EvaluationRun(
            suite, tmp_path / "run", InMemoryDecisions(), TinySuite.services, EXECUTOR_ONLY
        ).run()
        for task in ("done", "half-done"):
            repo = tmp_path / "run" / task / "executor" / "repo"
            tracked = subprocess.run(
                [
                    "git",
                    "-C",
                    str(repo),
                    "log",
                    "--all",
                    "--name-only",
                    "--format=",
                ],  # every file ever committed
                capture_output=True,
                text=True,
                check=True,
            ).stdout
            assert "greeter.py" in tracked
            assert HiddenTests.DIRECTORY not in tracked

    async def test_a_used_workdir_is_refused(self, tmp_path: Path) -> None:
        suite = TinySuite.write(tmp_path)
        (tmp_path / "run" / "done" / "executor").mkdir(parents=True)
        with pytest.raises(FileExistsError, match="new workdir"):
            await EvaluationRun(
                suite, tmp_path / "run", InMemoryDecisions(), TinySuite.services, EXECUTOR_ONLY
            ).run()


class FarewellSuite:
    """The greeter suite's farewell task alone, run on the host, with its reference solution."""

    @staticmethod
    def write(tmp_path: Path) -> TaskSuite:
        TinySuite.write(tmp_path)  # for project.yaml
        farewell = GreeterSuite.task("farewell")
        suite = {
            "project": "project.yaml",
            "test_command": list(PYTEST),
            "tasks": [
                {
                    "id": "farewell",
                    "task": farewell.task,
                    "hidden_tests": [str(p) for p in farewell.hidden_tests],
                    "reference": {k: str(v) for k, v in farewell.reference.items()},
                }
            ],
        }
        (tmp_path / "suite.yaml").write_text(yaml.safe_dump(suite))
        return TaskSuite.load(tmp_path / "suite.yaml")

    @staticmethod
    def services(task: EvalTask) -> ProjectServices:
        return ProjectServices(decider=FakeDecider([Judged.passing()]))


class TestScriptedVariants:
    async def test_known_truths_and_mutation_agree_with_the_construction(self, tmp_path: Path) -> None:
        suite = FarewellSuite.write(tmp_path)
        decisions = InMemoryDecisions()
        variants = (Variant.REFERENCE, Variant.OUT_OF_SCOPE, Variant.TRIVIAL_TESTS)
        results = await EvaluationRun(
            suite, tmp_path / "run", decisions, FarewellSuite.services, variants
        ).run()

        truths: dict[Variant, dict[str, tuple[bool, OutcomeSource]]] = {}
        for result in results:
            (decision,) = result.decisions
            outcomes = decisions.outcomes(decision)
            truths[result.variant] = {
                field: (best.truth, best.source)
                for field in ("complete", "in_scope", "tests_relevant")
                if (best := Outcomes.strongest(outcomes, field)) is not None
            }
            assert result.hidden_tests_passed == (True,)  # every variant writes the reference solution

        assert truths[Variant.REFERENCE]["in_scope"] == (True, OutcomeSource.PLANTED)
        assert truths[Variant.OUT_OF_SCOPE]["in_scope"] == (False, OutcomeSource.PLANTED)
        assert truths[Variant.TRIVIAL_TESTS]["tests_relevant"] == (False, OutcomeSource.PLANTED)
        # Mutation testing reaches the same verdicts on its own:
        by_variant = {r.variant: r for r in results}
        assert (by_variant[Variant.REFERENCE].mutation_scores[0] or 0) >= MutationTesting.MIN_SCORE
        assert by_variant[Variant.TRIVIAL_TESTS].mutation_scores == (0.0,)

    def test_scripted_variants_need_reference_solutions(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="need reference solutions"):
            EvaluationRun(
                TinySuite.write(tmp_path), tmp_path / "run", InMemoryDecisions(), TinySuite.services
            )
