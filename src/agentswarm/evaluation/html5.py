"""The HTML5 project: an HTML parser that builds the tree the HTML standard prescribes (ROADMAP.md, stage 3).

From the html5lib tree-construction tests, now kept in web-platform-tests
(html/syntax/parsing/resources/*.dat, BSD-3-Clause), this writes a seed project with an empty parser and one
test file per group under tests/acceptance/, where no producer can change them, and holds back every fifth
test of each group as a set no agent ever sees. A test gives an input (a document, or a fragment with its
context element), the scripting flag where it matters, and the tree in the suite's dump format; the parse
errors it lists aren't checked. Left out: tests that need a script engine (scripted_*.dat) and changes not
yet in the standard (pending-spec-changes*.dat). Files are read as bytes, since some inputs hold carriage
returns.

The parser's interface: `parse(html, scripting=False)` and `parse_fragment(html, context, scripting=False)`
return the tree in the dump format described in docs/tree-dump.md. The stub returns an empty tree, so it
starts near 0%.
"""

import re
from collections.abc import Mapping, Sequence
from html.parser import HTMLParser
from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel, ConfigDict

from agentswarm.evaluation.seed import SeedGroup, SeedImport


class Html5Case(BaseModel):
    """One tree-construction test."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str  # file and number, no spaces: "tests1:12"
    html: str
    context: str | None  # the fragment's context element ("td", "svg path"); None for a whole document
    scripting: bool  # tests that don't say are run with scripting off: their tree is the same in both modes
    document: str  # the expected dump, its lines joined by newlines


class Html5TestFile:
    """The suite's .dat format: tests separated by a blank line, each a series of #sections.

    A section runs from its #name line to the next one; #data's last newline isn't part of the input; the
    #document dump runs to the end of the test, and a text node in it may span lines, blank ones included,
    so a test ends only where a blank line is followed by the next test's #data or #description.
    """

    BOUNDARY = re.compile(r"\n\n(?=#data\n|#description\n)")
    SECTION = re.compile(
        r"^#(data|errors|new-errors|document-fragment|script-off|script-on|document|description)$"
    )

    def __init__(self, path: Path) -> None:
        self._path = path

    def cases(self) -> list[Html5Case]:
        text = self._path.read_bytes().decode("utf-8").removesuffix("\n")
        stem = self._path.stem
        return [self.case(block, f"{stem}:{n}") for n, block in enumerate(self.BOUNDARY.split(text), 1)]

    def case(self, block: str, name: str) -> Html5Case:
        sections: dict[str, list[str]] = {}
        current = ""
        for line in block.split("\n"):
            found = self.SECTION.match(line)
            if found and (current != "document" or line == "#document"):
                current = found.group(1)
                sections[current] = []
            else:
                sections[current].append(line)
        context = sections.get("document-fragment")
        return Html5Case(
            name=name,
            html="\n".join(sections["data"]),
            context=context[0] if context else None,
            scripting="script-on" in sections,
            document="\n".join(sections["document"]),
        )


class SpecText(HTMLParser):
    """The standard's parsing chapter (html.spec.whatwg.org/multipage/parsing.html) as Markdown-like text.

    Headings become #-headings, blocks become paragraphs, <pre> keeps its lines. Left out: the examples (some
    are tests' inputs), DOM-tree pictures, the non-normative section of worked strange cases (SKIP_SECTIONS),
    browser-support boxes, scripts, styles and navigation. An end tag closes everything
    opened inside its element, since the spec leaves out optional end tags.
    """

    SKIP_CLASSES: ClassVar[frozenset[str]] = frozenset({"example", "mdn-anno", "status", "domTree"})
    # Non-normative sections whose worked cases are tests' inputs with their trees: left out, heading to the
    # next heading of the same or a higher level. The normative algorithms elsewhere say all they show.
    SKIP_SECTIONS: ClassVar[frozenset[str]] = frozenset(
        {"an-introduction-to-error-handling-and-strange-cases-in-the-parser"}
    )
    SKIP_TAGS: ClassVar[frozenset[str]] = frozenset({"script", "style", "nav", "head"})
    BLOCKS: ClassVar[frozenset[str]] = frozenset(
        {"p", "li", "dt", "dd", "tr", "div", "section", "table", "ol", "ul", "dl", "blockquote", "figcaption"}
    )
    VOID: ClassVar[frozenset[str]] = frozenset({"br", "img", "hr", "meta", "link", "input", "wbr", "source"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._out: list[str] = []
        self._open: list[tuple[str, bool]] = []  # open elements: tag, and whether it is skipped
        self._pre = 0
        self._section: int | None = None  # the heading level of a section being left out

    @classmethod
    def convert(cls, html: str) -> str:
        parser = cls()
        parser.feed(html)
        parser.close()
        text = re.sub(r"[ \t]+\n", "\n", "".join(parser._out))
        return re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"

    def skipping(self) -> bool:
        return self._section is not None or any(skip for _, skip in self._open)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        heading = re.fullmatch(r"h([1-6])", tag)
        if heading:
            level = int(heading.group(1))
            if self._section is not None and level <= self._section:
                self._section = None
            if dict(attrs).get("id") in self.SKIP_SECTIONS:
                self._section = level
        classes = set((dict(attrs).get("class") or "").split())
        skip = tag in self.SKIP_TAGS or bool(classes & self.SKIP_CLASSES)
        if tag not in self.VOID:
            self._open.append((tag, skip))
        if self.skipping():
            return
        if heading:
            self._out.append("\n\n" + "#" * int(tag[1]) + " ")
        elif tag == "pre":
            self._pre += 1
            self._out.append("\n\n```\n")
        elif tag in self.BLOCKS or tag == "br":
            self._out.append("\n\n" if tag != "br" else "\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.VOID:
            return
        was_skipping = self.skipping()
        if any(t == tag for t, _ in self._open):  # end tags the spec omits (</p>, </li>) close here too
            while self._open.pop()[0] != tag:
                pass
        if was_skipping:
            return
        if tag == "pre":
            self._pre -= 1
            self._out.append("\n```\n\n")
        elif re.fullmatch(r"h[1-6]", tag) or tag in self.BLOCKS:
            self._out.append("\n\n")

    def handle_data(self, data: str) -> None:
        if not self.skipping():
            self._out.append(data if self._pre else re.sub(r"\s+", " ", data))


class Html5Import(SeedImport[Html5Case]):
    PACKAGE = "htmlparse"
    PREFIX: ClassVar[str] = "test_html5"
    MAX_CASES: ClassVar[int] = 40  # a larger file is cut into several test files, a work item each
    LEFT_OUT: ClassVar[tuple[str, ...]] = ("scripted_", "pending-spec-changes")  # file name prefixes

    STUB = '''"""An HTML parser in pure Python that builds the tree the HTML standard prescribes."""


def parse(html: str, scripting: bool = False) -> str:
    """Parse a whole document; return its tree in the dump format of docs/tree-dump.md."""
    return ""


def parse_fragment(html: str, context: str, scripting: bool = False) -> str:
    """Parse a fragment as the HTML fragment parsing algorithm does, with this context element ("td", or
    "svg path" and "math mi" for SVG and MathML ones); return its tree in the dump format."""
    return ""
'''
    HEAD = '''"""html5lib tree-construction tests, "{title}" (BSD-3-Clause). Never changed."""

import pytest

from {package} import parse, parse_fragment
'''
    DOCUMENTS = """
DOCUMENTS = {cases}


@pytest.mark.parametrize(("name", "html", "scripting", "tree"), DOCUMENTS, ids=[c[0] for c in DOCUMENTS])
def test_document(name: str, html: str, scripting: bool, tree: str) -> None:
    assert parse(html, scripting=scripting) == tree
"""
    FRAGMENTS = """
FRAGMENTS = {cases}


@pytest.mark.parametrize(
    ("name", "html", "context", "scripting", "tree"), FRAGMENTS, ids=[c[0] for c in FRAGMENTS]
)
def test_fragment(name: str, html: str, context: str, scripting: bool, tree: str) -> None:
    assert parse_fragment(html, context, scripting=scripting) == tree
"""

    def __init__(self, tests: Path, out: Path, spec: Path | None = None) -> None:
        super().__init__(out)
        self._tests = tests  # the folder of .dat files
        self._spec = (
            spec  # the standard's parsing chapter (HTML), given to the agents as text without examples
        )

    def groups(self) -> list[SeedGroup[Html5Case]]:
        """One per file (numbered ones in number order, then the rest by name), cut into parts of 40."""
        files = [p for p in self._tests.glob("*.dat") if not p.name.startswith(self.LEFT_OUT)]
        found: list[SeedGroup[Html5Case]] = []
        for path in sorted(files, key=self.order):
            found += self.cut(path.stem, [[c] for c in Html5TestFile(path).cases()], self.MAX_CASES)
        return found

    @staticmethod
    def order(path: Path) -> tuple[int, int, str]:
        numbered = re.fullmatch(r"tests(\d+)", path.stem)
        return (0, int(numbered.group(1)), "") if numbered else (1, 0, path.stem)

    def files(self) -> Mapping[str, str]:
        files = {
            f"{self.PACKAGE}/__init__.py": self.STUB,
            "tests/__init__.py": "",
            "tests/acceptance/__init__.py": "",
            "docs/tree-dump.md": self.dump_format(),
        }
        if self._spec is not None:
            files["docs/html-parsing.md"] = SpecText.convert(self._spec.read_text())
        return files

    EXAMPLE: ClassVar[str] = """For example, `parse("<title>Notes</title><p class=x>Hi")` gives

```
| <html>
|   <head>
|     <title>
|       "Notes"
|   <body>
|     <p>
|       class="x"
|       "Hi"
```
"""

    def dump_format(self) -> str:
        """The suite's description of the dump, from its README; the example is ours, not one of the tests."""
        readme = (self._tests / "README.md").read_text()
        start = readme.index('Then there must be a line that says "\\#document"')
        end = readme.index("For example:")
        return "# The tree dump\n\n" + readme[start:end] + self.EXAMPLE

    def test(self, title: str, cases: Sequence[Html5Case]) -> str:
        text = self.HEAD.format(title=title, package=self.PACKAGE)
        documents = [c for c in cases if c.context is None]
        fragments = [c for c in cases if c.context is not None]
        if documents:
            rows = "".join(
                f"\n    ({c.name!r}, {c.html!r}, {c.scripting!r}, {c.document!r})," for c in documents
            )
            text += self.DOCUMENTS.format(cases=f"[{rows}\n]")
        if fragments:
            rows = "".join(
                f"\n    ({c.name!r}, {c.html!r}, {c.context!r}, {c.scripting!r}, {c.document!r}),"
                for c in fragments
            )
            text += self.FRAGMENTS.format(cases=f"[{rows}\n]")
        return text
