import sys
import time
from pathlib import Path

import pytest

from agentswarm.core.config import SandboxConfig, SandboxKind
from agentswarm.workspace.sandbox import DockerSandbox, LocalSandbox, Process, Sandboxes, SandboxUnavailable


class TestLocalSandbox:
    def test_runs_in_the_directory_and_reports_the_exit_code(self, tmp_path: Path) -> None:
        (tmp_path / "here").write_text("")
        result = LocalSandbox().run(
            [
                sys.executable,
                "-c",
                "import pathlib, sys; print('ok'); sys.exit(0 if pathlib.Path('here').exists() else 1)",
            ],
            tmp_path,
            30,
        )
        assert result.passed
        assert "ok" in result.output

    def test_does_not_pass_secrets_through(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SOME_API_KEY", "secret")
        result = LocalSandbox().run(
            [sys.executable, "-c", "import os; print(os.environ.get('SOME_API_KEY', 'absent'))"], tmp_path, 30
        )
        assert result.output.strip() == "absent"

    def test_timeout(self, tmp_path: Path) -> None:
        result = LocalSandbox().run(["sleep", "5"], tmp_path, 0.1)
        assert result.timed_out
        assert not result.passed

    def test_commands_run_on_a_copy_without_git_metadata(self, tmp_path: Path) -> None:
        worktree = tmp_path / "worktree"
        worktree.mkdir()
        (worktree / ".git").write_text("gitdir: /somewhere/trusted\n")
        (worktree / "code.py").write_text("x = 1\n")
        script = (
            "import pathlib, sys; "
            "print('git visible' if pathlib.Path('.git').exists() else 'no git'); "
            "pathlib.Path('.git').write_text('gitdir: evil'); pathlib.Path('code.py').write_text('x = 2')"
        )
        result = LocalSandbox().run([sys.executable, "-c", script], worktree, 30)
        assert result.passed
        assert "no git" in result.output
        assert (worktree / ".git").read_text() == "gitdir: /somewhere/trusted\n"
        assert (worktree / "code.py").read_text() == "x = 1\n"
        assert [p.name for p in tmp_path.iterdir()] == ["worktree"]  # the copy is gone

    def test_keeps_only_the_start_and_end_of_long_output(self, tmp_path: Path) -> None:
        script = f"print('START' + 'x' * {2 * Process.KEEP + 400_000} + 'END')"
        result = LocalSandbox().run([sys.executable, "-c", script], tmp_path, 30)
        assert result.output.startswith("START")
        assert result.output.rstrip().endswith("END")
        assert "bytes of output omitted" in result.output
        assert len(result.output) < 2 * Process.KEEP + 200
        assert (
            result.omitted == 400_009
        )  # all but the 2 * KEEP bytes kept: the x's beyond them, START, END, newline

    def test_timeout_kills_child_processes_too(self, tmp_path: Path) -> None:
        marker = tmp_path / "survived"
        script = f"import subprocess; subprocess.Popen(['sh', '-c', 'sleep 1; touch {marker}']).wait()"
        assert LocalSandbox().run([sys.executable, "-c", script], tmp_path, 0.3).timed_out
        time.sleep(1.5)
        assert not marker.exists()


class TestDockerSandbox:
    def test_command_isolates_the_container(self, tmp_path: Path) -> None:
        command = DockerSandbox(SandboxConfig(image="img:1")).command(["pytest"], tmp_path, "box")
        joined = " ".join(command)
        for flag in ("--rm", "--network none", "--read-only", "--cap-drop ALL", "--pids-limit 256"):
            assert flag in joined
        assert f"{tmp_path.resolve()}:/work" in joined
        assert command[-2:] == ["img:1", "pytest"]

    def test_network_can_be_enabled_explicitly(self, tmp_path: Path) -> None:
        command = DockerSandbox(SandboxConfig(network=True)).command(["true"], tmp_path, "box")
        assert "--network bridge" in " ".join(command)

    def test_stops_when_no_daemon_answers(self, tmp_path: Path) -> None:
        sandbox = DockerSandbox(SandboxConfig(), docker="no-such-docker")
        with pytest.raises(SandboxUnavailable, match="start Docker"):
            sandbox.run(["pytest"], tmp_path, 10)

    @pytest.mark.docker
    def test_runs_in_a_container_without_network(self, docker_workdir: Path) -> None:
        sandbox = DockerSandbox(SandboxConfig(image="python:3.13-slim"))
        (docker_workdir / "probe.py").write_text(
            "import socket, sys\n"
            "try:\n"
            "    socket.create_connection(('1.1.1.1', 53), timeout=2)\n"
            "except OSError:\n"
            "    sys.exit(0)\n"
            "sys.exit(1)\n"
        )
        result = sandbox.run(["python", "probe.py"], docker_workdir, 120)
        assert result.passed, result.output


class TestSandboxes:
    def test_builds_the_configured_kind(self) -> None:
        assert isinstance(Sandboxes.build(SandboxConfig()), DockerSandbox)
        assert isinstance(Sandboxes.build(SandboxConfig(kind=SandboxKind.LOCAL)), LocalSandbox)
