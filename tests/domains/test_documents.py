"""The document domain end to end: Phase 5's exit criterion, with fakes for the models and Jev."""

import sys
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from agentswarm.control.escalations import Escalations
from agentswarm.core.clock import FakeClock
from agentswarm.core.config import ProjectConfig
from agentswarm.core.domain import Resolution, SignedOff
from agentswarm.jev.api import NoulAnswer
from agentswarm.judging.deciders import FakeDecider
from agentswarm.judging.decisions import InMemoryDecisions
from agentswarm.judging.judges import (
    AudienceRelevance,
    ConsistencyRelevance,
    FactRelevance,
    JoinCheck,
    SectionReadiness,
    SizeCheck,
    SplitCheck,
)
from agentswarm.llm.workers import FakeWorkers, Toolbox
from agentswarm.project import Project, ProjectServices
from agentswarm.teams.analyzer import ReviewReport
from agentswarm.teams.decomposer import ChildTask, Split
from agentswarm.teams.executor import ExecutorReport, ExecutorRequest, WorktreeToolbox
from tests.support.fakes import ScriptedWorker
from tests.support.paths import RepoPaths

SECTIONS = {
    "sections/01-choosing-books.md": "## Choosing books\n\nPick books most members can finish in a month.\n",
    "sections/02-meetings.md": (
        "## Running a meeting\n\nStart with a question about the ending.\nStop after an hour.\n"
    ),
}


class Handbook:
    """The handbook example on the document preset, with scripted models and Jev answers."""

    @staticmethod
    def open(root: Path) -> Project:
        config: dict[str, Any] = {
            "project": "handbook",
            "domain": "document",
            "north_star": "A short handbook for running a book club.",
            "seed": str(RepoPaths.EXAMPLES / "handbook" / "seed"),
            "sandbox": {"kind": "local"},
            "models": {"main": {"model": "ollama:unused"}},
            "checks": {
                "check_doc": {"command": [sys.executable, "tools/check_doc.py"], "kind": "build_failure"}
            },
            "jev": {"mode": "shadow", "active": ["size:*", "split:*", "join:*"]},
        }

        def outline(payload: BaseModel, toolbox: Toolbox | None) -> Split:
            return Split(
                children=tuple(ChildTask(task=f"Write {path} for the handbook") for path in SECTIONS)
            )

        def write(payload: BaseModel, toolbox: Toolbox | None) -> ExecutorReport:
            assert isinstance(payload, ExecutorRequest) and isinstance(toolbox, WorktreeToolbox)
            (path,) = [p for p in SECTIONS if p in payload.instruction.goal]
            toolbox.write_file(path, SECTIONS[path])
            assert toolbox.run_checks() == "check_doc: passed"
            return ExecutorReport(summary=f"write {path}", complete=True)

        def review(payload: BaseModel, toolbox: Toolbox | None) -> ReviewReport:
            return ReviewReport(summary="reads well", problems=())

        worker = ScriptedWorker({Split: outline, ExecutorReport: write, ReviewReport: review})
        yes = NoulAnswer(noul=0.95)
        answers = [
            SizeCheck(fits=NoulAnswer(noul=0.05)),  # the handbook as a whole is too large...
            SizeCheck(fits=yes),  # ...its sections fit
            SplitCheck(smaller=yes),
            SectionReadiness(on_topic=yes, clear=yes, complete=yes),
            ConsistencyRelevance(relevant=yes),
            FactRelevance(relevant=yes),
            AudienceRelevance(relevant=yes),
            JoinCheck(accomplished=yes),
        ]
        services = ProjectServices(
            workers=FakeWorkers({"main": worker}),
            decider=FakeDecider(answers),
            decisions=InMemoryDecisions(),
            clock=FakeClock(),
        )
        return Project.open(ProjectConfig.model_validate(config), root, services)


class TestDocumentDomain:
    async def test_a_multi_section_document_goes_through_every_level(self, tmp_path: Path) -> None:
        project = Handbook.open(tmp_path)
        goal = project.create_artifact("Write a short handbook for running a book club")
        await project.run_until_idle()

        views = project.views()
        sections = views.children(goal.id)
        assert [s.id for s in sections] == ["a-0001.1", "a-0001.2"]
        assert all(views.artifact(s.id).level == "final" for s in sections)
        assert views.artifact(goal.id).level == "final"  # joined at reviewed, then final

        # Publishing needs a person. Sections are written one after another, so each may arrive in a batch of
        # its own: every batch the train asks about is approved.
        queue = Escalations(project.board, project.decisions)
        waiting = queue.open()
        assert waiting and all("needs human approval" in w.reason for w in waiting)
        for w in waiting:
            queue.resolve(w.artifact, Resolution.ACCEPT, "approved for publishing")
        await project.run_until_idle()

        views = project.views()
        assert all(views.artifact(s.id).level == "published" for s in sections)
        assert views.artifact(goal.id).level == "published"
        published = project.repository.files_at("published")
        assert {"sections/00-introduction.md", *SECTIONS} <= set(published)
        reviewers = {e.team for s in sections for e in views.history(s.id) if isinstance(e, SignedOff)}
        assert reviewers == {"consistency_checker", "fact_checker", "audience_reviewer"}
        project.close()
