"""A real model on the greeter example: the Phase 1 exit criterion. Opt in with AGENTSWARM_LIVE_MODEL.

    AGENTSWARM_LIVE_MODEL=ollama:gemma4:26b uv run --extra ollama pytest -m live -s

Checks run in Docker when a daemon answers (the image from docker/sandbox-python.Dockerfile must exist),
otherwise on the host with this interpreter.
"""

import os
import sys
from pathlib import Path

import pytest

from agentswarm.catalog import TeamCatalog
from agentswarm.cli import Report
from agentswarm.config import ProjectConfig, SandboxKind
from agentswarm.gates import FakeDecider
from agentswarm.project import Project
from agentswarm.sandbox import DockerSandbox

EXAMPLE = Path(__file__).parent.parent / "examples" / "greeter" / "project.yaml"
TASK = "Add a function farewell(name) to greeter.py that returns 'Goodbye, <name>!', with tests."


@pytest.mark.live
class TestLiveGreeter:
    async def test_a_real_model_takes_a_task_to_experimental(self, docker_workdir: Path) -> None:
        model = os.environ["AGENTSWARM_LIVE_MODEL"]
        config = ProjectConfig.load(EXAMPLE)
        config = config.model_copy(
            update={"models": {"coder": config.models["coder"].model_copy(update={"model": model})}}
        )
        if not DockerSandbox.available():
            local = config.sandbox.model_copy(update={"kind": SandboxKind.LOCAL})
            tests = config.checks["unit_tests"].model_copy(
                update={"command": (sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider")}
            )
            config = config.model_copy(update={"sandbox": local, "checks": {"unit_tests": tests}})

        # docker_workdir is under $HOME, which Colima and Docker Desktop share with containers by default.
        project = Project.open(config, docker_workdir, TeamCatalog.standard(), FakeDecider())
        artifact = project.create_artifact(TASK)
        await project.run_until_idle(max_rounds=10)
        print(Report.render(project.views()))
        assert project.views().artifact(artifact.id).level == "experimental"
        project.close()
