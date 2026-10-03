import os
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from dotenv import load_dotenv

from agentswarm.workspace.sandbox import DockerSandbox
from tests.support.paths import RepoPaths

# The project's own .env (e.g. TYPESAFE_API_KEY) for the opt-in live tests. Never overrides the shell.
load_dotenv(RepoPaths.ROOT / ".env")


class DockerGate:
    """Docker tests run only when a daemon answers, in a directory the Docker VM can mount (under $HOME)."""

    available = DockerSandbox.available()

    @staticmethod
    def workdir() -> Iterator[Path]:
        base = Path.home() / ".cache" / "agentswarm-tests"
        base.mkdir(parents=True, exist_ok=True)
        path = Path(tempfile.mkdtemp(dir=base))
        try:
            yield path
        finally:
            shutil.rmtree(path, ignore_errors=True)


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    no_docker = pytest.mark.skip(reason="no Docker daemon")
    no_model = pytest.mark.skip(reason="set AGENTSWARM_LIVE_MODEL to run tests against a real model")
    no_jev = pytest.mark.skip(reason="set TYPESAFE_API_KEY and AGENTSWARM_JEV_MODEL to call the real Jev")
    for item in items:
        if item.get_closest_marker("docker") and not DockerGate.available:
            item.add_marker(no_docker)
        if item.get_closest_marker("live") and not os.environ.get("AGENTSWARM_LIVE_MODEL"):
            item.add_marker(no_model)
        if item.get_closest_marker("jev") and not (
            os.environ.get("TYPESAFE_API_KEY") and os.environ.get("AGENTSWARM_JEV_MODEL")
        ):
            item.add_marker(no_jev)


@pytest.fixture
def docker_workdir() -> Iterator[Path]:
    yield from DockerGate.workdir()
