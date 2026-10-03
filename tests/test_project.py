"""End to end: the Phase 0 exit criterion from proposal §19."""

import sys
from collections.abc import Sequence
from copy import deepcopy
from pathlib import Path
from typing import Any, ClassVar

from agentswarm.blackboard import Blackboard
from agentswarm.clock import FakeClock
from agentswarm.config import ProjectConfig
from agentswarm.domain import ArtifactCreated, Claimed, CommitPushed, ProjectId, Promoted, Rejected, Released
from agentswarm.eventlog import SqliteEventLog
from agentswarm.gates import FakeDecider, Verdict
from agentswarm.project import Project
from agentswarm.teams import TeamCatalog
from tests.samples import Samples


class Projects:
    """Opens a project in a temporary directory with a fake executor and a scripted decider."""

    CONFIG: ClassVar[dict[str, Any]] = {
        "project": "demo",
        "domain": "software",
        "north_star": "A small demo project",
        "levels": ["raw", "experimental"],
        "sandbox": {"kind": "local"},
        "checks": {
            # Every markdown file in the tree must be non-empty.
            "non_empty": {
                "command": [
                    sys.executable,
                    "-c",
                    "import pathlib, sys; "
                    "sys.exit(any(p.stat().st_size == 0 for p in pathlib.Path().glob('*.md')))",
                ],
                "kind": "test_failure",
            }
        },
        "teams": [{"use": "fake_executor", "watches": "raw"}],
        "gates": [{"from": "raw", "to": "experimental", "hard_checks": ["non_empty"]}],
    }

    @classmethod
    def open(cls, root: Path, verdicts: Sequence[Verdict] = (Verdict.PROMOTE,)) -> Project:
        return Project.open(
            ProjectConfig.model_validate(cls.CONFIG),
            root,
            TeamCatalog.with_fakes(),
            FakeDecider(verdicts),
            FakeClock(Samples.AT),
        )


class TestProject:
    async def test_artifact_from_a_fake_executor_is_promoted_and_replay_reproduces_the_views(
        self, tmp_path: Path
    ) -> None:
        project = Projects.open(tmp_path)
        artifact = project.create_artifact("Write the greeting")
        assert artifact.branch == "raw/a-0001-write-the-greeting"

        assert await project.run_until_idle() == 1
        views = project.views()
        assert views.artifact(artifact.id).level == Samples.EXPERIMENTAL
        assert project.repository.files_at("experimental") == ["a-0001.md"]
        assert views.active_claims(Samples.AT) == []
        assert [type(e) for e in views.history(artifact.id)] == [
            ArtifactCreated,
            Claimed,  # executor
            CommitPushed,
            Released,
            Claimed,  # gate
            Promoted,
            Released,
        ]

        # Replay from a separate connection to the same log reproduces every view.
        snapshot = deepcopy(views)
        with SqliteEventLog(tmp_path / "blackboard.sqlite", ProjectId("demo"), FakeClock()) as log:
            assert Blackboard(log).current() == snapshot
        assert project.board.rebuild() == snapshot
        project.close()

    async def test_rejection_is_fixed_by_the_executor_and_then_promoted(self, tmp_path: Path) -> None:
        project = Projects.open(tmp_path, verdicts=[Verdict.REJECT, Verdict.PROMOTE])
        artifact = project.create_artifact("Write the greeting")
        await project.run_until_idle()
        history = project.views().history(artifact.id)
        assert [type(e) for e in history].count(Rejected) == 1
        assert [type(e) for e in history].count(CommitPushed) == 2
        assert project.views().artifact(artifact.id).level == Samples.EXPERIMENTAL
        merged = project.repository.git.run(project.repository.path, "show", "experimental:a-0001.md")
        assert "fixed: Rejected by the fake decider" in merged
        project.close()

    async def test_several_artifacts_all_reach_the_next_level(self, tmp_path: Path) -> None:
        project = Projects.open(tmp_path)
        tasks = ["First part", "Second part", "Third part"]
        for task in tasks:
            project.create_artifact(task)
        await project.run_until_idle()
        views = project.views()
        assert len(views.artifacts_at(Samples.EXPERIMENTAL)) == len(tasks)
        assert project.repository.files_at("experimental") == ["a-0001.md", "a-0002.md", "a-0003.md"]
        project.close()

    async def test_work_resumes_after_reopening(self, tmp_path: Path) -> None:
        first = Projects.open(tmp_path)
        artifact = first.create_artifact("Survive a restart")
        first.close()

        second = Projects.open(tmp_path)
        await second.run_until_idle()
        assert second.views().artifact(artifact.id).level == Samples.EXPERIMENTAL
        second.close()

    async def test_an_idle_project_takes_no_action(self, tmp_path: Path) -> None:
        project = Projects.open(tmp_path)
        assert await project.run_until_idle() == 0
        project.close()


class TestSlug:
    def test_slug_is_branch_safe(self) -> None:
        assert Project.slug("Add retry: exponential back-off (v2)!") == "add-retry-exponential-back-off-v2"
        assert Project.slug("???") == "task"
        assert len(Project.slug("x" * 100)) == 40
