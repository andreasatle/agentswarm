"""The jq project: a third real problem, a language rather than a format (ROADMAP.md, stage 3).

From jq's own tests (MIT) this writes a seed project with an empty interpreter and one test file per group
under tests/acceptance/, where no producer can change them, and holds back every fifth case of each group as a
set no agent ever sees. The cases are jq's main test file (grouped by its section headers), the manual's
examples (grouped by manual section) and the base64 tests. Left out: onig.test (Oniguruma's regex edge cases)
and optional.test (dates and platform-specific behaviour). The agents get the manual without its examples,
since those are tests, some of them held out.

The interpreter's interface: `Program(source)` compiles a program and raises `JqError` if jq would reject it;
`.run(text)` takes one input as JSON text, as jq reads it (with jq's extensions, such as `nan`, and numbers
kept as written), yields the outputs as jq would print them, and raises `JqError` for an error nothing
catches. As in jq's own test runner, a test reads as many outputs as it expects, compares them as JSON values
(numbers by value, objects regardless of key order), and then wants no more: an error is allowed there. Left
out as well: programs that load modules (they need jq's module files and search path) and those that read the
environment. The stub accepts every program and outputs nothing, so it starts near 0%.
"""

import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import ClassVar

import yaml
from pydantic import BaseModel, ConfigDict, ValidationInfo, field_validator

from agentswarm.evaluation.seed import SeedGroup, SeedImport


class JqCase(BaseModel):
    """One test: a program with an input and its outputs, or a program jq must reject (`outputs` None)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str  # where it comes from, no spaces: "jq.test:123" or "manual:object-identifier-index-foo:1"
    program: str
    given: str = "null"  # the input, as JSON text
    outputs: tuple[str, ...] | None  # JSON texts; None: the program must not compile

    @property
    def rejected(self) -> bool:
        return self.outputs is None


class JqTestFile:
    """jq's test format: a program, its input and its outputs, one per line, a blank line between tests.

    `%%FAIL` (or `%%FAIL IGNORE MSG`) before a program means it must not compile; the error message that
    follows is jq's own wording and isn't checked. Lines starting with # are comments; a `#` line, a title,
    and a `#` line again open a section.
    """

    FAIL = "%%FAIL"

    def __init__(self, path: Path) -> None:
        self._path = path

    def sections(self) -> list[tuple[str, list[JqCase]]]:
        lines = self._path.read_text().split("\n")
        found: list[tuple[str, list[JqCase]]] = [("start", [])]
        n = 0
        while n < len(lines):
            line = lines[n]
            title = self.title(lines, n)
            if title is not None:
                found.append((title, []))
                n += 2
                continue
            if not line.strip() or line.startswith("#"):
                n += 1
                continue
            block: list[str] = []
            start = n + 1
            while n < len(lines) and lines[n].strip():
                if not lines[n].startswith("#"):  # a comment inside a test, e.g. the error it ends with
                    block.append(lines[n])
                n += 1
            found[-1][1].append(self.case(block, f"{self._path.name}:{start}"))
        return [(title, cases) for title, cases in found if cases]

    @staticmethod
    def title(lines: Sequence[str], n: int) -> str | None:
        """The section title opened at line n: a `#` line, comment lines, and a `#` line again, not inside a
        longer comment."""
        if n > 0 and lines[n - 1].startswith("#"):
            return None
        if lines[n] != "#" or n + 1 >= len(lines) or not lines[n + 1].startswith("# "):
            return None
        end = n + 1
        while end < len(lines) and lines[end].startswith("# "):
            end += 1
        return lines[n + 1][2:] if end < len(lines) and lines[end] == "#" else None

    def case(self, block: Sequence[str], name: str) -> JqCase:
        if block[0].startswith(self.FAIL):
            return JqCase(name=name, program=block[1], outputs=None)
        return JqCase(name=name, program=block[0], given=block[1], outputs=tuple(block[2:]))


class ManualExample(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    program: str
    input: str
    output: tuple[str, ...]


class ManualEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    title: str
    body: str = ""
    examples: tuple[ManualExample, ...] = ()


class ManualSection(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    title: str
    body: str = ""
    entries: tuple[ManualEntry, ...] = ()

    @field_validator("body", "entries", mode="before")
    @classmethod
    def absent(cls, value: object, info: ValidationInfo) -> object:
        """The manual writes an empty part as null."""
        return ("" if info.field_name == "body" else ()) if value is None else value


class ManualDoc(BaseModel):
    """manual.yml, as far as it's used here (the man page's own parts are ignored)."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    headline: str
    body: str
    sections: tuple[ManualSection, ...]


class JqManual:
    """The manual (docs/content/manual/manual.yml): its examples as cases, and its text without them."""

    def __init__(self, path: Path) -> None:
        self._doc = ManualDoc.model_validate(yaml.safe_load(path.read_text()))

    def sections(self) -> list[tuple[str, list[list[JqCase]]]]:
        """Per section, the cases of each entry that has examples."""
        found: list[tuple[str, list[list[JqCase]]]] = []
        for section in self._doc.sections:
            entries = [
                [
                    JqCase(
                        name=f"manual:{self.slug(entry.title)}:{k}",
                        program=example.program,
                        given=example.input,
                        outputs=tuple(example.output),
                    )
                    for k, example in enumerate(entry.examples, 1)
                ]
                for entry in section.entries
                if entry.examples
            ]
            if entries:
                found.append((section.title, entries))
        return found

    @staticmethod
    def slug(title: str) -> str:
        """A title fit for a test's name: pytest's report ends a name at its first space."""
        return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")

    def text(self) -> str:
        """The manual as Markdown, every example left out."""
        parts = [f"# {self._doc.headline}", self._doc.body]
        for section in self._doc.sections:
            parts += [f"## {section.title}", section.body]
            for entry in section.entries:
                parts += [f"### {entry.title}", entry.body]
        return "\n\n".join(p.strip() for p in parts if p.strip()) + "\n"


class JqImport(SeedImport[JqCase]):
    PACKAGE = "jqpy"
    PREFIX: ClassVar[str] = "test_jq"
    TESTS: ClassVar[tuple[str, ...]] = ("tests/jq.test",)
    EXTRA: ClassVar[tuple[str, ...]] = ("tests/base64.test",)  # one group each, after the manual
    MANUAL: ClassVar[str] = "docs/content/manual/manual.yml"
    DOC: ClassVar[str] = "docs/jq-manual.md"
    # Programs that need more than the language: jq's module files and search path, or the environment.
    LEFT_OUT: ClassVar[re.Pattern[str]] = re.compile(r"\b(import|include|modulemeta)\b|\$ENV\b|\benv\b")
    MAX_CASES: ClassVar[int] = 40  # larger sections are cut into several test files, a work item each

    STUB = '''"""A jq interpreter in pure Python."""

from collections.abc import Iterator
from typing import Any


class JqError(Exception):
    """The program isn't valid jq, or it raised an error nothing caught."""


class Program:
    """A compiled jq program."""

    def __init__(self, source: str) -> None:
        """Compile a jq program; raise JqError where jq would reject it."""
        self.source = source

    def run(self, text: str) -> Iterator[Any]:
        """The outputs for one input given as JSON text, as jq reads it, in order and as jq would print them
        (JSON values: NaN as None, infinities as the largest finite floats); JqError for an error nothing
        catches."""
        return iter(())
'''
    # How a test compares outputs: JSON values, as jq's own test runner does.
    JSON = '''"""JSON values as jq's test runner compares them (MIT).

Part of the acceptance tests: never changed.
"""

import json
from typing import Any


def value(text: str) -> Any:
    """An expected output, as JSON text, as a Python value."""
    return json.loads(text)


def same(got: Any, want: Any) -> bool:
    """Equal JSON values: numbers by value, objects regardless of key order, true never equal to 1."""
    if isinstance(want, bool) or isinstance(got, bool) or want is None or got is None:
        return type(got) is type(want) and got == want
    if isinstance(want, int | float):
        return isinstance(got, int | float) and number(got) == number(want)
    if isinstance(want, str):
        return isinstance(got, str) and got == want
    if isinstance(want, list):
        return isinstance(got, list) and len(got) == len(want) and all(same(g, w) for g, w in zip(got, want))
    return (
        isinstance(got, dict) and got.keys() == want.keys() and all(same(got[k], want[k]) for k in want)
    )


def number(n: int | float) -> float:
    """As a double, the way jq compares numbers (integers too large for one become infinite)."""
    try:
        return float(n)
    except OverflowError:
        return float("inf") if n > 0 else float("-inf")
'''
    # One program running away (an endless generator) must fail its own test, not the whole suite.
    CONFTEST = '''"""Each acceptance test gets 10 seconds. Never changed."""

import signal
from collections.abc import Iterator
from types import FrameType

import pytest


def _expired(signum: int, frame: FrameType | None) -> None:
    raise TimeoutError("the test ran longer than 10 seconds")


@pytest.fixture(autouse=True)
def _time_limit() -> Iterator[None]:
    previous = signal.signal(signal.SIGALRM, _expired)
    signal.alarm(10)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)
'''
    HEAD = '''"""jq's own tests, "{title}" (MIT). Never changed."""

from itertools import islice

import pytest

from {package} import JqError, Program
from tests.acceptance._json import same, value
'''
    RUNS = """
RUNS = {cases}


@pytest.mark.parametrize(("name", "program", "given", "outputs"), RUNS, ids=[r[0] for r in RUNS])
def test_outputs(name: str, program: str, given: str, outputs: list[str]) -> None:
    results = iter(Program(program).run(given))
    assert same(list(islice(results, len(outputs))), [value(o) for o in outputs])
    try:
        extra = next(results)
    except (StopIteration, JqError):  # no more outputs; an error after the expected ones is allowed
        return
    pytest.fail(f"an output more than expected: {{extra!r}}")
"""
    REJECTS = """
REJECTS = {cases}


@pytest.mark.parametrize(("name", "program"), REJECTS, ids=[r[0] for r in REJECTS])
def test_rejected(name: str, program: str) -> None:
    with pytest.raises(JqError):
        Program(program)
"""

    def __init__(self, source: Path, out: Path) -> None:
        super().__init__(out)
        self._source = source  # a checkout of github.com/jqlang/jq

    def groups(self) -> list[SeedGroup[JqCase]]:
        return [g for g in (self.kept(g) for g in self.all_groups()) if g.cases]

    @classmethod
    def kept(cls, group: SeedGroup[JqCase]) -> SeedGroup[JqCase]:
        return group.model_copy(
            update={"cases": tuple(c for c in group.cases if not cls.LEFT_OUT.search(c.program))}
        )

    def all_groups(self) -> list[SeedGroup[JqCase]]:
        found: list[SeedGroup[JqCase]] = []
        for path in self.TESTS:
            for title, cases in JqTestFile(self._source / path).sections():
                found += self.cut(self.short(title), [[c] for c in cases], self.MAX_CASES)
        for title, entries in JqManual(self._source / self.MANUAL).sections():
            found += self.cut(self.short(f"manual {title}"), entries, self.MAX_CASES)
        for path in self.EXTRA:
            for _, cases in JqTestFile(self._source / path).sections():
                found += self.cut(Path(path).stem, [[c] for c in cases], self.MAX_CASES)
        return found

    @classmethod
    def short(cls, title: str) -> str:
        """jq.test's section titles are sentences: the first 40 characters name a file."""
        return cls.slug(title)[:40].strip("_")

    def files(self) -> Mapping[str, str]:
        return {
            f"{self.PACKAGE}/__init__.py": self.STUB,
            "tests/__init__.py": "",
            "tests/acceptance/__init__.py": "",
            "tests/acceptance/_json.py": self.JSON,
            "tests/acceptance/conftest.py": self.CONFTEST,
            self.DOC: JqManual(self._source / self.MANUAL).text(),
        }

    def extra(self) -> Mapping[str, str]:
        return {"conftest.py": self.CONFTEST}

    def test(self, title: str, cases: Sequence[JqCase]) -> str:
        text = self.HEAD.format(title=title, package=self.PACKAGE)
        runs = [c for c in cases if not c.rejected]
        rejects = [c for c in cases if c.rejected]
        if runs:
            rows = "".join(
                f"\n    ({c.name!r}, {c.program!r}, {c.given!r}, {list(c.outputs or ())!r})," for c in runs
            )
            text += self.RUNS.format(cases=f"[{rows}\n]")
        if rejects:
            rows = "".join(f"\n    ({c.name!r}, {c.program!r})," for c in rejects)
            text += self.REJECTS.format(cases=f"[{rows}\n]")
        return text
