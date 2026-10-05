"""Imported task suites: public coding benchmarks turned into our suite format (Phase 8 revision).

EvalPlus publishes HumanEval+ (164 tasks) and MBPP+ (378) with a reference solution and strengthened tests
(Apache-2.0; HumanEval is MIT, MBPP CC-BY-4.0). Each task becomes a suite task on an almost empty seed:

- the task asks for `solution.py` with one function: HumanEval's signature and docstring, or MBPP's
  sentence with one of its examples;
- the reference is that function, with its helpers;
- the hidden tests are EvalPlus's own test code, unchanged except for one line: `import numpy as np`
  becomes a standard-library stand-in for the four numpy names the tests use, since the sandbox has no
  third-party packages. The test code goes in `check_<id>.py`, which pytest doesn't collect, and
  `test_<id>.py` runs it.

Only tasks that pass `ImportCheck` are written: the reference passes the hidden tests and the seed fails
them, both in the sandbox. Every task's outcome is reported, so nothing is dropped silently.
"""

import ast
import gzip
import json
import shutil
import warnings
from enum import StrEnum
from pathlib import Path
from typing import Any, ClassVar

import yaml
from pydantic import BaseModel, ConfigDict

from agentswarm.evaluation.suite import HiddenTests, TaskSuite
from agentswarm.gating.checks import CheckStatus
from agentswarm.workspace.sandbox import Sandbox


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class EvalPlusFormat(StrEnum):
    HUMANEVAL = "humaneval"
    MBPP = "mbpp"


class ImportedTask(Frozen):
    id: str  # e.g. "he-2", "mbpp-4"
    source_id: str  # the benchmark's own id, e.g. "HumanEval/2"
    task: str
    reference: str  # the complete solution.py
    check: str  # the benchmark's test code, runnable once solution.py exists


class NumpyStandIn:
    """What the EvalPlus tests use of numpy, in plain Python: ndarray, float64, float32 and allclose."""

    IMPORT = "import numpy as np"
    SOURCE = """\
class np:  # a standard-library stand-in for the numpy names these tests use
    class ndarray:
        dtype = None

    float64 = float32 = float

    @staticmethod
    def allclose(out, exp, rtol=1e-07, atol=0.0):
        if isinstance(exp, (list, tuple)):
            return (
                isinstance(out, (list, tuple))
                and len(out) == len(exp)
                and all(np.allclose(o, e, rtol, atol) for o, e in zip(out, exp))
            )
        try:
            return abs(out - exp) <= atol + rtol * abs(exp)
        except TypeError:
            return False"""

    @classmethod
    def replace(cls, test: str) -> str:
        if cls.IMPORT not in test:
            return test
        return test.replace(cls.IMPORT, cls.SOURCE, 1)


class EvalPlusReader:
    """Reads an EvalPlus "OriginFmt" release file (.jsonl.gz) into imported tasks."""

    @classmethod
    def read(cls, path: Path, kind: EvalPlusFormat) -> list[ImportedTask]:
        with gzip.open(path, "rt", encoding="utf-8") as file:
            rows: list[dict[str, Any]] = [json.loads(line) for line in file if line.strip()]
        convert = cls.humaneval if kind is EvalPlusFormat.HUMANEVAL else cls.mbpp
        return [convert(row) for row in rows]

    @staticmethod
    def humaneval(row: dict[str, Any]) -> ImportedTask:
        source_id = str(row["task_id"])
        entry = str(row["entry_point"])
        prompt = str(row["prompt"]).strip("\n")
        reference = prompt + "\n" + str(row["canonical_solution"]).rstrip() + "\n"
        task = (
            f"Create solution.py with the function `{entry}` below: keep its signature and implement what "
            f"its docstring describes. Keep any other functions given as they are. Add tests.\n\n"
            f"```python\n{prompt}\n```"
        )
        check = f"from solution import *\n\n{NumpyStandIn.replace(str(row['test']))}\n\ncheck({entry})\n"
        return ImportedTask(
            id=f"he-{source_id.split('/')[1]}",
            source_id=source_id,
            task=task,
            reference=reference,
            check=check,
        )

    @classmethod
    def mbpp(cls, row: dict[str, Any]) -> ImportedTask:
        source_id = str(row["task_id"])
        reference = str(row["code"]).strip("\n") + "\n"
        example = str(row["test_list"][0]).strip()
        name = cls.function_called(reference, example)
        shown = example.removeprefix("assert").strip()
        task = (
            f"{str(row['prompt']).strip()} Write it as the function `{name}` in solution.py; for example, "
            f"`{shown}` must hold. Add tests."
        )
        check = f"from solution import *\n\n{NumpyStandIn.replace(str(row['test']))}\n"
        return ImportedTask(
            id=f"mbpp-{source_id}", source_id=source_id, task=task, reference=reference, check=check
        )

    @staticmethod
    def function_called(reference: str, example: str) -> str:
        """The reference's top-level function that the example calls; MBPP names it only there."""
        with warnings.catch_warnings():  # MBPP code has invalid escapes in strings; harmless for parsing
            warnings.simplefilter("ignore", SyntaxWarning)
            tree = ast.parse(reference)
        defined = [node.name for node in tree.body if isinstance(node, ast.FunctionDef)]
        called = {
            node.func.id
            for node in ast.walk(ast.parse(example))
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        for name in defined:
            if name in called:
                return name
        raise ValueError(f"no function of the reference ({defined}) is called in {example!r}")


class ImportCheck:
    """In the sandbox: the reference must pass the hidden tests, and the seed must fail them."""

    def __init__(self, sandbox: Sandbox, scratch: Path, command: tuple[str, ...], timeout_s: float) -> None:
        self._sandbox = sandbox
        self._scratch = scratch
        self._command = command
        self._timeout_s = timeout_s

    def problem(self, task: ImportedTask, seed: Path) -> str | None:
        """Why the task can't be used, or None if it can."""
        with_reference = self._status(task, seed, task.reference)
        if with_reference is not CheckStatus.PASSED:
            return f"the reference doesn't pass its tests ({with_reference})"
        on_seed = self._status(task, seed, None)
        if on_seed is not CheckStatus.FAILED:
            return f"the seed doesn't fail the tests ({on_seed})"
        return None

    def _status(self, task: ImportedTask, seed: Path, solution: str | None) -> CheckStatus:
        folder = self._scratch / task.id
        shutil.rmtree(folder, ignore_errors=True)
        shutil.copytree(seed, folder)
        if solution is not None:
            (folder / SuiteWriter.SOLUTION).write_text(solution)
        hidden = folder / HiddenTests.DIRECTORY
        hidden.mkdir()
        for name, content in SuiteWriter.hidden_files(task).items():
            (hidden / name).write_text(content)
        result = self._sandbox.run((*self._command, HiddenTests.DIRECTORY), folder, self._timeout_s)
        shutil.rmtree(folder, ignore_errors=True)
        return CheckStatus.of(result, SuiteWriter.FAILURE_EXIT_CODES)


class ImportReport(Frozen):
    written: tuple[str, ...]
    skipped: dict[str, str]  # task id -> why

    def render(self) -> str:
        lines = [f"{len(self.written)} task(s) written, {len(self.skipped)} skipped"]
        lines += [f"  skipped {task}: {why}" for task, why in self.skipped.items()]
        return "\n".join(lines)


class SuiteWriter:
    """Writes imported tasks as a suite folder: project.yaml, seed/, suite.yaml, hidden/, reference/."""

    SOLUTION = "solution.py"
    TEST_COMMAND: ClassVar[tuple[str, ...]] = ("python", "-m", "pytest", "-q", "-p", "no:cacheprovider")
    FAILURE_EXIT_CODES: ClassVar[tuple[int, ...]] = (1, 2)
    TIMEOUT_S = 120.0
    SEED_README = "A small Python project. Its code goes in solution.py, its tests in tests/.\n"

    def __init__(self, out: Path, name: str, check: ImportCheck) -> None:
        self._out = out
        self._name = name
        self._check = check

    @staticmethod
    def hidden_files(task: ImportedTask) -> dict[str, str]:
        name = task.id.replace("-", "_")
        runner = (
            "import pathlib\nimport runpy\n\n\n"
            "def test_task():\n"
            f'    runpy.run_path(str(pathlib.Path(__file__).with_name("check_{name}.py")))\n'
        )
        return {f"test_{name}.py": runner, f"check_{name}.py": task.check}

    def write(self, tasks: list[ImportedTask]) -> ImportReport:
        seed = self._out / "seed"
        seed.mkdir(parents=True, exist_ok=True)
        (seed / "README.md").write_text(self.SEED_README)
        (self._out / "project.yaml").write_text(yaml.safe_dump(self.project(), sort_keys=False))
        written: list[dict[str, Any]] = []
        skipped: dict[str, str] = {}
        for task in tasks:
            problem = self._check.problem(task, seed)
            if problem is not None:
                skipped[task.id] = problem
                continue
            written.append(self._write_task(task))
        suite: dict[str, Any] = {
            "project": "project.yaml",
            "test_command": list(self.TEST_COMMAND),
            "test_failure_exit_codes": list(self.FAILURE_EXIT_CODES),
            "tasks": written,
        }
        (self._out / "suite.yaml").write_text(yaml.safe_dump(suite, sort_keys=False, width=110))
        TaskSuite.load(self._out / "suite.yaml")  # what was written must load
        return ImportReport(written=tuple(t["id"] for t in written), skipped=skipped)

    def _write_task(self, task: ImportedTask) -> dict[str, Any]:
        hidden: list[str] = []
        for name, content in self.hidden_files(task).items():
            path = self._out / "hidden" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
            hidden.append(f"hidden/{name}")  # both are copied next to each other; pytest collects test_*
        reference = self._out / "reference" / f"{task.id}.py"
        reference.parent.mkdir(parents=True, exist_ok=True)
        reference.write_text(task.reference)
        return {
            "id": task.id,
            "task": task.task,
            "hidden_tests": hidden,
            "reference": {self.SOLUTION: f"reference/{task.id}.py"},
        }

    def project(self) -> dict[str, Any]:
        """A project the suite's tasks run in: one executor and one gate, as for the greeter suites."""
        return {
            "project": self._name,
            "domain": "software",
            "north_star": "Small, correct Python functions.",
            "levels": ["raw", "experimental"],
            "seed": "seed",
            "sandbox": {"kind": "docker", "image": "agentswarm/sandbox-python:3.13"},
            "models": {"coder": {"model": "ollama:gemma4:e4b", "temperature": 0}},
            "checks": {
                "unit_tests": {
                    "command": list(self.TEST_COMMAND),
                    "kind": "test_failure",
                    "timeout_s": self.TIMEOUT_S,
                    "failure_exit_codes": [1],
                }
            },
            "teams": [
                {
                    "use": "executor",
                    "watches": "raw",
                    "model": "coder",
                    "checks": ["unit_tests"],
                    "constraints": ["Use only the Python standard library."],
                }
            ],
            "gates": [{"from": "raw", "to": "experimental", "hard_checks": ["unit_tests"]}],
        }
