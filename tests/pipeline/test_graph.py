# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportMissingTypeStubs=false
# pyright: reportArgumentType=false
import asyncio
import sqlite3
from pathlib import Path

import pytest
from langgraph.graph import END, START, StateGraph

from agentswarm.evaluation.papers import Candidate, Found, ReadingFolder
from agentswarm.pipeline.graph import DocumentPipeline
from agentswarm.pipeline.policy import Policy
from agentswarm.pipeline.run import Answer, AuditResult, Choice, DocumentRun, RunRequest, Stage
from agentswarm.pipeline.stages import Stages, Written


class Ledger:
    def __init__(self) -> None:
        self.usd = 0.0

    def spent(self, run: DocumentRun) -> float:
        return self.usd


class FakeSources:
    def __init__(self, ledger: Ledger) -> None:
        self._ledger = ledger
        self.calls = 0

    def find(self, run: DocumentRun, policy: Policy) -> str:
        self.calls += 1
        self._ledger.usd += 0.05
        run.sources.mkdir(parents=True, exist_ok=True)
        (run.sources / "a-paper.txt").write_text("A paper.")
        return "1. A paper"


class NestedSources(FakeSources):
    """Runs a LangGraph graph of its own, asynchronously, as a model call does (a LangChain agent runs on
    LangGraph): the first real run failed there, its agent borrowing the pipeline's checkpointer."""

    def find(self, run: DocumentRun, policy: Policy) -> str:
        graph = StateGraph(dict[str, int])
        graph.add_node("one", lambda state: {"n": 1})
        graph.add_edge(START, "one")
        graph.add_edge("one", END)
        result = asyncio.run(graph.compile().ainvoke({"n": 0}))
        assert result == {"n": 1}
        return super().find(run, policy)


class FlakySources(FakeSources):
    """Fails the first time, as a provider outage or a bug would."""

    def find(self, run: DocumentRun, policy: Policy) -> str:
        if self.calls == 0:
            self.calls += 1
            raise RuntimeError("the provider answered 503")
        return super().find(run, policy)


class ListingSources(FakeSources):
    """Finds one paper it can't download, with its reading list, as the real finder does."""

    def find(self, run: DocumentRun, policy: Policy) -> str:
        run.sources.mkdir(parents=True, exist_ok=True)
        gone = Candidate(id="W7", title="Gone", authors=("A. Author",), year=2024, abstract="")
        folder = ReadingFolder(topic="t", papers=(Found(paper=gone, why="", file=None),))
        folder.save(run.sources)
        return folder.render()


class FakeProject:
    """Writes, fails sections as told, and audits with the shares given, one per audit."""

    def __init__(self, ledger: Ledger, shares: list[int], failures: int = 0, empty: bool = False) -> None:
        self._ledger = ledger
        self._empty = empty  # writes leave no sections, as the first real run's assembly did
        self._shares = shares  # fully supported of 20, per audit
        self._failures = failures  # writes that leave a failed section
        self.writes = 0
        self.reviews = 0
        self.settled: list[Choice] = []

    def write(self, run: DocumentRun, policy: Policy, budget_left: float) -> Written:
        self.writes += 1
        self._ledger.usd += 0.25
        failed = ("a-0003: cites no source",) if self.writes <= self._failures else ()
        if self._empty:
            return Written(document=None, sections=0, failed=())
        return Written(document=run.folder / "document.md", sections=10, failed=failed)

    def settle(self, run: DocumentRun, choice: Choice) -> None:
        self.settled.append(choice)

    def audit(self, run: DocumentRun, policy: Policy) -> AuditResult:
        self._ledger.usd += 0.07
        supported = self._shares.pop(0)
        if supported < 0:  # no cited statement found
            return AuditResult(supported=0, partly=0, unsupported=0, unruled=0)
        return AuditResult(supported=supported, partly=20 - supported, unsupported=0, unruled=0)

    def review(self, run: DocumentRun, policy: Policy, budget_left: float) -> Written:
        self.reviews += 1
        self._ledger.usd += 1.5
        return Written(document=run.folder / "document.md", sections=10, failed=())


class FakePage:
    def publish(self, run: DocumentRun, policy: Policy) -> Path:
        return run.folder / "page.html"


def pipeline(project: FakeProject, ledger: Ledger, sources: FakeSources | None = None) -> DocumentPipeline:
    stages = Stages(sources or FakeSources(ledger), project, project, FakePage(), ledger)
    return DocumentPipeline(stages, Policy.load(), sqlite3.connect(":memory:", check_same_thread=False))


def request(budget: float = 3.0) -> RunRequest:
    return RunRequest(name="sleep", topic="sleep and memory", reader="a curious adult", budget=budget)


class TestDocumentPipeline:
    def test_a_run_pauses_for_the_sources_and_the_publishing_and_reviews_by_the_policy(
        self, tmp_path: Path
    ) -> None:
        ledger = Ledger()
        project = FakeProject(ledger, shares=[12, 17])  # 60% supported, then 85% after one review
        runs = pipeline(project, ledger)
        run = runs.start(request(), tmp_path)
        assert run.stage is Stage.SOURCES and project.writes == 0  # nothing written before the approval
        question = runs.waiting("sleep")
        assert (
            question is not None
            and question.title == "Approve the sources"
            and "1. A paper" in question.details
        )
        run = runs.answer("sleep", Answer(choice=Choice.YES))
        assert run.stage is Stage.PUBLISHING and project.writes == 1 and project.reviews == 1
        assert [a.supported for a in run.audits] == [12, 17] and run.reviews == 1
        question = runs.waiting("sleep")
        assert question is not None and "17 of 20" in question.details
        run = runs.answer("sleep", Answer(choice=Choice.YES))
        assert run.stage is Stage.PUBLISHED and run.page == tmp_path / "page.html"
        assert runs.waiting("sleep") is None and runs.runs() == ["sleep"]

    def test_no_review_when_the_audit_meets_the_threshold(self, tmp_path: Path) -> None:
        ledger = Ledger()
        project = FakeProject(ledger, shares=[16])  # 80%
        runs = pipeline(project, ledger)
        runs.start(request(), tmp_path)
        run = runs.answer("sleep", Answer(choice=Choice.YES))
        assert run.stage is Stage.PUBLISHING and project.reviews == 0

    def test_failed_sections_wait_for_the_users_choice(self, tmp_path: Path) -> None:
        ledger = Ledger()
        project = FakeProject(ledger, shares=[16], failures=1)
        runs = pipeline(project, ledger)
        runs.start(request(), tmp_path)
        runs.answer("sleep", Answer(choice=Choice.YES))
        question = runs.waiting("sleep")
        assert (
            question is not None
            and "cites no source" in question.details
            and Choice.ACCEPT in question.choices
        )
        run = runs.answer("sleep", Answer(choice=Choice.ACCEPT))
        assert project.settled == [Choice.ACCEPT] and project.writes == 2 and run.stage is Stage.PUBLISHING

    def test_a_stage_the_budget_wont_fit_pauses_until_it_is_raised(self, tmp_path: Path) -> None:
        ledger = Ledger()
        project = FakeProject(ledger, shares=[12, 17])
        runs = pipeline(project, ledger)
        runs.start(request(budget=0.6), tmp_path)
        runs.answer("sleep", Answer(choice=Choice.YES))  # writes and audits (0.37), then the review won't fit
        question = runs.waiting("sleep")
        assert (
            question is not None
            and question.title == "The budget would run out"
            and "review" in question.details
        )
        assert project.reviews == 0
        run = runs.answer("sleep", Answer(choice=Choice.BUDGET, budget=3.0))
        assert run.request.budget == 3.0 and project.reviews == 1 and run.stage is Stage.PUBLISHING

    def test_stop_ends_the_run_where_it_is(self, tmp_path: Path) -> None:
        ledger = Ledger()
        project = FakeProject(ledger, shares=[16])
        runs = pipeline(project, ledger)
        runs.start(request(), tmp_path)
        run = runs.answer("sleep", Answer(choice=Choice.STOP))
        assert run.stage is Stage.STOPPED and project.writes == 0 and runs.waiting("sleep") is None

    def test_a_run_name_is_used_once_and_only_a_waiting_run_takes_an_answer(self, tmp_path: Path) -> None:
        ledger = Ledger()
        runs = pipeline(FakeProject(ledger, shares=[16]), ledger)
        runs.start(request(), tmp_path)
        with pytest.raises(ValueError, match="exists already"):
            runs.start(request(), tmp_path)
        runs.answer("sleep", Answer(choice=Choice.STOP))
        with pytest.raises(ValueError, match="isn't waiting"):
            runs.answer("sleep", Answer(choice=Choice.YES))

    def test_a_stage_may_run_langgraph_itself(self, tmp_path: Path) -> None:
        ledger = Ledger()
        project = FakeProject(ledger, shares=[16])
        runs = pipeline(project, ledger, NestedSources(ledger))
        run = runs.start(request(), tmp_path)
        assert run.stage is Stage.SOURCES

    def test_a_run_stopped_by_an_error_says_so_and_retries_from_that_step(self, tmp_path: Path) -> None:
        ledger = Ledger()
        project = FakeProject(ledger, shares=[16])
        runs = pipeline(project, ledger, FlakySources(ledger))
        with pytest.raises(RuntimeError):
            runs.start(request(), tmp_path)
        failure = runs.failed("sleep")
        assert failure is not None and "find" in failure and "503" in failure
        assert "STOPPED BY AN ERROR" in runs.report("sleep") and "--choice retry" in runs.report("sleep")
        with pytest.raises(ValueError, match="isn't waiting"):
            runs.answer("sleep", Answer(choice=Choice.YES))  # only a retry restarts it
        run = runs.answer("sleep", Answer(choice=Choice.RETRY))
        assert run.stage is Stage.SOURCES and runs.failed("sleep") is None

    def test_sources_cannot_be_approved_while_the_folder_is_empty(self, tmp_path: Path) -> None:
        ledger = Ledger()
        project = FakeProject(ledger, shares=[16])
        runs = pipeline(project, ledger)
        runs.start(request(), tmp_path)
        (tmp_path / "sources" / "a-paper.txt").unlink()  # nothing downloaded, nothing added
        with pytest.raises(ValueError, match="no documents"):
            runs.answer("sleep", Answer(choice=Choice.YES))
        assert project.writes == 0 and runs.waiting("sleep") is not None
        (tmp_path / "sources" / "fetched-by-hand.pdf").write_text("x")
        assert runs.answer("sleep", Answer(choice=Choice.YES)).stage is Stage.PUBLISHING

    def test_the_sources_question_shows_the_folder_as_it_is_now(self, tmp_path: Path) -> None:
        ledger = Ledger()
        runs = pipeline(FakeProject(ledger, shares=[16]), ledger, ListingSources(ledger))
        runs.start(request(), tmp_path)
        assert "**not here yet**" in runs.report("sleep")
        (tmp_path / "sources" / "fetched.txt").write_text("Gone\nA. Author, the text.")
        assert "`fetched.txt`" in runs.report("sleep") and "not here yet" not in runs.report("sleep")

    def test_a_waiting_run_survives_a_restart(self, tmp_path: Path) -> None:
        ledger = Ledger()
        project = FakeProject(ledger, shares=[16])
        path = tmp_path / "runs.sqlite"
        first = DocumentPipeline.open(
            Stages(FakeSources(ledger), project, project, FakePage(), ledger), Policy.load(), path
        )
        first.start(request(), tmp_path)
        first.close()
        again = DocumentPipeline.open(
            Stages(FakeSources(ledger), project, project, FakePage(), ledger), Policy.load(), path
        )
        assert again.waiting("sleep") is not None
        run = again.answer("sleep", Answer(choice=Choice.YES))
        assert run.stage is Stage.PUBLISHING

    def test_a_writing_that_leaves_no_document_stops_the_run_before_any_check(self, tmp_path: Path) -> None:
        ledger = Ledger()
        project = FakeProject(ledger, shares=[16], empty=True)
        runs = pipeline(project, ledger)
        runs.start(request(), tmp_path)
        with pytest.raises(RuntimeError, match="no sections"):
            runs.answer("sleep", Answer(choice=Choice.YES))
        failure = runs.failed("sleep")
        assert failure is not None and "write" in failure and project.reviews == 0

    def test_an_audit_that_finds_no_citations_stops_the_run_instead_of_reviewing(
        self, tmp_path: Path
    ) -> None:
        ledger = Ledger()
        project = FakeProject(ledger, shares=[-1])  # 0 of 0 counted as 0% once, and a $1.35 review followed
        runs = pipeline(project, ledger)
        runs.start(request(), tmp_path)
        with pytest.raises(RuntimeError, match="no cited statements"):
            runs.answer("sleep", Answer(choice=Choice.YES))
        assert project.reviews == 0
