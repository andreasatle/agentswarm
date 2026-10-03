"""End to end: whole projects, from tasks to promoted artifacts (the exit criteria of §19)."""

import sys
from copy import deepcopy
from pathlib import Path

from agentswarm.board.artifacts import ArtifactFactory
from agentswarm.board.blackboard import Blackboard
from agentswarm.board.eventlog import SqliteEventLog
from agentswarm.core.clock import FakeClock
from agentswarm.core.config import ProjectConfig
from agentswarm.core.domain import (
    ArtifactCreated,
    ArtifactId,
    Claimed,
    CommitPushed,
    FindingAdded,
    FindingKind,
    ProjectId,
    Promoted,
    Rejected,
    Released,
    TeamName,
)
from agentswarm.jev.api import NoulAnswer
from agentswarm.judging.deciders import FakeDecider
from agentswarm.judging.decisions import InMemoryDecisions
from agentswarm.judging.judges import AdversarialRelevance
from agentswarm.judging.policy import Verdict
from agentswarm.llm.workers import FakeWorker, FakeWorkers, Toolbox
from agentswarm.project import Project, ProjectServices
from agentswarm.teams.analyzer import AnalysisReport, AnalysisToolbox, ReportedProblem
from agentswarm.teams.executor import ExecutorReport, WorktreeToolbox
from tests.support.projects import Projects
from tests.support.samples import Judged, Samples


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
        project = Projects.open(tmp_path, judged=[Judged.incomplete(), Judged.passing()])
        artifact = project.create_artifact("Write the greeting")
        await project.run_until_idle()
        history = project.views().history(artifact.id)
        assert [type(e) for e in history].count(Rejected) == 1
        assert [type(e) for e in history].count(CommitPushed) == 2
        assert project.views().artifact(artifact.id).level == Samples.EXPERIMENTAL
        merged = project.repository.git.run(project.repository.path, "show", "experimental:a-0001.md")
        assert "fixed: The change does not address every part of the task (Jev: p(yes) = 0.05)" in merged
        (rejecting, promoting) = project.decisions.all()
        assert rejecting.ruling.verdict is Verdict.REJECT
        assert promoting.ruling.verdict is Verdict.PROMOTE
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


class TestProjectWithExecutorTeam:
    async def test_model_driven_executor_takes_a_task_to_the_next_level(self, tmp_path: Path) -> None:
        config = Projects.CONFIG | {
            "models": {"coder": {"model": "ollama:any-model"}},
            "teams": [{"use": "executor", "watches": "raw", "model": "coder", "checks": ["non_empty"]}],
        }

        def work(toolbox: Toolbox | None) -> ExecutorReport:
            assert isinstance(toolbox, WorktreeToolbox)
            toolbox.write_file("greeting.md", "Hello!\n")
            assert toolbox.run_checks() == "non_empty: passed"
            return ExecutorReport(summary="add greeting", complete=True)

        project = Project.open(
            ProjectConfig.model_validate(config),
            tmp_path,
            ProjectServices(workers=FakeWorkers({"coder": FakeWorker([work])}), clock=FakeClock(Samples.AT)),
        )
        artifact = project.create_artifact("Write a greeting")
        await project.run_until_idle()
        assert project.views().artifact(artifact.id).level == Samples.EXPERIMENTAL
        assert project.repository.files_at("experimental") == ["greeting.md"]
        project.close()


class TestSlug:
    def test_slug_is_branch_safe(self) -> None:
        assert (
            ArtifactFactory.slug("Add retry: exponential back-off (v2)!")
            == "add-retry-exponential-back-off-v2"
        )
        assert ArtifactFactory.slug("???") == "task"
        assert len(ArtifactFactory.slug("x" * 100)) == 40


class TestProjectWithAnalyzers:
    async def test_analyzers_examine_merged_artifacts_and_their_relevance_is_logged(
        self, tmp_path: Path
    ) -> None:
        config = Projects.CONFIG | {
            "levels": ["raw", "experimental", "candidate"],
            "checks": Projects.CONFIG["checks"]
            | {"pytest": {"command": [sys.executable, "-m", "pytest", "-q"]}},
            "models": {"coder": {"model": "ollama:any"}},
            "teams": [
                {"use": "fake_executor", "watches": "raw"},
                {
                    "use": "red_team",
                    "watches": "experimental",
                    "model": "coder",
                    "checks": ["pytest"],
                    "relevance": "AdversarialRelevance",
                },
            ],
        }

        def attack(toolbox: Toolbox | None) -> AnalysisReport:
            assert isinstance(toolbox, AnalysisToolbox)
            toolbox.run_test("test_break.py", "def test_break():\n    assert False\n")
            problem = ReportedProblem(
                title="Breaks", detail="It breaks.", severity="major", test="test_break.py"
            )
            return AnalysisReport(summary="attacked", problems=(problem,))

        def nothing(toolbox: Toolbox | None) -> AnalysisReport:
            return AnalysisReport(summary="attacked the fix", problems=())

        services = ProjectServices(
            workers=FakeWorkers({"coder": FakeWorker([attack, nothing])}),
            decider=FakeDecider([AdversarialRelevance(relevant=NoulAnswer(noul=0.9))]),
            decisions=InMemoryDecisions(),
            clock=FakeClock(Samples.AT),
        )
        project = Project.open(ProjectConfig.model_validate(config), tmp_path, services)
        artifact = project.create_artifact("Write the greeting")
        await project.run_until_idle()

        views = project.views()
        assert views.artifact(artifact.id).level == Samples.EXPERIMENTAL
        (added,) = [e for e in views.history(artifact.id) if isinstance(e, FindingAdded)]
        assert (added.finding.team, added.finding.kind) == ("red_team", FindingKind.EDGE_CASE)
        assert views.signed_off(artifact.id, TeamName("red_team"))
        # The router turned the finding into a fix artifact, which reached the level and resolved it.
        fix = views.artifact(views.fix_for(added.finding.id) or ArtifactId("missing"))
        assert (fix.parent, fix.level, fix.created_by) == (artifact.id, Samples.EXPERIMENTAL, "router")
        assert views.open_findings(artifact.id) == []
        (relevance,) = [
            d
            for d in project.decisions.all()
            if d.point == "relevance:red_team" and d.artifact == artifact.id
        ]
        assert project.decisions.outcomes(relevance.id)[0].truth is True
        project.close()
