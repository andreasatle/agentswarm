import sys
from pathlib import Path
from typing import ClassVar, Literal

from pydantic import BaseModel

from agentswarm.core.config import CheckConfig, JevMode
from agentswarm.core.domain import Escalated, Event, FindingAdded, FindingKind, Severity, SignedOff, TeamName
from agentswarm.gating.checks import CommandCheck
from agentswarm.jev.api import NoulAnswer
from agentswarm.judging.deciders import FakeDecider
from agentswarm.judging.decisions import OutcomeSource
from agentswarm.judging.judgement import Judgement
from agentswarm.judging.judges import IntegrationRelevance, Judge
from agentswarm.judging.policy import Policy
from agentswarm.llm.workers import FakeWorker, Step, Toolbox, WorkerError
from agentswarm.teams.analyzer import (
    AnalysisReport,
    AnalysisToolbox,
    Analyzer,
    ProbingAnalyzer,
    QuotedProblem,
    ReadingToolbox,
    ReportedProblem,
    ReviewingAnalyzer,
    ReviewReport,
)
from agentswarm.teams.relevance import RelevanceFilter
from agentswarm.teams.runner import Relevance
from agentswarm.workspace.sandbox import LocalSandbox
from tests.support.harness import Harness
from tests.support.samples import Samples

PYTEST = CheckConfig(command=(sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"))
FAREWELL = 'def farewell(name):\n    return f"Goodbye, {name}!"\n'
PROBE = TeamName("probe")


class ProbeAnalyzer(ProbingAnalyzer):
    INSTRUCTIONS: ClassVar[str] = "Probe the change."
    KIND: ClassVar[FindingKind] = FindingKind.TEST_FAILURE


class Merged:
    """A harness whose artifact has been promoted into experimental by the real promoter."""

    def __init__(
        self, tmp_path: Path, mode: JevMode = JevMode.SHADOW, p_relevant: float | None = None
    ) -> None:
        self.h = Harness(tmp_path)
        self.artifact = self.h.create(task="Add a farewell function")
        self.h.push(self.artifact, {"greeter.py": FAREWELL})
        assert self.h.promoter.step() == 1
        self.runner_check = CommandCheck("pytest", PYTEST, LocalSandbox())
        self.relevance: RelevanceFilter | None = None
        if p_relevant is not None:
            judge = Judge(IntegrationRelevance)
            answers = IntegrationRelevance(relevant=NoulAnswer(noul=p_relevant))
            judgement = Judgement(
                judge, Policy(judge), FakeDecider([answers]), self.h.decisions, mode, self.h.clock
            )
            self.relevance = RelevanceFilter(PROBE, judgement, self.h.decisions, self.h.clock)

    def analyzer(self, *steps: Step[BaseModel]) -> ProbeAnalyzer:
        return ProbeAnalyzer(
            PROBE, Samples.EXPERIMENTAL, FakeWorker(steps), self.runner_check, self.relevance
        )

    async def run(self, analyzer: Analyzer) -> list[Event]:
        assert await self.h.runner(analyzer).step() == 1
        return list(self.h.board.current().history(self.artifact.id))


class Steps:
    @staticmethod
    def report(tests: dict[str, str], problems: list[ReportedProblem]) -> Step[BaseModel]:
        def step(toolbox: Toolbox | None) -> AnalysisReport:
            assert isinstance(toolbox, AnalysisToolbox)
            assert "farewell" in toolbox.show_change()
            for name, content in tests.items():
                toolbox.run_test(name, content)
            return AnalysisReport(summary="probed", problems=tuple(problems))

        return step


FAILING = "from greeter import farewell\n\n\ndef test_empty():\n    assert farewell('') == 'Goodbye!'\n"
PASSING = "from greeter import farewell\n\n\ndef test_ada():\n    assert farewell('Ada') == 'Goodbye, Ada!'\n"


def problem(test: str, severity: Literal["major", "minor"] = "major") -> ReportedProblem:
    return ReportedProblem(
        title="Empty names", detail="An empty name greets nobody.", severity=severity, test=test
    )


class TestAnalyzer:
    async def test_reports_problems_with_failing_tests_and_signs_off(self, tmp_path: Path) -> None:
        m = Merged(tmp_path)
        analyzer = m.analyzer(Steps.report({"test_empty.py": FAILING}, [problem("test_empty.py")]))
        history = await m.run(analyzer)
        (added,) = [e for e in history if isinstance(e, FindingAdded)]
        assert added.finding.team == PROBE and added.finding.level == Samples.EXPERIMENTAL
        assert added.finding.severity is Severity.MAJOR
        assert "def test_empty" in added.finding.detail
        assert isinstance(history[-2], SignedOff)  # then the runner's release
        assert m.h.board.current().signed_off(m.artifact.id, PROBE)

    async def test_drops_problems_without_a_failing_test(self, tmp_path: Path) -> None:
        m = Merged(tmp_path)
        claims = [problem("test_ada.py"), problem("test_never_run.py")]
        history = await m.run(m.analyzer(Steps.report({"test_ada.py": PASSING}, claims)))
        assert not any(isinstance(e, FindingAdded) for e in history)
        assert any(isinstance(e, SignedOff) for e in history)

    async def test_examines_each_version_once(self, tmp_path: Path) -> None:
        m = Merged(tmp_path)
        analyzer = m.analyzer(Steps.report({}, []))
        await m.run(analyzer)
        assert await m.h.runner(analyzer).step() == 0  # signed off: nothing new to examine
        assert analyzer.relevant(m.h.contexts.build(m.artifact.id)) is Relevance.NO

    async def test_ignores_artifacts_not_yet_merged(self, tmp_path: Path) -> None:
        h = Harness(tmp_path)
        artifact = h.create()
        analyzer = ProbeAnalyzer(
            PROBE, Samples.RAW, FakeWorker([]), CommandCheck("t", PYTEST, LocalSandbox())
        )
        assert analyzer.relevant(h.contexts.build(artifact.id)) is Relevance.NO

    async def test_a_worker_failure_escalates_without_signing_off(self, tmp_path: Path) -> None:
        m = Merged(tmp_path)

        def fail(toolbox: Toolbox | None) -> AnalysisReport:
            raise WorkerError("model down")

        history = await m.run(m.analyzer(fail))
        escalations = [e for e in history if isinstance(e, Escalated)]
        assert len(escalations) == 1 and not escalations[0].parks  # a notice: other reviewers carry on
        views = m.h.board.current()
        assert not views.signed_off(m.artifact.id, PROBE)
        assert views.active(m.artifact.id)
        assert views.open_escalation(m.artifact.id) is not None  # still waits for a person


class TestRelevanceFilter:
    async def test_shadow_mode_works_anyway_and_records_the_team_result(self, tmp_path: Path) -> None:
        m = Merged(tmp_path, JevMode.SHADOW, p_relevant=0.05)
        await m.run(m.analyzer(Steps.report({"test_empty.py": FAILING}, [problem("test_empty.py")])))
        (relevance,) = [d for d in m.h.decisions.all() if d.point == "relevance:probe"]
        assert relevance.ruling.verdict == "reject"  # Jev said not relevant...
        (outcome,) = m.h.decisions.outcomes(relevance.id)
        assert (outcome.truth, outcome.source) == (
            True,
            OutcomeSource.TEAM_RESULT,
        )  # ...but it found something

    async def test_active_mode_skips_clearly_irrelevant_changes_but_signs_off(self, tmp_path: Path) -> None:
        m = Merged(tmp_path, JevMode.ACTIVE, p_relevant=0.05)
        worker = FakeWorker([])  # would fail if asked
        analyzer = ProbeAnalyzer(PROBE, Samples.EXPERIMENTAL, worker, m.runner_check, m.relevance)
        history = await m.run(analyzer)
        assert worker.calls == []
        assert any(isinstance(e, SignedOff) for e in history)
        (relevance,) = [d for d in m.h.decisions.all() if d.point == "relevance:probe"]
        assert m.h.decisions.outcomes(relevance.id) == []  # skipped work has no outcome

    async def test_active_mode_works_when_unsure(self, tmp_path: Path) -> None:
        m = Merged(tmp_path, JevMode.ACTIVE, p_relevant=0.5)
        history = await m.run(m.analyzer(Steps.report({}, [])))
        (relevance,) = [d for d in m.h.decisions.all() if d.point == "relevance:probe"]
        assert m.h.decisions.outcomes(relevance.id)[0].truth is False  # worked, found nothing
        assert any(isinstance(e, SignedOff) for e in history)


class TestAnalysisToolbox:
    async def test_run_test_names_and_paths_are_checked(self, tmp_path: Path) -> None:
        m = Merged(tmp_path)
        worktree = m.h.repository.checkout_detached(m.h.repository.tip("experimental"), "probe")
        toolbox = AnalysisToolbox(worktree, "diff", m.runner_check)
        assert toolbox.run_test("../evil.py", "x").startswith("error")
        assert toolbox.read_file("../../etc/passwd").startswith("error")
        assert toolbox.run_test("test_ok.py", PASSING).startswith("PASSED")
        assert "_analysis" not in toolbox.list_files()
        assert not toolbox.failed("test_ok.py")

    async def test_a_test_that_cannot_run_is_not_evidence(self, tmp_path: Path) -> None:
        m = Merged(tmp_path)
        worktree = m.h.repository.checkout_detached(m.h.repository.tip("experimental"), "probe")
        pytest_like = CheckConfig(
            command=(sys.executable, "-c", "import sys; sys.exit(2)"), failure_exit_codes=(1,)
        )
        toolbox = AnalysisToolbox(worktree, "diff", CommandCheck("tests", pytest_like, LocalSandbox()))
        assert toolbox.run_test("test_broken.py", "import nothing_here\n").startswith("ERROR")
        assert not toolbox.failed("test_broken.py")


class ProbeReviewer(ReviewingAnalyzer):
    INSTRUCTIONS: ClassVar[str] = "Review the text."
    KIND: ClassVar[FindingKind] = FindingKind.INCONSISTENT


def quoted(quote: str, file: str = "greeter.py") -> QuotedProblem:
    return QuotedProblem(title="Tone", detail="Too abrupt.", severity="major", file=file, quote=quote)


class TestReviewingAnalyzer:
    async def test_keeps_only_problems_quoting_text_that_is_really_there(self, tmp_path: Path) -> None:
        m = Merged(tmp_path)

        def review(toolbox: Toolbox | None) -> ReviewReport:
            assert isinstance(toolbox, ReadingToolbox)
            assert "farewell" in toolbox.read_file("greeter.py")
            return ReviewReport(
                summary="reviewed",
                problems=(
                    quoted('return  f"Goodbye, {name}!"'),  # really there (whitespace runs don't matter)
                    quoted("this sentence is not in the file at all"),  # invented
                    quoted("Goodbye"),  # too short to count as evidence
                    quoted('return f"Goodbye, {name}!"', file="missing.md"),  # wrong file
                ),
            )

        reviewer = ProbeReviewer(PROBE, Samples.EXPERIMENTAL, FakeWorker([review]))
        history = await m.run(reviewer)
        (added,) = [e for e in history if isinstance(e, FindingAdded)]
        assert added.finding.kind is FindingKind.INCONSISTENT
        assert 'In greeter.py: "return  f"Goodbye, {name}!""' in added.finding.detail
        assert any(isinstance(e, SignedOff) for e in history)

    def test_reading_tools_stay_inside_the_project(self, tmp_path: Path) -> None:
        m = Merged(tmp_path)
        worktree = m.h.repository.checkout_detached(m.h.repository.tip("experimental"), "read")
        toolbox = ReadingToolbox(worktree, "diff")
        assert toolbox.read_file("../outside").startswith("error")
        assert "greeter.py" in toolbox.list_files()
        assert [t.__name__ for t in toolbox.tools()] == ["list_files", "read_file", "show_change"]

    def test_reading_tools_do_not_follow_links_out_of_the_project(self, tmp_path: Path) -> None:
        m = Merged(tmp_path)
        secret = tmp_path / "secret.txt"
        secret.write_text("secret")
        worktree = m.h.repository.checkout_detached(m.h.repository.tip("experimental"), "read")
        (worktree.path / "link.txt").symlink_to(secret)
        assert ReadingToolbox(worktree, "diff").read_file("link.txt").startswith("error")
        runner = CommandCheck("tests", CheckConfig(command=("true",)), LocalSandbox())
        assert AnalysisToolbox(worktree, "diff", runner).read_file("link.txt").startswith("error")
