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
    if DockerGate.available:
        return
    skip = pytest.mark.skip(reason="no Docker daemon")
    for item in items:
        if "docker" in item.keywords:
            item.add_marker(skip)


@pytest.fixture
def docker_workdir() -> Iterator[Path]:
    yield from DockerGate.workdir()
