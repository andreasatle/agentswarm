import sys
from pathlib import Path

import pytest

from agentswarm.core.config import SandboxConfig, SandboxKind
from agentswarm.workspace.sandbox import DockerSandbox, LocalSandbox, Sandboxes


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
