"""Task suites for evaluation tests: the greeter suite, and small suites run on the host with fakes."""

import shutil
import sys
from pathlib import Path
from typing import Any

import yaml

from agentswarm.evaluation.faults import Variant
from agentswarm.evaluation.suite import EvalTask, HiddenTests, TaskSuite
from agentswarm.judging.deciders import FakeDecider
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
    HARD = TaskSuite.load(GREETER_SUITE.parent / "hard" / "suite.yaml")
    HELD_OUT = TaskSuite.load(GREETER_SUITE.parent / "heldout" / "suite.yaml")
    COMPLEX = TaskSuite.load(GREETER_SUITE.parent / "complex" / "suite.yaml")

    @classmethod
    def hidden_tests_pass(cls, tmp_path: Path, task: EvalTask, files: dict[str, str]) -> bool:
        """The task's hidden tests on the seed with `files` (project path -> text) written over it."""
        work = tmp_path / "work"
        shutil.copytree(EXAMPLES / "seed", work)
        for target, text in files.items():
            path = work / target
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        (work / HiddenTests.DIRECTORY).mkdir()
        for test in task.hidden_tests:
            shutil.copy(test, work / HiddenTests.DIRECTORY / test.name)
        return LocalSandbox().run((*PYTEST, HiddenTests.DIRECTORY), work, 60).passed

    @classmethod
    def ids(cls) -> list[str]:
        return [t.id for t in cls.SUITE.tasks]

    @classmethod
    def all_tasks(cls) -> list[EvalTask]:
        """Every task of the greeter suites."""
        return [*cls.SUITE.tasks, *cls.HARD.tasks, *cls.HELD_OUT.tasks, *cls.COMPLEX.tasks]

    @classmethod
    def task(cls, id: str) -> EvalTask:
        return next(t for t in cls.all_tasks() if t.id == id)


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
