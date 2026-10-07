import json
import sys
from pathlib import Path

from agentswarm.evaluation.commonmark import CommonMarkImport
from agentswarm.evaluation.score import Scoreboard
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
        assert (summary.sections, summary.visible, summary.held_out) == (3, 4, 1)
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
