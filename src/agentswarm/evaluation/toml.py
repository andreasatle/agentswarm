"""The TOML project: a second real problem with an external definition of done (ROADMAP.md, stage 3).

From the official test suite (toml-test, MIT: valid TOML files with their expected values as tagged JSON, and
invalid files a parser must reject) this writes a seed project with an empty parser and one test file per
group under tests/acceptance/, where no producer can change them, and holds back every fifth file of each
group as a set no agent ever sees. Only the TOML 1.0.0 files are used (the suite's own list). The tests and
the spec are the suite's own; no model writes any of it.

The parser's interface is the standard library's (`tomllib`): `loads` returns plain Python values and raises
`TOMLDecodeError`; it takes bytes as well, since some invalid files aren't valid UTF-8. The stub accepts
everything (returns {}), so it starts near 0%: one that rejected everything would pass every invalid file,
70% of the suite, without parsing anything.
"""

import re
from collections.abc import Sequence
from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel, ConfigDict


class TomlCase(BaseModel):
    """One file of the suite: its TOML bytes, and for a valid file the expected value as tagged JSON."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str  # e.g. "valid/integer/literals"
    toml: bytes
    expected: str | None  # tagged JSON; None for an invalid file

    @property
    def valid(self) -> bool:
        return self.expected is not None

    @property
    def group(self) -> str:
        """The test file it goes to: kind and folder, e.g. "valid_integer" (top-level files: "valid_misc")."""
        kind, *folders, _ = self.name.split("/")
        return f"{kind}_{re.sub(r'[^a-z0-9]+', '_', (folders[0] if folders else 'misc').lower())}"


class TomlImportSummary(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    groups: int
    visible: int
    held_out: int
    seed: Path
    held_out_tests: Path


class TomlImport:
    PACKAGE = "tomlparse"
    LIST: ClassVar[str] = "tests/files-toml-1.0.0"  # the suite's list of its TOML 1.0.0 files
    SPEC: ClassVar[str] = "specs/v1.0.0.md"
    DOC: ClassVar[str] = "docs/toml-v1.0.0.md"
    HELD_OUT_EVERY: ClassVar[int] = 5  # within each group, the fifth, tenth, ... file (sorted) is held out

    STUB = '''"""A TOML 1.0.0 parser in pure Python, with the standard library's `tomllib` interface."""

from typing import Any


class TOMLDecodeError(ValueError):
    """The document isn't valid TOML 1.0.0."""


def loads(document: str | bytes) -> dict[str, Any]:
    """Parse a TOML document (bytes must be UTF-8) into plain Python values, as `tomllib.loads` does."""
    return {}
'''
    # How a test compares the parser's result with the suite's tagged JSON: the expected values in Python.
    TAGGED = '''"""The suite's tagged JSON as Python values, to compare a parser's result with (MIT).

Part of the acceptance tests: never changed.
"""

import datetime
import json
import math
from typing import Any


def untag(node: Any) -> Any:
    if isinstance(node, list):
        return [untag(item) for item in node]
    if isinstance(node, dict) and set(node) == {"type", "value"} and isinstance(node["value"], str):
        return scalar(node["type"], node["value"])
    return {key: untag(value) for key, value in node.items()}


def scalar(kind: str, value: str) -> Any:
    match kind:
        case "string":
            return value
        case "integer":
            return int(value)
        case "float":
            return float(value)
        case "bool":
            return value == "true"
        case "datetime":
            return datetime.datetime.fromisoformat(micro(value))
        case "datetime-local":
            return datetime.datetime.fromisoformat(micro(value))
        case "date-local":
            return datetime.date.fromisoformat(value)
        case "time-local":
            return datetime.time.fromisoformat(micro(value))
    raise ValueError(f"unknown type {kind!r}")


def micro(value: str) -> str:
    """Fractions of a second beyond microseconds dropped: Python keeps six digits (TOML allows truncation)."""
    head, dot, rest = value.partition(".")
    if not dot:
        return value
    digits = len(rest) - len(rest.lstrip("0123456789"))
    return f"{head}.{rest[:min(digits, 6)]}{rest[digits:]}"


def same(got: Any, want: Any) -> bool:
    """Equal values of equal types; NaN equals NaN."""
    if isinstance(want, float) and math.isnan(want):
        return isinstance(got, float) and math.isnan(got)
    if type(got) is not type(want):
        return False
    if isinstance(want, dict):
        return got.keys() == want.keys() and all(same(got[k], want[k]) for k in want)
    if isinstance(want, list):
        return len(got) == len(want) and all(same(g, w) for g, w in zip(got, want))
    return got == want


def expected(tagged: str) -> Any:
    return untag(json.loads(tagged))
'''
    VALID = '''"""toml-test, valid TOML 1.0.0 files in "{group}": each parses to its expected values (MIT).

Never changed.
"""

import pytest

from tomlparse import loads
from tests.acceptance._tagged import expected, same

CASES = {cases}


@pytest.mark.parametrize(("name", "document", "tagged"), CASES, ids=[n for n, _, _ in CASES])
def test_valid(name: str, document: bytes, tagged: str) -> None:
    assert same(loads(document), expected(tagged))
'''
    INVALID = '''"""toml-test, invalid TOML 1.0.0 files in "{group}": each must be rejected (MIT).

Never changed.
"""

import pytest

from tomlparse import TOMLDecodeError, loads

CASES = {cases}


@pytest.mark.parametrize(("name", "document"), CASES, ids=[n for n, _ in CASES])
def test_invalid(name: str, document: bytes) -> None:
    with pytest.raises(TOMLDecodeError):
        loads(document)
'''

    def __init__(self, suite: Path, out: Path) -> None:
        self._suite = suite  # a checkout of toml-test
        self._out = out

    def cases(self) -> list[TomlCase]:
        listed = (self._suite / self.LIST).read_text().split()
        names = sorted(n.removesuffix(".toml") for n in listed if n.endswith(".toml"))
        tests = self._suite / "tests"
        return [
            TomlCase(
                name=name,
                toml=(tests / f"{name}.toml").read_bytes(),
                expected=(tests / f"{name}.json").read_text() if name.startswith("valid/") else None,
            )
            for name in names
        ]

    def write(self) -> TomlImportSummary:
        cases = self.cases()
        groups = sorted({c.group for c in cases}, key=lambda g: (not g.startswith("valid_"), g))
        seed = self._out / "seed"
        hidden = self._out / "held_out"
        (seed / self.PACKAGE).mkdir(parents=True, exist_ok=True)
        (seed / "tests" / "acceptance").mkdir(parents=True, exist_ok=True)
        (seed / "docs").mkdir(exist_ok=True)
        hidden.mkdir(parents=True, exist_ok=True)
        (seed / self.PACKAGE / "__init__.py").write_text(self.STUB)
        (seed / "tests" / "__init__.py").write_text("")
        (seed / "tests" / "acceptance" / "__init__.py").write_text("")
        (seed / "tests" / "acceptance" / "_tagged.py").write_text(self.TAGGED)
        (seed / self.DOC).write_text((self._suite / self.SPEC).read_text())
        visible = held = 0
        for index, group in enumerate(groups, 1):
            mine = [c for c in cases if c.group == group]
            shown = [c for n, c in enumerate(mine, 1) if n % self.HELD_OUT_EVERY]
            kept = [c for n, c in enumerate(mine, 1) if not n % self.HELD_OUT_EVERY]
            name = f"{index:02d}_{group}"
            if shown:
                (seed / "tests" / "acceptance" / f"test_toml_{name}.py").write_text(self.test(group, shown))
            if kept:
                (hidden / f"test_held_out_{name}.py").write_text(self.test(group, kept))
            visible, held = visible + len(shown), held + len(kept)
        return TomlImportSummary(
            groups=len(groups), visible=visible, held_out=held, seed=seed, held_out_tests=hidden
        )

    def test(self, group: str, cases: Sequence[TomlCase]) -> str:
        if cases[0].valid:
            rows = "".join(f"\n    ({c.name!r}, {c.toml!r}, {c.expected!r})," for c in cases)
            return self.VALID.format(group=group, cases=f"[{rows}\n]")
        rows = "".join(f"\n    ({c.name!r}, {c.toml!r})," for c in cases)
        return self.INVALID.format(group=group, cases=f"[{rows}\n]")
