"""The CommonMark project: a real problem with an external definition of done (ROADMAP.md, stage 1).

From the official spec's examples (spec.json: markdown, html, example number, section) this writes a seed
project with an empty converter and one test file per spec section under tests/acceptance/, where no producer
can change them, and holds back every fifth example as a set no agent ever sees, to tell real progress
from fitting the visible examples. The examples are the spec's own (CC-BY-SA 4.0); no model writes any of it.
"""

import json
import re
from collections.abc import Sequence
from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, TypeAdapter


class SpecExample(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    markdown: str
    html: str
    example: int
    section: str


class ImportSummary(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    sections: int
    visible: int
    held_out: int
    seed: Path
    held_out_tests: Path


class CommonMarkImport:
    PACKAGE = "mdconv"
    HELD_OUT_EVERY: ClassVar[int] = 5  # example numbers divisible by this are held out
    # The order a CommonMark parser is usually built in, which the worklist follows (files are numbered by
    # it): paragraphs and the other blocks first, then inlines; the spec's "preliminaries" (tabs, and how
    # blocks and inlines take precedence) need most of the rest to work, so they come last. Sections not
    # listed go after these, in spec order.
    BUILD_ORDER: ClassVar[tuple[str, ...]] = (
        "Paragraphs",
        "Blank lines",
        "Textual content",
        "Thematic breaks",
        "ATX headings",
        "Setext headings",
        "Indented code blocks",
        "Fenced code blocks",
        "Block quotes",
        "List items",
        "Lists",
        "HTML blocks",
        "Link reference definitions",
        "Inlines",
        "Backslash escapes",
        "Entity and numeric character references",
        "Code spans",
        "Emphasis and strong emphasis",
        "Links",
        "Images",
        "Autolinks",
        "Raw HTML",
        "Hard line breaks",
        "Soft line breaks",
        "Tabs",
        "Precedence",
    )
    STUB = '''"""A CommonMark-compliant Markdown to HTML converter, in pure Python."""


def to_html(markdown: str) -> str:
    """Convert CommonMark Markdown to HTML, exactly as the CommonMark spec's examples show."""
    return ""
'''
    TEST = '''"""CommonMark spec, section "{section}": the official examples (CC-BY-SA 4.0). Never changed."""

import pytest

from mdconv import to_html

EXAMPLES = {examples}


@pytest.mark.parametrize(("number", "markdown", "html"), EXAMPLES, ids=[f"ex{{n}}" for n, _, _ in EXAMPLES])
def test_example(number: int, markdown: str, html: str) -> None:
    assert to_html(markdown) == html
'''

    FENCE: ClassVar[str] = "`" * 32  # how spec.txt delimits its examples
    DOC: ClassVar[str] = "docs/spec.md"

    def __init__(self, spec: Path, out: Path, prose: Path | None = None) -> None:
        self._spec = spec
        self._out = out
        self._prose = prose  # spec.txt: the spec's text, given to the agents without its examples

    @classmethod
    def without_examples(cls, text: str) -> str:
        """The spec's text with every example replaced by a marker, so the held-out ones stay unseen.

        The examples the agents may see are their tests already; the rest are what tells real progress from
        fitting, so no copy of any example goes into the project.
        """
        lines: list[str] = []
        number = 0
        inside = False
        for line in text.splitlines():
            if not inside and line.startswith(f"{cls.FENCE} example"):
                inside, number = True, number + 1
                lines.append(
                    f"(Example {number}: not shown here; the visible examples are tests/acceptance/)"
                )
            elif inside and line.startswith(cls.FENCE):
                inside = False
            elif not inside:
                lines.append(line)
        return "\n".join(lines) + "\n"

    def examples(self) -> list[SpecExample]:
        return TypeAdapter(list[SpecExample]).validate_python(json.loads(self._spec.read_text()))

    @staticmethod
    def slug(section: str) -> str:
        return re.sub(r"[^a-z0-9]+", "_", section.lower()).strip("_")

    def write(self) -> ImportSummary:
        examples = self.examples()
        in_spec = list(dict.fromkeys(e.section for e in examples))
        sections = [s for s in self.BUILD_ORDER if s in in_spec] + [
            s for s in in_spec if s not in self.BUILD_ORDER
        ]
        seed = self._out / "seed"
        hidden = self._out / "held_out"
        (seed / self.PACKAGE).mkdir(parents=True, exist_ok=True)
        (seed / "tests" / "acceptance").mkdir(parents=True, exist_ok=True)
        hidden.mkdir(parents=True, exist_ok=True)
        (seed / self.PACKAGE / "__init__.py").write_text(self.STUB)
        if self._prose is not None:
            (seed / "docs").mkdir(exist_ok=True)
            (seed / self.DOC).write_text(self.without_examples(self._prose.read_text()))
        visible = held = 0
        for index, section in enumerate(sections, 1):
            name = f"{index:02d}_{self.slug(section)}"
            mine = [e for e in examples if e.section == section]
            shown = [e for e in mine if e.example % self.HELD_OUT_EVERY]
            kept = [e for e in mine if not e.example % self.HELD_OUT_EVERY]
            if shown:
                (seed / "tests" / "acceptance" / f"test_spec_{name}.py").write_text(self.test(section, shown))
            if kept:
                (hidden / f"test_held_out_{name}.py").write_text(self.test(section, kept))
            visible, held = visible + len(shown), held + len(kept)
        return ImportSummary(
            sections=len(sections), visible=visible, held_out=held, seed=seed, held_out_tests=hidden
        )

    def test(self, section: str, examples: Sequence[SpecExample]) -> str:
        rows = "".join(f"\n    ({e.example}, {e.markdown!r}, {e.html!r})," for e in examples)
        return self.TEST.format(section=section, examples=f"[{rows}\n]")
