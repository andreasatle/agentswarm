"""Hard checks: deterministic commands run in the sandbox on an artifact's files (proposal §11.2, step 1)."""

from collections.abc import Sequence
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from agentswarm.core.config import CheckConfig
from agentswarm.core.domain import FindingKind
from agentswarm.workspace.sandbox import Sandbox, SandboxResult


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class CheckStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"  # it ran and found a problem
    ERROR = "error"  # it couldn't run properly: a timeout, a missing tool, a crash of the runner itself

    @classmethod
    def of(cls, result: SandboxResult, failure_exit_codes: tuple[int, ...] | None) -> "CheckStatus":
        if result.exit_code == 0:
            return cls.PASSED
        if result.exit_code is None:
            return cls.ERROR
        if failure_exit_codes is None or result.exit_code in failure_exit_codes:
            return cls.FAILED
        return cls.ERROR


class CheckResult(Frozen):
    name: str
    status: CheckStatus
    output: str  # the tail of stdout and stderr

    @property
    def passed(self) -> bool:
        return self.status is CheckStatus.PASSED

    @property
    def failed(self) -> bool:
        """It ran and found a problem: evidence. An error running it is not."""
        return self.status is CheckStatus.FAILED


class HardCheck(Protocol):
    """A deterministic check run in a directory holding the artifact's files."""

    @property
    def name(self) -> str: ...

    @property
    def kind(self) -> FindingKind: ...

    def run(self, cwd: Path) -> CheckResult: ...


class CommandCheck:
    """A hard check that runs a command in a sandbox; exit code 0 passes."""

    OUTPUT_LIMIT = 4000  # characters of output kept, from the end, for the finding

    def __init__(self, name: str, config: CheckConfig, sandbox: Sandbox) -> None:
        self._name = name
        self._config = config
        self._sandbox = sandbox

    @property
    def name(self) -> str:
        return self._name

    @property
    def kind(self) -> FindingKind:
        return self._config.kind

    def run(self, cwd: Path) -> CheckResult:
        return self.run_on(cwd, ())

    def run_on(
        self, cwd: Path, paths: Sequence[str], failure_exit_codes: tuple[int, ...] | None = None
    ) -> CheckResult:
        """Run the check's command on specific paths (appended as arguments), e.g. one test file.

        `failure_exit_codes` overrides the check's own, e.g. to count a test that can't import yet as failing.
        """
        result = self._sandbox.run((*self._config.command, *paths), cwd, self._config.timeout_s)
        codes = failure_exit_codes if failure_exit_codes is not None else self._config.failure_exit_codes
        status = CheckStatus.of(result, codes)
        return CheckResult(name=self._name, status=status, output=result.output[-self.OUTPUT_LIMIT :])


class CheckSet:
    """Builds the configured hard checks by name, all running in the project's sandbox."""

    def __init__(self, configs: dict[str, CheckConfig], sandbox: Sandbox) -> None:
        self._configs = configs
        self._sandbox = sandbox

    def build(self, names: Sequence[str]) -> list[HardCheck]:
        return [self.command(name) for name in names]

    def command(self, name: str) -> CommandCheck:
        return CommandCheck(name, self._configs[name], self._sandbox)
