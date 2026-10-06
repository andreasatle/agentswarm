import shutil
from pathlib import Path

import pytest

from agentswarm.evaluation.suite import EvalTask, HiddenTests, TaskSuite
from agentswarm.workspace.sandbox import LocalSandbox
from tests.support.paths import RepoPaths
from tests.support.suites import PYTEST

INVENTORY = RepoPaths.EXAMPLES / "inventory"
SUITE = TaskSuite.load(INVENTORY / "evaluation" / "suite.yaml")


class Run:
    """A task's hidden tests, or the seed's own tests, on the seed with some files written over it."""

    @staticmethod
    def passes(tmp_path: Path, task: EvalTask, files: dict[str, str], target: str) -> bool:
        work = tmp_path / "work"
        shutil.copytree(INVENTORY / "seed", work)
        for path, text in files.items():
            (work / path).parent.mkdir(parents=True, exist_ok=True)
            (work / path).write_text(text)
        (work / HiddenTests.DIRECTORY).mkdir()
        for test in task.hidden_tests:
            shutil.copy(test, work / HiddenTests.DIRECTORY / test.name)
        return LocalSandbox().run((*PYTEST, target), work, 120).passed


class TestInventorySuite:
    """Ground truth is checked: references pass, the seed fails, and the seed's own tests keep passing."""

    @pytest.mark.parametrize("task", SUITE.tasks, ids=[t.id for t in SUITE.tasks])
    def test_reference_passes_seed_fails_and_own_tests_hold(self, tmp_path: Path, task: EvalTask) -> None:
        reference = {target: source.read_text() for target, source in task.reference.items()}
        assert Run.passes(tmp_path / "ref", task, reference, HiddenTests.DIRECTORY)
        assert Run.passes(tmp_path / "own", task, reference, "tests")
        assert not Run.passes(tmp_path / "seed", task, {}, HiddenTests.DIRECTORY)

    def test_five_tasks_at_each_of_five_levels(self) -> None:
        levels = [t.task.split(")")[0] for t in SUITE.tasks]
        assert sorted(levels) == sorted(f"(level {n}" for n in range(1, 6) for _ in range(5))
