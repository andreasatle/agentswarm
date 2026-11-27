import sqlite3
from pathlib import Path

import pytest

from agentswarm.cli import Cli
from agentswarm.control.limits import SharedSpend
from agentswarm.core.clock import SystemClock
from agentswarm.core.config import ProjectConfig, SpendLimits
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
        assert "\\(x_1 * y_2\\)" in page and "<em>a</em>" in page  # emphasis outside math only
        assert "katex" in page

    def test_money_is_not_math(self) -> None:
        page = PageRenderer().render("# T\n\nraise $45 billion, and a $100 billion deal; costs $5 to $8", "")
        assert "raise $45 billion, and a $100 billion deal; costs $5 to $8" in page
        assert "\\(" not in page.split("</h1>")[1]  # no inline math in the body
        assert "left:'$'" not in page  # the browser never pairs single dollars

    def test_math_between_amounts_is_still_math(self) -> None:
        page = PageRenderer().render("# T\n\n$300 billion, so $C \\approx 6ND$ holds; $R = 0.16$.", "")
        assert "$300 billion" in page and "\\(C \\approx 6ND\\)" in page and "\\(R = 0.16\\)" in page

    @staticmethod
    def body(text: str) -> str:
        page = PageRenderer().render("# T\n\n" + text, "")
        return page.split('<p class="note"></p>\n')[1].split(f"<script>{PageRenderer.REFERENCES}")[0]

    def test_numbered_references_link_to_their_entries_and_back(self) -> None:
        body = self.body(
            "It grew[^1]. It slowed[^2][^1].\n\n## References\n\n[^1]: Kaplan (2020).\n[^2]: Lane.\n"
        )
        assert '<a class="footnote-ref" href="#fn:1">1</a>' in body  # shown as [1] by the page's style
        assert '<li id="fn:2">' in body and 'class="footnote-backref" href="#fnref:2"' in body

    def test_raw_html_is_shown_not_run(self) -> None:
        body = self.body("x < y, <script>alert(1)</script> and <b>b</b>")
        assert "<script>" not in body and "&lt;script&gt;" in body and "<b>" not in body

    def test_an_asterisk_inside_a_word_or_number_is_a_times_sign(self) -> None:
        body = self.body("2*3*4 and f(x)*g(x), but *it* and **bold**")
        assert (
            "2*3*4" in body
            and "f(x)*g(x)" in body
            and "<em>it</em>" in body
            and "<strong>bold</strong>" in body
        )

    def test_an_escaped_dollar_is_a_dollar_and_backslash_math_is_math(self) -> None:
        body = self.body("costs \\$5; \\(x_1\\) and \\[y^2\\]")
        assert "&#36;5" in body and "\\(x_1\\)" in body and "\\[y^2\\]" in body

    def test_a_paragraph_starting_with_a_year_is_not_a_list(self) -> None:
        body = self.body("Intro.\n\n2026. The year\n\n1. one\n2. two")
        assert "<p>2026. The year</p>" in body and "<ol>" in body and 'start="2026"' not in body

    def test_code_is_left_as_written(self) -> None:
        body = self.body("a `$x$ 2*3 <b>` span\n\n```\n$a$ <b>\n```")
        assert "<code>$x$ 2*3 &lt;b&gt;</code>" in body and "<pre><code>$a$ &lt;b&gt;" in body


class TestPolicy:
    def test_the_default_policy_loads_and_plans_a_run(self) -> None:
        policy = Policy.load()
        assert policy.review_threshold == 0.9 and "{topic}" in policy.goal
        assert "sleep" in policy.brief("sleep", "an adult") and 0.3 < policy.expected(5000) < 3.0

    def test_the_brief_carries_the_questions_the_user_approved(self) -> None:
        policy = Policy.load()
        brief = policy.brief("sleep", "an adult", None, ("Why do we dream?",))
        assert brief.startswith(policy.brief("sleep", "an adult")) and brief.endswith("- Why do we dream?")
        assert policy.brief("sleep", "an adult", "My own brief.", ()) == "My own brief."


class TestPageStage:
    def test_the_page_goes_beside_the_run(self, tmp_path: Path) -> None:
        page = PageStage().publish(run(tmp_path), Policy.load())
        assert page == tmp_path / "page.html"
        assert "80% of cited statements fully supported" in page.read_text()

    def test_the_users_title_replaces_the_writers_and_nothing_else(self, tmp_path: Path) -> None:
        titled = run(tmp_path).model_copy(update={"title": "Sleep:  a short  title"})
        assert titled.heading == "Sleep and Memory"
        page = PageStage().publish(titled, Policy.load())
        assert titled.heading == "Sleep: a short title"
        assert (tmp_path / "document.md").read_text().startswith("# Sleep: a short title\n\n## Replay\n")
        assert "<title>Sleep: a short title" in page.read_text()


class TestProjectStage:
    def test_the_writing_and_the_review_name_only_teams_the_stage_can_build(self, tmp_path: Path) -> None:
        # the first real run stopped here: the stage was given no teams ("unknown teams ['writer']")
        folder = run(tmp_path)
        folder.sources.mkdir(parents=True)
        (folder.sources / "paper.txt").write_text("Sleep and memory\nA. Author, the text.")
        stage = ProjectStage(Ledger())
        stage.validate(stage.config(folder, Policy.load(), 1.0))
        reviewing = stage.reviewing(folder, Policy.load(), 1.0)
        stage.validate(reviewing)
        review = ProjectConfig.load(reviewing).review
        assert review is not None and review.max_rounds == Policy.load().review_rounds == 2
        # findings are ruled without reasoning, cited statements with it (docs/design/self-correction.md)
        config = ProjectConfig.load(reviewing)
        assert review.finding_referee == "referee-fast" and config.models["referee-fast"].thinking is False

    def test_the_audit_is_capped_by_the_budget_left_now_not_the_writings(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        limits = tmp_path / "limits.yaml"
        limits.write_text(f"ledger: {tmp_path / 'spend.sqlite'}\n")  # an empty ledger: nothing spent today
        monkeypatch.setenv(SpendLimits.VARIABLE, str(limits))
        folder = run(tmp_path)
        folder.sources.mkdir(parents=True)
        (folder.sources / "paper.txt").write_text("Sleep and memory\nA. Author, the text.")
        ledger = Ledger()
        stage = ProjectStage(ledger)
        stage.capped(stage.config(folder, Policy.load(), 0.5), folder, 0.5)  # the writing's cap
        ledger.usd = 1.9
        raised = folder.model_copy(update={"request": folder.request.model_copy(update={"budget": 3.0})})
        assert stage.checking(raised).budgets.usd_per_day == pytest.approx(1.1)


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
        project = FakeProject(ledger, shares=[19, 19])
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
            cli.dispatch(["write", "sleep and memory", "--reader", "a curious adult", "--budget", "4"]) == 0
        )
        assert "WAITING: Approve the questions" in shown[-1] and "--drop" in shown[-1]
        assert cli.dispatch(["approve", "sleep-and-memory", "--drop", "9"]) == 2  # there are three
        assert cli.dispatch(["approve", "sleep-and-memory", "--drop", "3", "--add", "Why do we dream?"]) == 0
        assert (
            "WAITING: Approve the sources" in shown[-1]
            and "uv run agentswarm approve sleep-and-memory" in shown[-1]
            and "questions left out: 1" in shown[-1]
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
        assert cli.dispatch(["approve", "sleep-and-memory"]) == 0 and "page written" in shown[-1]
