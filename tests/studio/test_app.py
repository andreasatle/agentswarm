import sqlite3
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from agentswarm.evaluation.papers import Candidate, Found, ReadingFolder
from agentswarm.monitor.boards import Watched
from agentswarm.pipeline.graph import DocumentPipeline
from agentswarm.pipeline.policy import Policy
from agentswarm.pipeline.run import DocumentRun, Stage
from agentswarm.pipeline.stages import Stages
from agentswarm.studio.app import Studio
from agentswarm.studio.worker import ThreadWorker
from tests.monitor.test_monitor import CONFIG, Demo
from tests.pipeline.test_graph import FakePage, FakeProject, FakeSources, FlakySources, Ledger


class InlineWorker:
    """Runs each step at once, so a test sees its result on the next page."""

    def __init__(self) -> None:
        self._errors: dict[str, str] = {}

    def submit(self, name: str, label: str, work: Callable[[], object]) -> None:
        self._errors.pop(name, None)
        try:
            work()
        except Exception as error:
            self._errors[name] = f"{type(error).__name__}: {error}"

    def busy(self, name: str) -> str | None:
        return None

    def error(self, name: str) -> str | None:
        return self._errors.get(name)


class HostileSources(FakeSources):
    """A found paper whose title carries HTML, as a search result may."""

    def find(self, run: DocumentRun, policy: Policy) -> str:
        super().find(run, policy)
        paper = Candidate(
            id="W1", title="<script>alert(1)</script>", authors=("A. Author",), year=2024, abstract=""
        )
        folder = ReadingFolder(topic="t", papers=(Found(paper=paper, why="", file=None),))
        folder.save(run.sources)
        return folder.render()


class Fixture:
    def __init__(self, tmp_path: Path, sources: FakeSources | None = None, **projects: Watched) -> None:
        self.ledger = Ledger()
        self.project = FakeProject(self.ledger, shares=[16])
        self.page = FakePage()
        stages = Stages(
            sources or FakeSources(self.ledger), self.project, self.project, self.page, self.ledger
        )
        self.pipeline = DocumentPipeline(
            stages, Policy.load(), sqlite3.connect(":memory:", check_same_thread=False)
        )
        self.runs = tmp_path / "runs"
        studio = Studio(self.pipeline, Policy.load(), self.runs, InlineWorker(), projects)
        self.client = TestClient(studio.app)

    def start(self, **fields: str) -> Any:
        form = {"topic": "sleep and memory", "reader": "a curious adult", "budget": "2"} | fields
        return self.client.post("/new", data=form, follow_redirects=False)

    def answer(self, choice: str, **fields: str) -> str:
        reply = self.client.post("/runs/sleep-and-memory/answer", data={"choice": choice} | fields)
        return reply.text


class TestStudio:
    def test_a_run_from_the_form_to_the_page(self, tmp_path: Path) -> None:
        f = Fixture(tmp_path)
        assert "Expected cost" in f.client.get("/new").text
        reply = f.start()
        assert reply.status_code == 303 and reply.headers["location"] == "/runs/sleep-and-memory"
        page = f.client.get("/runs/sleep-and-memory").text
        assert "Waiting for you" in page and "Approve the sources" in page and "Add papers" in page
        assert "sleep and memory" in f.client.get("/").text
        page = f.answer("yes")
        assert "Approve publishing" in page and "80%" in page and f.project.writes == 1
        (f.runs / "sleep-and-memory" / "document.md").write_text("# Sleep\n\nIt helps.\n")
        assert "It helps." in f.client.get("/runs/sleep-and-memory/document").text
        page = f.answer("yes")
        assert "The page is written" in page and "homepage" not in page
        assert "page written" in f.client.get("/").text  # a finished run in the list shows its stage

    def test_the_form_says_what_is_wrong_and_keeps_what_was_typed(self, tmp_path: Path) -> None:
        f = Fixture(tmp_path)
        reply = f.start(budget="0")
        assert reply.status_code == 400 and "budget" in reply.text and "a curious adult" in reply.text
        f.start()
        reply = f.start()
        assert reply.status_code == 400 and "exists already" in reply.text

    def test_the_plan_warns_when_the_budget_wont_cover_every_step(self, tmp_path: Path) -> None:
        f = Fixture(tmp_path)
        assert "may not cover" in f.client.get("/plan", params={"words": "5000", "budget": "0.5"}).text
        assert "Within the budget" in f.client.get("/plan", params={"words": "5000", "budget": "9"}).text

    def test_papers_fetched_by_hand_keep_their_names(self, tmp_path: Path) -> None:
        f = Fixture(tmp_path)
        f.start()
        files = [("files", ("bhn155.pdf", b"%PDF-1.4", "application/pdf"))]
        page = f.client.post("/runs/sleep-and-memory/files", files=files).text
        assert "added bhn155.pdf" in page
        assert (f.runs / "sleep-and-memory" / "sources" / "bhn155.pdf").read_bytes() == b"%PDF-1.4"

    def test_an_answer_the_run_cant_take_is_explained_and_nothing_runs(self, tmp_path: Path) -> None:
        f = Fixture(tmp_path)
        f.start()
        (f.runs / "sleep-and-memory" / "sources" / "a-paper.txt").unlink()
        page = f.answer("yes")
        assert "no documents in" in page and f.project.writes == 0
        assert "can&#39;t take budget" in f.answer("budget", budget="3")

    def test_an_error_is_shown_with_a_retry(self, tmp_path: Path) -> None:
        f = Fixture(tmp_path, FlakySources(Ledger()))
        f.start()
        page = f.client.get("/runs/sleep-and-memory").text
        assert "Stopped by an error" in page and "503" in page and 'value="retry"' in page
        assert "Approve the sources" in f.answer("retry")

    def test_html_in_a_found_title_is_shown_as_text(self, tmp_path: Path) -> None:
        f = Fixture(tmp_path, HostileSources(Ledger()))
        f.start()
        page = f.client.get("/runs/sleep-and-memory").text
        assert "<script>alert" not in page and "&lt;script&gt;alert" in page

    def test_the_board_view_serves_its_page_and_data_and_only_reads(self, tmp_path: Path) -> None:
        w = Demo(tmp_path)
        before = w.h.log.head()
        later = CONFIG.model_copy(update={"project": "later"})
        f = Fixture(
            tmp_path,
            demo=Watched(config=CONFIG, board=tmp_path / "events.sqlite"),
            later=Watched(config=later, board=tmp_path / "not-started" / "blackboard.sqlite"),
        )
        assert "AgentSwarm board" in f.client.get("/board").text
        rows: list[dict[str, Any]] = f.client.get("/api/projects").json()
        assert [(r["project"], r["started"], r["seen"]) for r in rows] == [
            ("demo", True, 0.4),
            ("later", False, None),
        ]
        assert f.client.get("/api/projects/demo/state").json()["overview"]["project"] == "demo"
        assert f.client.get(f"/api/projects/demo/artifact/{w.merged.id}").json()["id"] == w.merged.id
        assert f.client.get("/api/projects/later/state").status_code == 404
        assert w.h.log.head() == before

    def test_a_document_runs_board_is_shown_once_it_has_one(self, tmp_path: Path) -> None:
        f = Fixture(tmp_path)
        f.start()
        assert f.client.get("/api/projects").json() == []  # no configuration yet
        run = f.pipeline.run("sleep-and-memory")
        run.project.mkdir(parents=True)
        (run.project / "project.yaml").write_text(
            CONFIG.model_copy(update={"project": "doc-x"}).model_dump_json()
        )
        assert [r["project"] for r in f.client.get("/api/projects").json()] == ["doc-x"]
        assert run.stage is Stage.SOURCES


class TestThreadWorker:
    def test_one_step_at_a_time_per_run_and_errors_kept(self) -> None:
        worker = ThreadWorker()
        release, finished = threading.Event(), threading.Event()

        def slow() -> None:
            release.wait(5)

        def failing() -> None:
            try:
                raise RuntimeError("the provider answered 503")
            finally:
                finished.set()

        worker.submit("a", "writing", slow)
        assert worker.busy("a") == "writing"
        try:
            worker.submit("a", "again", slow)
            raise AssertionError("a busy run took a second step")
        except ValueError as error:
            assert "busy writing" in str(error)
        release.set()
        worker.submit("b", "finding sources", failing)
        finished.wait(5)
        for _ in range(100):
            if worker.busy("a") is None and worker.busy("b") is None:
                break
            threading.Event().wait(0.01)
        assert worker.busy("a") is None and worker.error("b") == "RuntimeError: the provider answered 503"
