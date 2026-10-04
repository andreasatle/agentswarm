from pathlib import Path

import pytest

from agentswarm.core.config import ProjectConfig
from agentswarm.workspace.lock import ProjectLocked, RootIdentity, RootLock
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


class TestRootIdentity:
    def test_a_root_keeps_its_project_and_levels(self, tmp_path: Path) -> None:
        identity = RootIdentity(project="greeter", levels=("raw", "experimental"))
        identity.claim(tmp_path)
        identity.claim(tmp_path)  # the same project again
        with pytest.raises(ValueError, match="belongs to project 'greeter'"):
            identity.model_copy(update={"project": "handbook"}).claim(tmp_path)
        with pytest.raises(ValueError, match="needs a new root"):
            identity.model_copy(update={"levels": ("raw", "experimental", "candidate")}).claim(tmp_path)


class TestProjectOpen:
    def test_unknown_teams_are_refused_before_anything_is_written(self, tmp_path: Path) -> None:
        config = ProjectConfig.model_validate(
            Projects.CONFIG | {"teams": [{"use": "wizard", "watches": "raw"}]}
        )
        with pytest.raises(ValueError, match="unknown teams"):
            Projects.open_config(config, tmp_path / "root")
        assert not (tmp_path / "root").exists()

    def test_a_failed_open_releases_the_root(self, tmp_path: Path) -> None:
        Projects.open(tmp_path).close()
        other = ProjectConfig.model_validate(Projects.CONFIG | {"project": "someone-else"})
        with pytest.raises(ValueError, match="belongs to project"):
            Projects.open_config(other, tmp_path)
        Projects.open(tmp_path).close()  # not left locked
