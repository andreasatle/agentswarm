"""The handbook seed's document checker: it is ground truth for document projects, so it is tested."""

import shutil
import subprocess
import sys
from pathlib import Path

from tests.support.paths import RepoPaths

SEED = RepoPaths.EXAMPLES / "handbook" / "seed"


class Handbook:
    def __init__(self, tmp_path: Path) -> None:
        self.root = tmp_path / "handbook"
        shutil.copytree(SEED, self.root)

    def section(self, name: str, text: str) -> None:
        (self.root / "sections" / name).write_text(text)

    def check(self) -> tuple[bool, str]:
        result = subprocess.run(
            [sys.executable, "tools/check_doc.py"], cwd=self.root, capture_output=True, text=True, check=False
        )
        return result.returncode == 0, result.stdout


class TestCheckDoc:
    def test_the_seed_passes(self, tmp_path: Path) -> None:
        assert Handbook(tmp_path).check() == (True, "document checks passed\n")

    def test_a_good_section_with_a_link_passes(self, tmp_path: Path) -> None:
        h = Handbook(tmp_path)
        h.section(
            "01-meetings.md",
            "## Meetings\n\nMeet once a month.\nSee the [introduction](00-introduction.md).\n",
        )
        assert h.check()[0]

    def test_each_rule_is_enforced(self, tmp_path: Path) -> None:
        h = Handbook(tmp_path)
        h.section("01-no-heading.md", "Just text.\n")
        h.section("02-empty.md", "## Empty\n")
        h.section("03-two.md", "## Two\n\nFirst sentence. Second sentence.\n")
        h.section("04-link.md", "## Link\n\nSee [missing](nowhere.md).\n")
        h.section("05-notes.txt", "not markdown\n")
        passed, output = h.check()
        assert not passed
        assert "01-no-heading.md: must start with" in output
        assert "02-empty.md: has no text" in output
        assert "03-two.md:3: one sentence per line" in output
        assert "04-link.md:3: link to missing file nowhere.md" in output
        assert "05-notes.txt: sections must be Markdown files" in output

    def test_abbreviations_and_web_links_are_fine(self, tmp_path: Path) -> None:
        h = Handbook(tmp_path)
        h.section(
            "01-ok.md",
            "## OK\n\nBring snacks, e.g. fruit or tea.\nRead more at [the web](https://example.org).\n",
        )
        assert h.check()[0]
