import asyncio
import json
import sys
from pathlib import Path

from agentswarm.core.config import ProjectConfig
from agentswarm.core.domain import Level, Scored, Tally
from agentswarm.evaluation.commonmark import CommonMarkImport
from agentswarm.evaluation.score import Progress, Scoreboard
from agentswarm.workspace.git import Git, Repository
from agentswarm.workspace.sandbox import LocalSandbox
from tests.support.harness import Harness
from tests.support.samples import Samples

SPEC = [
    {"markdown": "\tx\n", "html": "<pre><code>x\n</code></pre>\n", "example": 4, "section": "Tabs"},
    {"markdown": "", "html": "", "example": 1, "section": "Blank lines"},
    {
        "markdown": "*a*\n",
        "html": "<p><em>a</em></p>\n",
        "example": 2,
        "section": "Emphasis and strong emphasis",
    },
    {
        "markdown": "**a**\n",
        "html": "<p><strong>a</strong></p>\n",
        "example": 3,
        "section": "Emphasis and strong emphasis",
    },
    {
        "markdown": "_a_\n",
        "html": "<p><em>a</em></p>\n",
        "example": 5,
        "section": "Emphasis and strong emphasis",
    },
]
PYTEST = (sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider")


class TestCommonMarkImport:
    def test_one_file_per_section_in_build_order_with_every_fifth_example_held_out(
        self, tmp_path: Path
    ) -> None:
        spec = tmp_path / "spec.json"
        spec.write_text(json.dumps(SPEC))
        summary = CommonMarkImport(spec, tmp_path / "out").write()
        assert (summary.groups, summary.visible, summary.held_out) == (3, 4, 1)
        tests = sorted(p.name for p in (summary.seed / "tests" / "acceptance").iterdir())
        assert tests == [  # blocks before inlines, tabs last, whatever the spec's order
            "test_spec_01_blank_lines.py",
            "test_spec_02_emphasis_and_strong_emphasis.py",
            "test_spec_03_tabs.py",
        ]
        assert [p.name for p in summary.held_out_tests.iterdir()] == [
            "test_held_out_02_emphasis_and_strong_emphasis.py"
        ]
        assert (summary.seed / "mdconv" / "__init__.py").read_text() == CommonMarkImport.STUB


class TestScoreboard:
    def test_scores_the_level_with_held_out_tests_copied_in(self, tmp_path: Path) -> None:
        spec = tmp_path / "spec.json"
        spec.write_text(json.dumps(SPEC))
        summary = CommonMarkImport(spec, tmp_path / "out").write()
        repository = Repository.create(
            tmp_path / "repo", tmp_path / "wt", Harness.LADDER, Git(), summary.seed
        )
        score = Scoreboard(repository.path, LocalSandbox(), PYTEST).score(
            Samples.EXPERIMENTAL, [summary.held_out_tests]
        )
        assert score.total("tests/acceptance/") == (1, 4)  # the stub passes the blank-line example only
        assert score.total(f"{Scoreboard.EXTRA}/") == (0, 1)


class TestProgress:
    def test_a_run_is_scored_seen_and_held_out_with_the_spend_so_far(self, tmp_path: Path) -> None:
        spec = tmp_path / "spec.json"
        spec.write_text(json.dumps(SPEC))
        summary = CommonMarkImport(spec, tmp_path / "out").write()
        repository = Repository.create(
            tmp_path / "repo", tmp_path / "wt", Harness.LADDER, Git(), summary.seed
        )
        config = ProjectConfig.model_validate(
            {
                "project": "demo",
                "domain": "software",
                "north_star": "convert",
                "teams": [],
                "gates": [],
                "levels": list(Harness.LADDER.levels),
                "checks": {"spec": {"command": list(PYTEST), "kind": "test_failure"}},
                "score": {"check": "spec", "held_out": [str(summary.held_out_tests)]},
            }
        )
        assert config.score is not None
        progress = Progress(config, config.score, repository.path, LocalSandbox())
        (tmp_path / "h").mkdir()
        scored = asyncio.run(progress.measure(Harness(tmp_path / "h").board.current()))
        assert (scored.seen, scored.held_out) == (Tally(passed=1, total=4), Tally(passed=0, total=1))
        assert scored.spent_usd == 0.0

    def test_the_report_says_what_a_run_bought(self) -> None:
        def at(passed: int, held: int, usd: float) -> Scored:
            return Scored(
                level=Level("experimental"),
                commit="c" * 40,
                seen=Tally(passed=passed, total=522),
                held_out=Tally(passed=held, total=130),
                spent_usd=usd,
            )

        report = Progress.render(at(227, 60, 8.0), at(237, 62, 10.5))
        assert "cost $2.50" in report
        assert "seen tests: 227/522 (43%) -> 237/522 (45%) (+10)" in report
        assert "held out:   60/130 (46%) -> 62/130 (48%) (+2)" in report
        assert "$0.25 per seen example gained" in report


class TestSpecText:
    def test_the_text_keeps_the_prose_and_drops_every_example(self) -> None:
        fence = CommonMarkImport.FENCE
        text = f"# Tabs\n\nSome prose.\n\n{fence} example\n\tfoo\n.\n<pre>foo</pre>\n{fence}\n\nMore prose.\n"
        prose = CommonMarkImport.without_examples(text)
        assert "Some prose." in prose and "More prose." in prose and "(Example 1:" in prose
        assert "foo" not in prose and fence not in prose
