"""Where commands that touch LLM-written code run (proposal §13.4).

`DockerSandbox` is the one to use for anything a model wrote: no network, limited CPU, memory and
processes, a read-only root filesystem, and only the worktree mounted. `LocalSandbox` runs on the
host with a minimal environment; it is for tests and for checks you trust.
"""

import os
import shutil
import subprocess
import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from agentswarm.config import SandboxConfig, SandboxKind


class SandboxResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    exit_code: int | None  # None when the command never finished
    output: str  # stdout followed by stderr
    timed_out: bool = False

    @property
    def passed(self) -> bool:
        return self.exit_code == 0


class Sandbox(Protocol):
    def run(self, command: Sequence[str], cwd: Path, timeout_s: float) -> SandboxResult:
        """Run `command` with `cwd` as its working directory."""
        ...


class LocalSandbox:
    """Runs on the host. Only PATH and a few harmless variables are passed on, so no keys leak in."""

    def __init__(self) -> None:
        self._env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "LANG": "C.UTF-8",
            "PYTHONDONTWRITEBYTECODE": "1",
        }

    def run(self, command: Sequence[str], cwd: Path, timeout_s: float) -> SandboxResult:
        try:
            result = subprocess.run(
                list(command),
                cwd=cwd,
                env=self._env | {"HOME": str(cwd)},
                capture_output=True,
                text=True,
                timeout=timeout_s,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return SandboxResult(exit_code=None, output=f"timed out after {timeout_s}s", timed_out=True)
        except OSError as error:
            return SandboxResult(exit_code=None, output=f"could not run: {error}")
        return SandboxResult(exit_code=result.returncode, output=result.stdout + result.stderr)


class DockerSandbox:
    """Runs each command in a fresh, throwaway container with the worktree mounted at /work."""

    WORKDIR = "/work"

    def __init__(self, config: SandboxConfig, docker: str = "docker") -> None:
        self._config = config
        self._docker = docker

    @classmethod
    def available(cls, docker: str = "docker") -> bool:
        """Whether a Docker daemon answers."""
        if shutil.which(docker) is None:
            return False
        return subprocess.run([docker, "info"], capture_output=True, check=False).returncode == 0

    def run(self, command: Sequence[str], cwd: Path, timeout_s: float) -> SandboxResult:
        name = f"agentswarm-{uuid.uuid4().hex[:12]}"
        try:
            result = subprocess.run(
                self.command(command, cwd, name),
                capture_output=True,
                text=True,
                timeout=timeout_s,
                check=False,
            )
        except subprocess.TimeoutExpired:
            # Stopping the client doesn't stop the container; kill it (--rm then removes it).
            subprocess.run([self._docker, "kill", name], capture_output=True, check=False)
            return SandboxResult(exit_code=None, output=f"timed out after {timeout_s}s", timed_out=True)
        except OSError as error:
            return SandboxResult(exit_code=None, output=f"could not run docker: {error}")
        return SandboxResult(exit_code=result.returncode, output=result.stdout + result.stderr)

    def command(self, command: Sequence[str], cwd: Path, name: str) -> list[str]:
        """The full `docker run` invocation, exposed so its isolation flags can be tested without Docker."""
        c = self._config
        return [
            self._docker,
            "run",
            "--rm",
            "--name",
            name,
            "--network",
            "bridge" if c.network else "none",
            "--cpus",
            str(c.cpus),
            "--memory",
            c.memory,
            "--pids-limit",
            str(c.pids),
            "--read-only",
            "--tmpfs",
            "/tmp:rw,size=256m",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "--env",
            "HOME=/tmp",
            "--env",
            "PYTHONDONTWRITEBYTECODE=1",
            "--volume",
            f"{cwd.resolve()}:{self.WORKDIR}",
            "--workdir",
            self.WORKDIR,
            c.image,
            *command,
        ]


class Sandboxes:
    """Builds the sandbox a project's configuration asks for."""

    @staticmethod
    def build(config: SandboxConfig) -> Sandbox:
        match config.kind:
            case SandboxKind.DOCKER:
                return DockerSandbox(config)
            case SandboxKind.LOCAL:
                return LocalSandbox()
