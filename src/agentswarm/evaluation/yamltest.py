"""The YAML project: a YAML 1.2 parser, judged by the official yaml-test-suite (ROADMAP.md, stage 3).

From the suite's data release (MIT: each test an input, the parser's events in the suite's notation, the
values as JSON where JSON can hold them, or a mark that the input must be rejected) this writes a seed
project with an empty parser and one test file per group under tests/acceptance/, where no producer can
change them, and holds back every fifth case of each group as a set no agent ever sees. A case is grouped by
its most specific tag (anchors before mappings, say); the inputs to reject come after the others, in the
same order, cut into files of their own. The event notation is described for the agents in
docs/event-notation.md, checked against every event line of the suite.

The parser's interface: `events(text)` returns the event lines, `load_all(text)` every document as plain
values (YAML's core schema), and both raise `YAMLError` for input the suite rejects. The stub accepts
everything and finds nothing, so it starts near 0%.
"""

import re
from collections.abc import Sequence
from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel, ConfigDict


class YamlCase(BaseModel):
    """One test of the suite: a YAML input with its events and values, or one the parser must reject."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str  # the suite's id, with the case's number for a file of several: "229Q", "2G84-01"
    title: str
    text: str
    events: tuple[str, ...]  # for a rejected input, the events up to the error (not checked)
    values: str | None  # the documents as a JSON stream, where JSON can hold them
    rejected: bool
    tags: tuple[str, ...]


class YamlGroup(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str  # fits a Python module name, e.g. "anchor" or "rejected_2"
    cases: tuple[YamlCase, ...]


class YamlImportSummary(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    groups: int
    cases: int
    visible: int  # tests: a valid case has one for its events and, where JSON holds it, one for its values
    held_out: int
    seed: Path
    held_out_tests: Path


class YamlSuite:
    """A checkout of the suite's data release: a folder per test, with numbered subfolders for several."""

    SKIP: ClassVar[frozenset[str]] = frozenset({"tags", "name"})  # indexes over the tests, not tests

    def __init__(self, root: Path) -> None:
        self._root = root

    def cases(self) -> list[YamlCase]:
        tags = self.tags()
        found: list[YamlCase] = []
        for folder in sorted(p for p in self._root.iterdir() if p.is_dir() and not self.hidden(p)):
            if (folder / "in.yaml").exists():
                found.append(self.case(folder, folder.name, tags.get(folder.name, ())))
            for sub in sorted(p for p in folder.iterdir() if p.is_dir()):
                found.append(self.case(sub, f"{folder.name}-{sub.name}", tags.get(folder.name, ())))
        return found

    def hidden(self, folder: Path) -> bool:
        return folder.name in self.SKIP or folder.name.startswith(".")

    def tags(self) -> dict[str, tuple[str, ...]]:
        found: dict[str, list[str]] = {}
        for tag in sorted((self._root / "tags").iterdir()):
            for test in tag.iterdir():
                found.setdefault(test.name, []).append(tag.name)
        return {test: tuple(names) for test, names in found.items()}

    @staticmethod
    def case(folder: Path, name: str, tags: tuple[str, ...]) -> YamlCase:
        values = folder / "in.json"
        return YamlCase(
            name=name,
            title=(folder / "===").read_text().strip(),
            text=(folder / "in.yaml").read_text(),
            events=tuple((folder / "test.event").read_text().splitlines()),
            values=values.read_text() if values.exists() else None,
            rejected=(folder / "error").exists(),
            tags=tags,
        )


class YamlImport:
    PACKAGE = "yamlpy"
    HELD_OUT_EVERY: ClassVar[int] = 5  # within each group, the fifth, tenth, ... case is held out
    MAX_CASES: ClassVar[int] = 30  # a larger group is cut into several test files, a work item each
    # A case's group: its first tag in this list, most specific first; a case with none of them is "other".
    SPECIFIC: ClassVar[tuple[str, ...]] = (
        "directive", "header", "footer", "document", "local-tag", "unknown-tag", "tag", "anchor", "alias",
        "explicit-key", "complex-key", "empty-key", "duplicate-key", "literal", "folded", "double", "single",
        "flow", "comment", "indent", "whitespace", "scalar", "mapping", "sequence",
    )  # fmt: skip
    # The order the test files come in, which is the order the worklist takes them: the basics first.
    ORDER: ClassVar[tuple[str, ...]] = (
        "mapping", "sequence", "scalar", "flow", "double", "single", "literal", "folded", "comment",
        "whitespace", "indent", "document", "header", "footer", "directive", "explicit-key", "complex-key",
        "empty-key", "duplicate-key", "anchor", "alias", "tag", "local-tag", "unknown-tag", "other",
    )  # fmt: skip

    STUB = '''"""A YAML 1.2 parser in pure Python."""

from typing import Any


class YAMLError(ValueError):
    """The input isn't valid YAML 1.2."""


def events(text: str) -> list[str]:
    """The parser's events for a YAML stream, one line each, in the notation of docs/event-notation.md."""
    return []


def load_all(text: str) -> list[Any]:
    """Every document of a YAML stream as plain Python values (YAML 1.2's core schema)."""
    return []
'''
    JSON = '''"""JSON values as the suite's in.json holds them (MIT).

Part of the acceptance tests: never changed.
"""

import json
from typing import Any


def stream(text: str) -> list[Any]:
    """The JSON values one after another in a text, as in.json holds a stream's documents."""
    decoder, found, at = json.JSONDecoder(), [], 0
    while True:
        while at < len(text) and text[at].isspace():
            at += 1
        if at == len(text):
            return found
        value, at = decoder.raw_decode(text, at)
        found.append(value)


def same(got: Any, want: Any) -> bool:
    """Equal JSON values: numbers by value, objects regardless of key order, true never equal to 1."""
    if isinstance(want, bool) or isinstance(got, bool) or want is None or got is None:
        return type(got) is type(want) and got == want
    if isinstance(want, int | float):
        return isinstance(got, int | float) and got == want
    if isinstance(want, str):
        return isinstance(got, str) and got == want
    if isinstance(want, list):
        return isinstance(got, list) and len(got) == len(want) and all(same(g, w) for g, w in zip(got, want))
    return isinstance(got, dict) and got.keys() == want.keys() and all(same(got[k], want[k]) for k in want)
'''
    HEAD = '''"""yaml-test-suite, "{title}" (MIT). Never changed."""

import pytest

from {package} import YAMLError, events, load_all
from tests.acceptance._json import same, stream
'''
    EVENTS = """
EVENTS = {cases}


@pytest.mark.parametrize(("name", "document", "expected"), EVENTS, ids=[c[0] for c in EVENTS])
def test_events(name: str, document: str, expected: list[str]) -> None:
    assert events(document) == expected
"""
    VALUES = """
VALUES = {cases}


@pytest.mark.parametrize(("name", "document", "expected"), VALUES, ids=[c[0] for c in VALUES])
def test_values(name: str, document: str, expected: str) -> None:
    assert same(load_all(document), stream(expected))
"""
    REJECTED = """
REJECTED = {cases}


@pytest.mark.parametrize(("name", "document"), REJECTED, ids=[c[0] for c in REJECTED])
def test_rejected(name: str, document: str) -> None:
    with pytest.raises(YAMLError):
        events(document)
"""
    NOTATION = """# The event notation

`events(text)` returns one line per parser event, in the notation of the yaml-test-suite. A stream:

- `+STR` and `-STR`: the stream starts and ends (always the first and the last line).
- `+DOC` and `-DOC`: a document starts and ends; `+DOC ---` when the document starts with an explicit `---`,
  `-DOC ...` when it ends with an explicit `...`.
- `+MAP` and `-MAP`: a mapping starts and ends; `+MAP {}` for a flow mapping.
- `+SEQ` and `-SEQ`: a sequence starts and ends; `+SEQ []` for a flow sequence.
- `=VAL`: a scalar. Its style comes right before its content: `:` plain, `'` single-quoted, `"` double-quoted,
  `|` literal, `>` folded. The content is the scalar's value after the YAML rules are applied (line folding,
  escapes, chomping), written on one line: a backslash as `\\\\`, a line break as `\\n`, a tab as `\\t`, a
  backspace as `\\b`, a carriage return as `\\r`, every other character as itself.
- `=ALI *name`: an alias of the node anchored as `name`.

A node's anchor and tag come after the event's name and its flow marker, anchor first: `+MAP {} &a <tag>`,
`=VAL &a <tag> :text`. A tag is written in full, resolved through the document's tag handles, for example
`<tag:yaml.org,2002:str>` for `!!str` or `<!local>` for `!local`; a node without an explicit tag has none.
Mapping entries are a key's events followed by its value's.

For example, this document:

```yaml
- &a key: !!str value
- [1, "two"]
- *a
```

gives

```
+STR
+DOC
+SEQ
+MAP
=VAL &a :key
=VAL <tag:yaml.org,2002:str> :value
-MAP
+SEQ []
=VAL :1
=VAL "two
-SEQ
=ALI *a
-SEQ
-DOC
-STR
```
"""

    def __init__(self, suite: Path, out: Path) -> None:
        self._suite = suite  # the suite's data release, e.g. the data-2022-01-17 tag
        self._out = out

    def groups(self) -> list[YamlGroup]:
        cases = YamlSuite(self._suite).cases()
        found: list[YamlGroup] = []
        for kind in self.ORDER:
            found += self.cut(kind, [c for c in cases if not c.rejected and self.kind(c) == kind])
        rejected = [c for kind in self.ORDER for c in cases if c.rejected and self.kind(c) == kind]
        return found + self.cut("rejected", rejected)  # a few of each kind: one sequence, in the same order

    @classmethod
    def kind(cls, case: YamlCase) -> str:
        return next((t for t in cls.SPECIFIC if t in case.tags), "other")

    @classmethod
    def cut(cls, title: str, cases: Sequence[YamlCase]) -> list[YamlGroup]:
        slug = re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_")
        parts = [cases[n : n + cls.MAX_CASES] for n in range(0, len(cases), cls.MAX_CASES)]
        if len(parts) <= 1:
            return [YamlGroup(name=slug, cases=tuple(p)) for p in parts]
        return [YamlGroup(name=f"{slug}_{n}", cases=tuple(p)) for n, p in enumerate(parts, 1)]

    def write(self) -> YamlImportSummary:
        groups = self.groups()
        seed = self._out / "seed"
        hidden = self._out / "held_out"
        acceptance = seed / "tests" / "acceptance"
        (seed / self.PACKAGE).mkdir(parents=True, exist_ok=True)
        acceptance.mkdir(parents=True, exist_ok=True)
        (seed / "docs").mkdir(exist_ok=True)
        hidden.mkdir(parents=True, exist_ok=True)
        (seed / self.PACKAGE / "__init__.py").write_text(self.STUB)
        (seed / "tests" / "__init__.py").write_text("")
        (acceptance / "__init__.py").write_text("")
        (acceptance / "_json.py").write_text(self.JSON)
        (seed / "docs" / "event-notation.md").write_text(self.NOTATION)
        visible = held = 0
        for index, group in enumerate(groups, 1):
            shown = [c for n, c in enumerate(group.cases, 1) if n % self.HELD_OUT_EVERY]
            kept = [c for n, c in enumerate(group.cases, 1) if not n % self.HELD_OUT_EVERY]
            name = f"{index:02d}_{group.name}"
            if shown:
                (acceptance / f"test_yaml_{name}.py").write_text(self.test(group.name, shown))
            if kept:
                (hidden / f"test_held_out_{name}.py").write_text(self.test(group.name, kept))
            visible, held = visible + self.count(shown), held + self.count(kept)
        return YamlImportSummary(
            groups=len(groups),
            cases=sum(len(g.cases) for g in groups),
            visible=visible,
            held_out=held,
            seed=seed,
            held_out_tests=hidden,
        )

    @staticmethod
    def count(cases: Sequence[YamlCase]) -> int:
        return sum(1 if c.rejected else 1 + (c.values is not None) for c in cases)

    def test(self, title: str, cases: Sequence[YamlCase]) -> str:
        text = self.HEAD.format(title=title, package=self.PACKAGE)
        valid = [c for c in cases if not c.rejected]
        loaded = [c for c in valid if c.values is not None]
        rejected = [c for c in cases if c.rejected]
        if valid:
            rows = "".join(f"\n    ({c.name!r}, {c.text!r}, {list(c.events)!r})," for c in valid)
            text += self.EVENTS.format(cases=f"[{rows}\n]")
        if loaded:
            rows = "".join(f"\n    ({c.name!r}, {c.text!r}, {c.values!r})," for c in loaded)
            text += self.VALUES.format(cases=f"[{rows}\n]")
        if rejected:
            rows = "".join(f"\n    ({c.name!r}, {c.text!r})," for c in rejected)
            text += self.REJECTED.format(cases=f"[{rows}\n]")
        return text
