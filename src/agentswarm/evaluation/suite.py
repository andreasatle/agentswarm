"""A task suite: small tasks with hidden acceptance tests, for measuring the judge (Phase 2 design, step 5).

    project: ../project.yaml                 # the project configuration each task runs in
    judge: RawToExperimental                 # asked at the gate out of the lowest level, in shadow mode
    hidden_tests_command: [python, -m, pytest, -q, -p, no:cacheprovider]
    tasks:
      - id: farewell
        task: Add a function farewell(name) ...
        hidden_tests: [hidden/test_farewell.py]

Hidden tests are never in the repository the executor works in. They are copied into a separate
checkout of the judged commit and run there, in the project's sandbox.
"""

from pathlib import Path
from typing import Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from agentswarm.config import JevConfig, JevMode, ProjectConfig
from agentswarm.gates import CheckResult
from agentswarm.git import Repository
from agentswarm.sandbox import Sandbox


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class EvalTask(Frozen):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    task: str  # precise enough that the hidden tests follow from it
    hidden_tests: tuple[Path, ...] = Field(min_length=1)


class TaskSuite(Frozen):
    project: Path
    judge: str = "RawToExperimental"
    hidden_tests_command: tuple[str, ...] = ("python", "-m", "pytest", "-q", "-p", "no:cacheprovider")
    hidden_tests_timeout_s: float = 120.0
    tasks: tuple[EvalTask, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_ids(self) -> Self:
        ids = [t.id for t in self.tasks]
        duplicates = sorted({i for i in ids if ids.count(i) > 1})
        if duplicates:
            raise ValueError(f"duplicate task ids: {duplicates}")
        return self

    @classmethod
    def load(cls, path: Path) -> Self:
        """Load a suite file; relative paths are resolved against its directory and must exist."""
        suite = cls.model_validate(yaml.safe_load(path.read_text()))
        base = path.parent.resolve()
        resolved = suite.model_copy(
            update={
                "project": (base / suite.project).resolve(),
                "tasks": tuple(
                    t.model_copy(update={"hidden_tests": tuple((base / h).resolve() for h in t.hidden_tests)})
                    for t in suite.tasks
                ),
            }
        )
        missing = [
            str(p)
            for p in (resolved.project, *(h for t in resolved.tasks for h in t.hidden_tests))
            if not p.is_file()
        ]
        if missing:
            raise ValueError(f"suite {path} refers to missing files: {missing}")
        return resolved

    def project_config(self) -> ProjectConfig:
        """The project configuration, with the suite's judge on the first gate and Jev in shadow mode."""
        config = ProjectConfig.load(self.project)
        lowest = config.levels.lowest
        if config.gate_from(lowest) is None:
            raise ValueError(f"{self.project} has no gate out of {lowest!r} to judge")
        gates = tuple(
            g.model_copy(update={"judge": self.judge}) if g.from_level == lowest else g for g in config.gates
        )
        jev = JevConfig(model=config.jev.model, mode=JevMode.SHADOW, timeout_s=config.jev.timeout_s)
        return config.model_copy(update={"gates": gates, "jev": jev})


class HiddenTests:
    """Runs a task's hidden tests against one commit, in a throwaway checkout, in the sandbox."""

    DIRECTORY = "_hidden"  # where the tests are placed in the checkout; outside any package of the project

    def __init__(self, command: tuple[str, ...], timeout_s: float, sandbox: Sandbox) -> None:
        self._command = command
        self._timeout_s = timeout_s
        self._sandbox = sandbox

    def run(self, repository: Repository, commit: str, tests: tuple[Path, ...], name: str) -> CheckResult:
        worktree = repository.checkout_detached(commit, f"_hidden-{name}")
        try:
            for test in tests:
                worktree.write(f"{self.DIRECTORY}/{test.name}", test.read_text())
            result = self._sandbox.run((*self._command, self.DIRECTORY), worktree.path, self._timeout_s)
        finally:
            worktree.remove()
        return CheckResult(name=f"hidden tests ({name})", passed=result.passed, output=result.output[-4000:])
