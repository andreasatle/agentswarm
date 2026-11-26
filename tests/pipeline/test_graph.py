# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportMissingTypeStubs=false
# pyright: reportArgumentType=false
import asyncio
import sqlite3
from pathlib import Path

import pytest
from langgraph.graph import END, START, StateGraph

from agentswarm.evaluation.papers import Candidate, Found, ReadingFolder
from agentswarm.evaluation.redteam import Finding, FlawKind, RedTeamReport
from agentswarm.llm.workers import WorkerError
from agentswarm.pipeline.graph import DocumentPipeline
from agentswarm.pipeline.policy import Policy
from agentswarm.pipeline.run import (
    Answer,
    AuditResult,
    Choice,
    DocumentRun,
    Revision,
    RunRequest,
    Stage,
    Working,
)
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

    def scope(self, run: DocumentRun, policy: Policy) -> tuple[str, ...]:
        self._ledger.usd += 0.01
        return ("How does sleep consolidate memory?", "What does a nap do?", "Who studied it first?")

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

    def __init__(
        self, ledger: Ledger, shares: list[int], failures: int = 0, empty: bool = False, held: int = 0
    ) -> None:
        self._held = held  # writes a limit cuts short
        self._ledger = ledger
        self._empty = empty  # writes leave no sections, as the first real run's assembly did
        self._shares = shares  # fully supported of 20, per audit
        self._failures = failures  # writes that leave a failed section
        self.writes = 0
        self.reviews = 0
        self.settled: list[Choice] = []
        self.flaws: list[Finding] = []  # what the red team finds at each look
        self.attacks = 0
        self.revisions: list[Revision] = []
        self.broken = False  # the red team's model fails

    def write(self, run: DocumentRun, policy: Policy, budget_left: float) -> Written:
        self.writes += 1
        self._ledger.usd += 0.25
        failed = ("a-0003: cites no source",) if self.writes <= self._failures else ()
        if self._empty:
            return Written(document=None, sections=0, failed=())
        if self.writes <= self._held:
            limit = "a model call won't fit the machine-wide limit: $4.84 of $5.00 spent today"
            return Written(document=run.folder / "document.md", sections=9, failed=(), held=limit)
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

    def attack(self, run: DocumentRun, policy: Policy) -> RedTeamReport:
        self.attacks += 1
        self._ledger.usd += 0.04
        if self.broken:
            raise WorkerError("the provider answered 503")
        return RedTeamReport(shown=tuple(self.flaws), unshown=())

    def revise(self, run: DocumentRun, policy: Policy, budget_left: float, revision: Revision) -> Written:
        self.revisions.append(revision)
        self._ledger.usd += 0.3
        self.flaws = []  # fixed
        return Written(
            document=run.folder / "document.md", sections=10, failed=(), unplaced=("References: a flaw",)
        )


class FakePage:
    def publish(self, run: DocumentRun, policy: Policy) -> Path:
        return run.folder / "page.html"


def pipeline(project: FakeProject, ledger: Ledger, sources: FakeSources | None = None) -> DocumentPipeline:
    stages = Stages(sources or FakeSources(ledger), project, project, FakePage(), ledger)
    return DocumentPipeline(stages, Policy.load(), sqlite3.connect(":memory:", check_same_thread=False))


def begin(runs: DocumentPipeline, request: RunRequest, folder: Path) -> DocumentRun:
    """Started, with its questions approved as planned: on to the sources."""
    runs.start(request, folder)
    return runs.answer(request.name, Answer(choice=Choice.YES))


def request(budget: float = 3.0) -> RunRequest:
    return RunRequest(name="sleep", topic="sleep and memory", reader="a curious adult", budget=budget)


class TestDocumentPipeline:
    def test_a_run_pauses_for_the_sources_and_the_publishing_and_reviews_by_the_policy(
        self, tmp_path: Path
    ) -> None:
        ledger = Ledger()
        project = FakeProject(ledger, shares=[12, 17])  # 60% supported, then 85% after one review
        runs = pipeline(project, ledger)
        run = begin(runs, request(), tmp_path)
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
        later = RunRequest(name="awake", topic="awake", reader="r", budget=3.0)
        runs.start(later, tmp_path / "awake")  # by its latest step, not by name: "awake" is newer
        assert runs.runs() == ["sleep", "awake"]

    def test_a_title_is_given_only_when_publishing_and_noted(self, tmp_path: Path) -> None:
        ledger = Ledger()
        runs = pipeline(FakeProject(ledger, shares=[19, 19]), ledger)
        begin(runs, request(), tmp_path)
        with pytest.raises(ValueError, match="only when publishing"):
            runs.answer("sleep", Answer(choice=Choice.YES, title="Sleep"))
        runs.answer("sleep", Answer(choice=Choice.YES))
        (tmp_path / "document.md").write_text("# Sleep and memory: a long title\n\nText.\n")
        with pytest.raises(ValueError, match="one line"):
            runs.answer("sleep", Answer(choice=Choice.YES, title="Two\nlines"))
        with pytest.raises(ValueError, match="only when publishing"):
            runs.answer("sleep", Answer(choice=Choice.REVISE, title="Sleep"))
        run = runs.answer("sleep", Answer(choice=Choice.YES, title="Sleep and memory"))
        assert run.title == "Sleep and memory" and "title set by you: Sleep and memory" in run.notes

    def test_the_questions_are_approved_before_anything_is_searched_and_bind_the_search(
        self, tmp_path: Path
    ) -> None:
        ledger = Ledger()
        sources = FakeSources(ledger)
        runs = pipeline(FakeProject(ledger, shares=[19, 19]), ledger, sources)
        run = runs.start(request(), tmp_path)
        question = runs.waiting("sleep")
        assert run.stage is Stage.SCOPE and sources.calls == 0  # nothing searched yet
        assert question is not None and question.title == "Approve the questions"
        assert question.items == run.questions and "2. What does a nap do?" in question.details
        assert "--drop" in runs.report("sleep")
        run = runs.answer("sleep", Answer(choice=Choice.YES, drop=(2,)))
        assert run.questions == ("How does sleep consolidate memory?", "Who studied it first?")
        assert sources.calls == 1 and run.stage is Stage.SOURCES

    def test_leaving_out_a_question_that_isnt_there_or_every_question_is_refused(
        self, tmp_path: Path
    ) -> None:
        ledger = Ledger()
        runs = pipeline(FakeProject(ledger, shares=[19, 19]), ledger)
        runs.start(request(), tmp_path)
        with pytest.raises(ValueError, match="no question numbered 4"):
            runs.answer("sleep", Answer(choice=Choice.YES, drop=(4,)))
        with pytest.raises(ValueError, match="nothing to write"):
            runs.answer("sleep", Answer(choice=Choice.YES, drop=(1, 2, 3)))
        run = runs.answer("sleep", Answer(choice=Choice.YES))
        with pytest.raises(ValueError, match="nothing to leave out"):
            runs.answer("sleep", Answer(choice=Choice.YES, drop=(1,)))
        assert run.stage is Stage.SOURCES

    def test_the_users_own_questions_are_added_to_those_kept(self, tmp_path: Path) -> None:
        ledger = Ledger()
        runs = pipeline(FakeProject(ledger, shares=[19, 19]), ledger)
        runs.start(request(), tmp_path)
        run = runs.answer("sleep", Answer(choice=Choice.YES, drop=(1, 2, 3), add=("  Why do  we dream? ",)))
        assert run.questions == ("Why do we dream?",) and "your questions added: 1" in run.notes
        with pytest.raises(ValueError, match="isn't asking for questions"):
            runs.answer("sleep", Answer(choice=Choice.YES, add=("Another?",)))

    def test_stopping_at_the_questions_spends_nothing_on_the_search(self, tmp_path: Path) -> None:
        ledger = Ledger()
        sources = FakeSources(ledger)
        runs = pipeline(FakeProject(ledger, shares=[19, 19]), ledger, sources)
        runs.start(request(), tmp_path)
        run = runs.answer("sleep", Answer(choice=Choice.STOP))
        assert run.stage is Stage.STOPPED and sources.calls == 0 and ledger.usd == pytest.approx(0.01)

    def test_a_document_whose_citations_hold_up_is_still_reviewed_once_for_its_reader(
        self, tmp_path: Path
    ) -> None:
        ledger = Ledger()
        project = FakeProject(ledger, shares=[19, 19])  # 95%: before 2026-11-13 it skipped all review
        runs = pipeline(project, ledger)
        begin(runs, request(), tmp_path)
        run = runs.answer("sleep", Answer(choice=Choice.YES))
        assert run.stage is Stage.PUBLISHING and project.reviews == 1 and run.reviews == 1

    def test_failed_sections_wait_for_the_users_choice(self, tmp_path: Path) -> None:
        ledger = Ledger()
        project = FakeProject(ledger, shares=[19, 19], failures=1)
        runs = pipeline(project, ledger)
        begin(runs, request(), tmp_path)
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
        begin(runs, request(budget=0.6), tmp_path)
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
        project = FakeProject(ledger, shares=[19, 19])
        runs = pipeline(project, ledger)
        begin(runs, request(), tmp_path)
        run = runs.answer("sleep", Answer(choice=Choice.STOP))
        assert run.stage is Stage.STOPPED and project.writes == 0 and runs.waiting("sleep") is None

    def test_a_run_name_is_used_once_and_only_a_waiting_run_takes_an_answer(self, tmp_path: Path) -> None:
        ledger = Ledger()
        runs = pipeline(FakeProject(ledger, shares=[19, 19]), ledger)
        begin(runs, request(), tmp_path)
        with pytest.raises(ValueError, match="exists already"):
            runs.start(request(), tmp_path)
        runs.answer("sleep", Answer(choice=Choice.STOP))
        with pytest.raises(ValueError, match="isn't waiting"):
            runs.answer("sleep", Answer(choice=Choice.YES))

    def test_a_stage_may_run_langgraph_itself(self, tmp_path: Path) -> None:
        ledger = Ledger()
        project = FakeProject(ledger, shares=[19, 19])
        runs = pipeline(project, ledger, NestedSources(ledger))
        run = begin(runs, request(), tmp_path)
        assert run.stage is Stage.SOURCES

    def test_a_run_stopped_by_an_error_says_so_and_retries_from_that_step(self, tmp_path: Path) -> None:
        ledger = Ledger()
        project = FakeProject(ledger, shares=[19, 19])
        runs = pipeline(project, ledger, FlakySources(ledger))
        runs.start(request(), tmp_path)
        with pytest.raises(RuntimeError):
            runs.answer("sleep", Answer(choice=Choice.YES))
        failure = runs.failed("sleep")
        assert failure is not None and "find" in failure and "503" in failure
        assert "STOPPED BY AN ERROR" in runs.report("sleep") and "--choice retry" in runs.report("sleep")
        with pytest.raises(ValueError, match="isn't waiting"):
            runs.answer("sleep", Answer(choice=Choice.YES))  # only a retry restarts it
        run = runs.answer("sleep", Answer(choice=Choice.RETRY))
        assert run.stage is Stage.SOURCES and runs.failed("sleep") is None

    def test_sources_cannot_be_approved_while_the_folder_is_empty(self, tmp_path: Path) -> None:
        ledger = Ledger()
        project = FakeProject(ledger, shares=[19, 19])
        runs = pipeline(project, ledger)
        begin(runs, request(), tmp_path)
        (tmp_path / "sources" / "a-paper.txt").unlink()  # nothing downloaded, nothing added
        with pytest.raises(ValueError, match="no documents"):
            runs.answer("sleep", Answer(choice=Choice.YES))
        assert project.writes == 0 and runs.waiting("sleep") is not None
        (tmp_path / "sources" / "fetched-by-hand.pdf").write_text("x")
        assert runs.answer("sleep", Answer(choice=Choice.YES)).stage is Stage.PUBLISHING

    def test_the_sources_question_shows_the_folder_as_it_is_now(self, tmp_path: Path) -> None:
        ledger = Ledger()
        runs = pipeline(FakeProject(ledger, shares=[19, 19]), ledger, ListingSources(ledger))
        begin(runs, request(), tmp_path)
        assert "**not here yet**" in runs.report("sleep")
        (tmp_path / "sources" / "fetched.txt").write_text("Gone\nA. Author, the text.")
        assert "`fetched.txt`" in runs.report("sleep") and "not here yet" not in runs.report("sleep")

    def test_a_waiting_run_survives_a_restart(self, tmp_path: Path) -> None:
        ledger = Ledger()
        project = FakeProject(ledger, shares=[19, 19])
        path = tmp_path / "runs.sqlite"
        first = DocumentPipeline.open(
            Stages(FakeSources(ledger), project, project, FakePage(), ledger), Policy.load(), path
        )
        begin(first, request(), tmp_path)
        first.close()
        again = DocumentPipeline.open(
            Stages(FakeSources(ledger), project, project, FakePage(), ledger), Policy.load(), path
        )
        assert again.waiting("sleep") is not None
        run = again.answer("sleep", Answer(choice=Choice.YES))
        assert run.stage is Stage.PUBLISHING

    def test_a_run_started_with_its_own_policy_keeps_it_after_a_restart(self, tmp_path: Path) -> None:
        own = tmp_path / "policy.yaml"
        own.write_text(Policy.DEFAULT.read_text() + "review_rounds: 0\n")
        ledger = Ledger()
        project = FakeProject(ledger, shares=[19])
        path = tmp_path / "runs.sqlite"
        first = DocumentPipeline.open(
            Stages(FakeSources(ledger), project, project, FakePage(), ledger), Policy.load(own), path
        )
        begin(first, request().model_copy(update={"policy": own}), tmp_path / "run")
        first.close()
        again = DocumentPipeline.open(  # `approve` opens the pipeline with the default policy
            Stages(FakeSources(ledger), project, project, FakePage(), ledger), Policy.load(), path
        )
        run = again.answer("sleep", Answer(choice=Choice.YES))
        assert run.stage is Stage.PUBLISHING and project.writes == 1
        assert project.reviews == 0  # the run's policy: no review rounds; the default would review once

    def test_a_writing_that_leaves_no_document_stops_the_run_before_any_check(self, tmp_path: Path) -> None:
        ledger = Ledger()
        project = FakeProject(ledger, shares=[19, 19], empty=True)
        runs = pipeline(project, ledger)
        begin(runs, request(), tmp_path)
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
        begin(runs, request(), tmp_path)
        with pytest.raises(RuntimeError, match="no cited statements"):
            runs.answer("sleep", Answer(choice=Choice.YES))
        assert project.reviews == 0


class TestCutShort:
    def test_a_step_a_limit_cuts_short_waits_to_be_retried_not_carried_on(self, tmp_path: Path) -> None:
        ledger = Ledger()
        project = FakeProject(ledger, shares=[19, 19], held=1)
        runs = pipeline(project, ledger)
        begin(runs, request(), tmp_path)
        run = runs.answer("sleep", Answer(choice=Choice.YES))
        question = runs.waiting("sleep")
        assert question is not None and question.title == "A spending limit stopped the run"
        assert "machine-wide limit" in question.details and Choice.RETRY in question.choices
        assert run.audits == () and run.blocked == "write"  # nothing audited or reviewed on a cut-short draft
        run = runs.answer("sleep", Answer(choice=Choice.RETRY))
        assert project.writes == 2 and run.stage is Stage.PUBLISHING and run.held is None


class TestRunning:
    def test_a_step_running_elsewhere_reads_as_running_and_takes_no_answer(self, tmp_path: Path) -> None:
        ledger = Ledger()
        runs = pipeline(FakeProject(ledger, shares=[19, 19]), ledger)
        begin(runs, request(), tmp_path)
        assert not (tmp_path / Working.NAME).exists()  # the marker goes when the step ends
        with Working(tmp_path):
            assert runs.view("sleep").running and "running" in runs.line("sleep")
            with pytest.raises(ValueError, match="running a step now"):
                runs.answer("sleep", Answer(choice=Choice.YES))
        assert not runs.view("sleep").running

    def test_a_marker_left_by_a_dead_process_is_not_running(self, tmp_path: Path) -> None:
        (tmp_path / Working.NAME).write_text("999999999")
        assert not Working(tmp_path).active()


def flaw(evidence: str, kind: FlawKind = FlawKind.REVISE) -> Finding:
    return Finding(
        where="Batching", evidence=evidence, flaw="The average is wrong.", fix="Recompute it.", kind=kind
    )


class TestTheUsersChanges:
    """The publishing pause: the red team's findings to keep or untick, the user's own instructions, one
    round of changes, and back to the pause."""

    def ready(self, tmp_path: Path, project: FakeProject, ledger: Ledger) -> DocumentPipeline:
        runs = pipeline(project, ledger)
        begin(runs, request(), tmp_path)
        (tmp_path / "document.md").write_text("## Batching\n\nEach expert receives Bk/E tokens on average.\n")
        runs.answer("sleep", Answer(choice=Choice.YES))
        return runs

    def test_the_red_team_looks_last_and_its_findings_wait_for_the_user(self, tmp_path: Path) -> None:
        ledger = Ledger()
        project = FakeProject(ledger, shares=[12, 17])
        project.flaws = [
            flaw("Each expert receives Bk/E tokens"),
            flaw("[^3]: plato.stanford.edu", FlawKind.FRAMEWORK),
        ]
        runs = self.ready(tmp_path, project, ledger)
        question = runs.waiting("sleep")
        assert project.attacks == 1 and question is not None and question.instructions
        assert question.items == ('Batching: The average is wrong. ("Each expert receives Bk/E tokens")',)
        assert "other remarks" in question.details and Choice.REVISE in question.choices
        assert "--choice revise" in runs.report("sleep")

    def test_revise_makes_the_ticked_findings_and_the_instructions_then_comes_back(
        self, tmp_path: Path
    ) -> None:
        ledger = Ledger()
        project = FakeProject(ledger, shares=[12, 17, 18])
        project.flaws = [flaw("Each expert receives Bk/E tokens"), flaw("tokens on average")]
        runs = self.ready(tmp_path, project, ledger)
        run = runs.answer(
            "sleep", Answer(choice=Choice.REVISE, drop=(2,), add=(" No  'first', 'second' in bullets ",))
        )
        assert (
            project.revisions
            == [
                Revision(
                    findings=(project_flaw := flaw("Each expert receives Bk/E tokens"),),
                    instructions=("No 'first', 'second' in bullets",),
                )
            ]
            and project_flaw.kind is FlawKind.REVISE
        )
        assert run.stage is Stage.PUBLISHING and run.revisions == 1 and run.revision is None
        assert [a.supported for a in run.audits] == [
            12,
            17,
            18,
        ] and project.attacks == 2  # audited, looked at again
        assert any("not made, no section holds it: References" in n for n in run.notes)
        question = runs.waiting("sleep")
        assert (
            question is not None and question.items == () and "your rounds of changes: 1" in question.details
        )
        assert runs.answer("sleep", Answer(choice=Choice.YES)).stage is Stage.PUBLISHED

    def test_answers_that_cant_be_made_are_refused(self, tmp_path: Path) -> None:
        ledger = Ledger()
        project = FakeProject(ledger, shares=[12, 17])
        project.flaws = [flaw("Each expert receives Bk/E tokens")]
        runs = self.ready(tmp_path, project, ledger)
        with pytest.raises(ValueError, match="nothing to revise"):
            runs.answer("sleep", Answer(choice=Choice.REVISE, drop=(1,)))
        with pytest.raises(ValueError, match="go with revise"):
            runs.answer("sleep", Answer(choice=Choice.YES, add=("Shorter, please",)))
        with pytest.raises(ValueError, match="isn't in the document word for word"):
            runs.answer(
                "sleep", Answer(choice=Choice.REVISE, add=('Fix "the experts are idle most of the time"',))
            )
        with pytest.raises(ValueError, match="no finding numbered 2"):
            runs.answer("sleep", Answer(choice=Choice.REVISE, drop=(2,)))
        assert project.revisions == []
        # unticked findings sent with yes (as the studio's form does) are only ignored
        assert runs.answer("sleep", Answer(choice=Choice.YES, drop=(1,))).stage is Stage.PUBLISHED

    def test_a_red_team_that_fails_leaves_the_pause_without_findings(self, tmp_path: Path) -> None:
        ledger = Ledger()
        project = FakeProject(ledger, shares=[12, 17])
        project.broken = True
        runs = self.ready(tmp_path, project, ledger)
        run = runs.run("sleep")
        question = runs.waiting("sleep")
        assert run.stage is Stage.PUBLISHING and question is not None and question.items == ()
        assert any(n.startswith("the red team failed") for n in run.notes)
