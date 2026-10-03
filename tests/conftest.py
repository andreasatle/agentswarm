import os
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from agentswarm.sandbox import DockerSandbox


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
    for item in items:
        if "docker" in item.keywords and not DockerGate.available:
            item.add_marker(no_docker)
        if "live" in item.keywords and not os.environ.get("AGENTSWARM_LIVE_MODEL"):
            item.add_marker(no_model)


@pytest.fixture
def docker_workdir() -> Iterator[Path]:
    yield from DockerGate.workdir()
