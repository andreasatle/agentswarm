import gzip
import json
from pathlib import Path
from typing import Any

import pytest

from agentswarm.evaluation.imports import (
    EvalPlusFormat,
    EvalPlusReader,
    ImportCheck,
    NumpyStandIn,
    SuiteWriter,
)
from agentswarm.evaluation.suite import TaskSuite
from agentswarm.workspace.sandbox import LocalSandbox
from tests.support.suites import PYTEST

PREAMBLE = """
import numpy as np

def assertion(out, exp, atol):
    if out != exp and atol != 0:
        assert np.allclose(out, exp, rtol=1e-07, atol=atol)
    else:
        assert out == exp
"""


class Rows:
    """EvalPlus rows in the OriginFmt shape, small enough to read."""

    @staticmethod
    def humaneval(task_id: int, body: str) -> dict[str, Any]:
        return {
            "task_id": f"HumanEval/{task_id}",
            "entry_point": "half",
            "prompt": '\n\ndef half(x: float) -> float:\n    """Return half of x."""\n',
            "canonical_solution": f"    {body}\n",
            "test": PREAMBLE + "\ndef check(candidate):\n    for x in [1.0, 3.0]:\n"
            "        assertion(candidate(x), x / 2, 1e-06)\n",
        }

    @staticmethod
    def mbpp(task_id: int) -> dict[str, Any]:
        return {
            "task_id": task_id,
            "prompt": "Write a function to add two numbers.",
            "code": "def add (a, b):\n    return a + b\n",
            "test_list": ["assert add (1, 2) == 3"],
            "test": PREAMBLE
            + "\nfor a, b, exp in [(1, 2, 3), (0.1, 0.2, 0.3)]:\n    assertion(add(a, b), exp, 1e-9)\n",
        }

    @staticmethod
    def write(path: Path, rows: list[dict[str, Any]]) -> Path:
        with gzip.open(path, "wt", encoding="utf-8") as file:
            for row in rows:
                file.write(json.dumps(row) + "\n")
        return path


class TestEvalPlusImport:
    def writer(self, tmp_path: Path, name: str) -> SuiteWriter:
        check = ImportCheck(LocalSandbox(), tmp_path / "scratch", PYTEST, 60)
        return SuiteWriter(tmp_path / name, name, check)

    def test_humaneval_tasks_whose_reference_passes_are_written_and_load(self, tmp_path: Path) -> None:
        rows = [Rows.humaneval(1, "return x / 2"), Rows.humaneval(2, "return x")]  # the second is wrong
        tasks = EvalPlusReader.read(Rows.write(tmp_path / "he.jsonl.gz", rows), EvalPlusFormat.HUMANEVAL)
        report = self.writer(tmp_path, "he").write(tasks)
        assert report.written == ("he-1",)
        assert "reference doesn't pass" in report.skipped["he-2"]
        suite = TaskSuite.load(tmp_path / "he" / "suite.yaml")
        task = suite.tasks[0]
        assert "def half(x: float) -> float:" in task.task and "solution.py" in task.task
        assert [p.name for p in task.hidden_tests] == ["test_he_1.py", "check_he_1.py"]
        assert "import numpy" not in task.hidden_tests[1].read_text()

    def test_mbpp_names_the_function_from_the_reference_and_shows_an_example(self, tmp_path: Path) -> None:
        tasks = EvalPlusReader.read(Rows.write(tmp_path / "mb.jsonl.gz", [Rows.mbpp(7)]), EvalPlusFormat.MBPP)
        assert tasks[0].id == "mbpp-7"
        assert "function `add` in solution.py" in tasks[0].task and "`add (1, 2) == 3`" in tasks[0].task
        report = self.writer(tmp_path, "mb").write(tasks)
        assert report.written == ("mbpp-7",)  # 0.1 + 0.2 passes through the stand-in's allclose


class TestNumpyStandIn:
    def test_allclose_compares_numbers_and_nested_sequences(self) -> None:
        namespace: dict[str, Any] = {}
        exec(NumpyStandIn.SOURCE, namespace)
        np = namespace["np"]
        assert np.allclose(0.1 + 0.2, 0.3, atol=1e-9)
        assert np.allclose([1.0, (2.0, 3.0)], [1.0, (2.0, 3.0 + 1e-12)], atol=1e-9)
        assert not np.allclose([1.0, 2.0], [1.0], atol=1e-9)
        assert not np.allclose("a", 1.0, atol=1e-9)
        assert isinstance(1.5, np.float64)

    def test_only_the_numpy_import_is_replaced(self) -> None:
        assert NumpyStandIn.replace("x = 1\n") == "x = 1\n"
        assert "class np:" in NumpyStandIn.replace("import numpy as np\nx = 1\n")

    def test_an_example_that_calls_no_reference_function_is_refused(self) -> None:
        with pytest.raises(ValueError, match="no function of the reference"):
            EvalPlusReader.function_called("def f():\n    pass\n", "assert g(1) == 2")
