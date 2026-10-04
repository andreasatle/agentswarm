from pathlib import Path

import pytest

from agentswarm.workspace.lock import ProjectLocked, RootLock
from tests.support.projects import Projects


class TestRootLock:
    def test_a_second_owner_is_refused_until_the_first_closes(self, tmp_path: Path) -> None:
        first = RootLock.acquire(tmp_path)
        with pytest.raises(ProjectLocked, match="another agentswarm process"):
            RootLock.acquire(tmp_path)
        first.close()
        RootLock.acquire(tmp_path).close()

    def test_an_open_project_holds_its_root(self, tmp_path: Path) -> None:
        project = Projects.open(tmp_path)
        with pytest.raises(ProjectLocked):
            Projects.open(tmp_path)
        project.close()
        Projects.open(tmp_path).close()
