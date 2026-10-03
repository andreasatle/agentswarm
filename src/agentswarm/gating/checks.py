"""Hard checks: deterministic commands run in the sandbox on an artifact's files (proposal §11.2, step 1)."""

from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from agentswarm.core.config import CheckConfig
from agentswarm.core.domain import FindingKind
from agentswarm.workspace.sandbox import Sandbox


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class CheckResult(Frozen):
    name: str
    passed: bool
    output: str  # the tail of stdout and stderr


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

    def run_on(self, cwd: Path, paths: Sequence[str]) -> CheckResult:
        """Run the check's command on specific paths (appended as arguments), e.g. one test file."""
        result = self._sandbox.run((*self._config.command, *paths), cwd, self._config.timeout_s)
        return CheckResult(name=self._name, passed=result.passed, output=result.output[-self.OUTPUT_LIMIT :])


class CheckSet:
    """Builds the configured hard checks by name, all running in the project's sandbox."""

    def __init__(self, configs: dict[str, CheckConfig], sandbox: Sandbox) -> None:
        self._configs = configs
        self._sandbox = sandbox

    def build(self, names: Sequence[str]) -> list[HardCheck]:
        return [self.command(name) for name in names]

    def command(self, name: str) -> CommandCheck:
        return CommandCheck(name, self._configs[name], self._sandbox)
