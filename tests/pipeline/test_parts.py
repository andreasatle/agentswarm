import json
import sqlite3
from pathlib import Path

from agentswarm.cli import Cli
from agentswarm.control.limits import SharedSpend
from agentswarm.core.clock import SystemClock
from agentswarm.core.config import SpendLimits
from agentswarm.pipeline.graph import DocumentPipeline
from agentswarm.pipeline.page import PageRenderer
from agentswarm.pipeline.policy import Policy
from agentswarm.pipeline.run import AuditResult, DocumentRun, RunRequest
from agentswarm.pipeline.stages import PageStage, ProjectStage, Stages
from tests.pipeline.test_graph import FakePage, FakeProject, FakeSources, Ledger


def run(tmp_path: Path) -> DocumentRun:
    request = RunRequest(name="sleep", topic="sleep and memory", reader="a curious adult", budget=2.0)
    document = tmp_path / "document.md"
    document.write_text("# Sleep and Memory\n\n## Replay\n\nThe loss is $L_2$ and $$\\sum_i x_i$$ holds.\n")
    return DocumentRun(
        request=request,
        folder=tmp_path,
        document=document,
        reviews=1,
        audits=(AuditResult(supported=16, partly=4, unsupported=0, unruled=0),),
    )


class TestPageRenderer:
    def test_math_survives_markdown_and_the_note_heads_the_page(self) -> None:
        page = PageRenderer().render("# T\n\n_a_ $x_1 * y_2$", "a note")
        assert "<h1>T</h1>" in page and '<p class="note">a note</p>' in page
        assert "$x_1 * y_2$" in page and "<em>a</em>" in page  # emphasis outside math only
        assert "katex" in page


class TestPolicy:
    def test_the_default_policy_loads_and_plans_a_run(self) -> None:
        policy = Policy.load()
        assert policy.review_threshold == 0.75 and "{topic}" in policy.goal
        assert "sleep" in policy.brief("sleep", "an adult") and 0.3 < policy.expected(5000) < 3.0


class TestPageStage:
    def test_a_page_goes_beside_the_run_and_onto_the_homepage_only_when_asked(self, tmp_path: Path) -> None:
        homepage = tmp_path / "site"
        (homepage / "src/web").mkdir(parents=True)
        (homepage / "src/web/tutorials.json").write_text(json.dumps([{"href": "/old", "title": "Old"}]))
        policy = Policy.load().model_copy(update={"homepage": homepage})
        assert PageStage().publish(run(tmp_path), policy) == tmp_path / "page.html"
        assert (homepage / "src/web/tutorials.json").read_text().count("href") == 1  # untouched
        page = PageStage().homepage(run(tmp_path), policy)
        assert page == homepage / "src/web/static/agentswarm/sleep.html"
        assert "80% of cited statements fully supported" in page.read_text()
        listing = json.loads((homepage / "src/web/tutorials.json").read_text())
        assert [e["href"] for e in listing] == ["/static/agentswarm/sleep.html", "/old"]
        assert listing[0]["title"] == "Sleep and Memory"


class TestProjectStage:
    def test_the_writing_and_the_review_name_only_teams_the_stage_can_build(self, tmp_path: Path) -> None:
        # the first real run stopped here: the stage was given no teams ("unknown teams ['writer']")
        folder = run(tmp_path)
        folder.sources.mkdir(parents=True)
        (folder.sources / "paper.txt").write_text("Sleep and memory\nA. Author, the text.")
        stage = ProjectStage(Ledger())
        stage.validate(stage.config(folder, Policy.load(), 1.0))
        stage.validate(stage.reviewing(folder, Policy.load(), 1.0))


class TestSpentBy:
    def test_a_runs_spend_is_summed_across_its_projects_and_days(self, tmp_path: Path) -> None:
        shared = SharedSpend(SpendLimits(ledger=tmp_path / "spend.sqlite"), SystemClock())
        shared.record("run-sleep", "openrouter.ai", 0.05)
        shared.record("doc-sleep", "openrouter.ai", 0.25)
        shared.record("doc-other", "openrouter.ai", 9.0)
        assert round(shared.spent_by(["run-sleep", "doc-sleep"]), 2) == 0.30
        assert round(shared.spent_today_by("doc-sleep"), 2) == 0.25


class TestDocumentCommands:
    def test_write_runs_and_approve_from_the_command_line(self, tmp_path: Path) -> None:
        ledger = Ledger()
        project = FakeProject(ledger, shares=[16])
        runs = DocumentPipeline(
            Stages(FakeSources(ledger), project, project, FakePage(), ledger),
            Policy.load(),
            sqlite3.connect(":memory:", check_same_thread=False),
        )
        runs.close = lambda: None  # one pipeline across the commands of this test
        shown: list[str] = []
        cli = Cli(show=shown.append, documents=runs)
        cli.RUNS = tmp_path
        assert (
            cli.dispatch(["write", "sleep and memory", "--reader", "a curious adult", "--budget", "2"]) == 0
        )
        assert (
            "WAITING: Approve the sources" in shown[-1]
            and "uv run agentswarm approve sleep-and-memory" in shown[-1]
        )
        assert (
            cli.dispatch(["runs"]) == 0
            and "sleep-and-memory" in shown[-1]
            and "approve the sources" in shown[-1]
        )
        assert (
            cli.dispatch(["approve", "sleep-and-memory"]) == 0 and "WAITING: Approve publishing" in shown[-1]
        )
        assert cli.dispatch(["approve", "sleep-and-memory", "--choice", "budget"]) == 2  # needs --budget
        assert cli.dispatch(["publish", "sleep-and-memory"]) == 2 and "isn't approved" in shown[-1]
        assert cli.dispatch(["approve", "sleep-and-memory"]) == 0
        assert cli.dispatch(["publish", "sleep-and-memory"]) == 0 and "on the homepage" in shown[-1]
