"""Where commands that touch LLM-written code run (proposal §13.4).

`DockerSandbox` is the one to use for anything a model wrote: no network, limited CPU, memory and
processes, a read-only root filesystem, and only a copy of the worktree mounted. `LocalSandbox` runs
on the host with a minimal environment; it is for tests and for checks you trust.

Both run commands on a throwaway copy of the worktree without its Git metadata (`RunCopy`), so nothing a
command writes reaches the worktree or host Git, and both keep a bounded amount of output (`Process`).
"""

import contextlib
import os
import shutil
import signal
import subprocess
import tempfile
import uuid
from collections.abc import Callable, Sequence
from pathlib import Path
from types import TracebackType
from typing import IO, Protocol

from pydantic import BaseModel, ConfigDict

from agentswarm.core.config import SandboxConfig, SandboxKind


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


class RunCopy:
    """A throwaway copy of a directory for one command, without Git metadata (CODEX_AUDIT.md F01).

    Commands never run in the worktree itself: whatever they write, a forged `.git` included, goes away
    with the copy, so host Git never reads it. Symlinks are copied as links, never followed. The copy sits
    next to the original so it can be mounted wherever the original can (Colima shares only the home
    directory).
    """

    IGNORED = (".git", "__pycache__")

    def __init__(self, source: Path) -> None:
        self._source = source
        self.path = source.parent / f".run-{uuid.uuid4().hex[:12]}"

    def __enter__(self) -> Path:
        shutil.copytree(self._source, self.path, symlinks=True, ignore=shutil.ignore_patterns(*self.IGNORED))
        return self.path

    def __exit__(
        self, kind: type[BaseException] | None, error: BaseException | None, trace: TracebackType | None
    ) -> None:
        shutil.rmtree(self.path, ignore_errors=True)


class Process:
    """Runs a command with a timeout, killing its whole process group when time runs out.

    Output goes to a temporary file rather than memory, and only its start and end are kept: a noisy
    command can't exhaust the orchestrator (CODEX_AUDIT.md F14).
    """

    KEEP = 64_000  # bytes kept from the start and from the end of the output

    @classmethod
    def run(
        cls,
        command: Sequence[str],
        cwd: Path,
        env: dict[str, str] | None,
        timeout_s: float,
        on_timeout: Callable[[], None] | None = None,
    ) -> SandboxResult:
        with tempfile.TemporaryFile() as out:
            try:
                process = subprocess.Popen(
                    list(command),
                    cwd=cwd,
                    env=env,
                    stdout=out,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
            except OSError as error:
                return SandboxResult(exit_code=None, output=f"could not run: {error}")
            try:
                code = process.wait(timeout=timeout_s)
            except subprocess.TimeoutExpired:
                cls._kill(process)
                if on_timeout is not None:
                    on_timeout()
                return SandboxResult(exit_code=None, output=f"timed out after {timeout_s}s", timed_out=True)
            return SandboxResult(exit_code=code, output=cls.bounded(out))

    @staticmethod
    def _kill(process: subprocess.Popen[bytes]) -> None:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        process.wait()

    @classmethod
    def bounded(cls, out: IO[bytes]) -> str:
        size = out.seek(0, os.SEEK_END)
        out.seek(0)
        if size <= 2 * cls.KEEP:
            return out.read().decode(errors="replace")
        head = out.read(cls.KEEP).decode(errors="replace")
        out.seek(size - cls.KEEP)
        tail = out.read().decode(errors="replace")
        return f"{head}\n... ({size - 2 * cls.KEEP} bytes of output omitted) ...\n{tail}"


class LocalSandbox:
    """Runs on the host. Only PATH and a few harmless variables are passed on, so no keys leak in."""

    def __init__(self) -> None:
        self._env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "LANG": "C.UTF-8",
            "PYTHONDONTWRITEBYTECODE": "1",
        }

    def run(self, command: Sequence[str], cwd: Path, timeout_s: float) -> SandboxResult:
        with RunCopy(cwd) as copy:
            return Process.run(command, copy, self._env | {"HOME": str(copy)}, timeout_s)


class DockerSandbox:
    """Runs each command in a fresh, throwaway container with a copy of the worktree mounted at /work."""

    WORKDIR = "/work"

    def __init__(self, config: SandboxConfig, docker: str = "docker") -> None:
        self._config = config
        self._docker = docker

    @classmethod
    def available(cls, docker: str = "docker") -> bool:
        """Whether a Docker daemon answers."""
        if shutil.which(docker) is None:
            return False
        try:
            info = subprocess.run(
                [docker, "info"], capture_output=True, check=False, timeout=cls.CONTROL_TIMEOUT_S
            )
        except subprocess.TimeoutExpired:
            return False
        return info.returncode == 0

    CONTROL_TIMEOUT_S = 30.0  # for docker's own commands (info, kill)

    def run(self, command: Sequence[str], cwd: Path, timeout_s: float) -> SandboxResult:
        name = f"agentswarm-{uuid.uuid4().hex[:12]}"
        with RunCopy(cwd) as copy:
            # Stopping the client doesn't stop the container: on timeout, kill it (--rm then removes it).
            return Process.run(
                self.command(command, copy, name), copy, None, timeout_s, lambda: self._kill(name)
            )

    def _kill(self, name: str) -> None:
        with contextlib.suppress(subprocess.TimeoutExpired):
            subprocess.run(
                [self._docker, "kill", name], capture_output=True, check=False, timeout=self.CONTROL_TIMEOUT_S
            )

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
