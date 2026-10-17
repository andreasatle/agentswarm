"""The JSONPath project: RFC 9535 (2024), a query language less familiar to models than TOML or YAML.

From the JSONPath Compliance Test Suite (BSD-2: cts.json, each test a query and a document with the nodelist
it selects, or a query that must be rejected) this writes a seed project with an empty implementation, the
RFC as its spec, and one test file per group. Every fifth test of each group is held out, or all of them for
a project without labels (`hide_all`). Groups follow the suite's own names ("filter", "slice selector", ...).
Some queries may select their nodes in more than one valid order; such a test accepts each. The RFC is given
whole: 11 of the 715 test queries appear in it, all short and generic.

The interface: `query(selector, document)` returns the values of the selected nodes, in order, and raises
`JsonPathError` for a query the RFC doesn't allow. The stub returns no nodes and rejects nothing, so it starts
near 0%.
"""

import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict

from agentswarm.evaluation.seed import SeedGroup, SeedImport


class JsonPathCase(BaseModel):
    """One test of the suite."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str  # a pytest id, no spaces: "cts-012-basic-wildcard-shorthand-object-data"
    selector: str
    document: str  # JSON text; "null" for a query that must be rejected
    results: tuple[str, ...] | None  # each acceptable nodelist as JSON text; None: the query must be rejected

    @property
    def rejected(self) -> bool:
        return self.results is None


class JsonPathSuite:
    """cts.json: its tests, in its order."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def cases(self) -> list[tuple[str, JsonPathCase]]:
        """(group, case) for every test; the group is the part of the suite's name before the first comma."""
        tests: list[dict[str, Any]] = json.loads(self._path.read_text())["tests"]
        found: list[tuple[str, JsonPathCase]] = []
        for n, test in enumerate(tests, 1):
            name = str(test["name"])
            slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:60].strip("-")
            if test.get("invalid_selector"):
                results = None
            elif "results" in test:
                results = tuple(json.dumps(r) for r in test["results"])
            else:
                results = (json.dumps(test["result"]),)
            case = JsonPathCase(
                name=f"cts-{n:03d}-{slug}",
                selector=str(test["selector"]),
                document=json.dumps(test.get("document")),
                results=results,
            )
            found.append((name.split(",", 1)[0].strip(), case))
        return found


class JsonPathImport(SeedImport[JsonPathCase]):
    PACKAGE = "jsonpathpy"
    PREFIX: ClassVar[str] = "test_jsonpath"
    SUITE: ClassVar[str] = "cts.json"
    DOC: ClassVar[str] = "docs/rfc9535.txt"
    MAX_CASES: ClassVar[int] = 40  # a larger group is cut into several test files, a work item each

    STUB = '''"""JSONPath (RFC 9535) in pure Python."""

from typing import Any


class JsonPathError(ValueError):
    """The query isn't valid JSONPath as RFC 9535 defines it."""


def query(selector: str, document: Any) -> list[Any]:
    """The values of the nodes the query selects from the document (parsed JSON), in the order RFC 9535 gives
    them; JsonPathError for a query RFC 9535 doesn't allow."""
    return []
'''
    JSON = '''"""JSON values compared as the compliance suite means them (BSD-2).

Part of the acceptance tests: never changed.
"""

from typing import Any


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
    HEAD = '''"""JSONPath Compliance Test Suite, "{title}" (BSD-2). Never changed."""

import json

import pytest

from {package} import JsonPathError, query
from tests.acceptance._json import same
'''
    QUERIES = """
QUERIES = {cases}


@pytest.mark.parametrize(("name", "selector", "document", "results"), QUERIES, ids=[c[0] for c in QUERIES])
def test_query(name: str, selector: str, document: str, results: list[str]) -> None:
    got = query(selector, json.loads(document))
    assert any(same(got, json.loads(r)) for r in results)  # some queries allow more than one order
"""
    INVALID = """
INVALID = {cases}


@pytest.mark.parametrize(("name", "selector"), INVALID, ids=[c[0] for c in INVALID])
def test_invalid(name: str, selector: str) -> None:
    with pytest.raises(JsonPathError):
        query(selector, None)
"""

    def __init__(self, suite: Path, rfc: Path | None, out: Path, hide_all: bool = False) -> None:
        super().__init__(out)
        self._suite = suite  # a checkout of the compliance test suite
        self._rfc = (
            rfc  # RFC 9535 as plain text; None: no spec in the seed (the swarm drafts one from a goal)
        )
        self._hide_all = hide_all  # every test held out: the RFC and no labels (self-supervised)

    def groups(self) -> list[SeedGroup[JsonPathCase]]:
        cases = JsonPathSuite(self._suite / self.SUITE).cases()
        titles = list(dict.fromkeys(group for group, _ in cases))
        found: list[SeedGroup[JsonPathCase]] = []
        for title in titles:
            found += self.cut(title, [[c] for g, c in cases if g == title], self.MAX_CASES)
        return found

    def held(self, position: int, case: JsonPathCase) -> bool:
        return self._hide_all or super().held(position, case)

    def files(self) -> Mapping[str, str]:
        return {
            f"{self.PACKAGE}/__init__.py": self.STUB,
            "tests/__init__.py": "",
            "tests/acceptance/__init__.py": "",
            "tests/acceptance/_json.py": self.JSON,
            **({self.DOC: self._rfc.read_text()} if self._rfc is not None else {}),
        }

    def test(self, title: str, cases: Sequence[JsonPathCase]) -> str:
        text = self.HEAD.format(title=title, package=self.PACKAGE)
        valid = [c for c in cases if not c.rejected]
        invalid = [c for c in cases if c.rejected]
        if valid:
            rows = "".join(
                f"\n    ({c.name!r}, {c.selector!r}, {c.document!r}, {list(c.results or ())!r}),"
                for c in valid
            )
            text += self.QUERIES.format(cases=f"[{rows}\n]")
        if invalid:
            rows = "".join(f"\n    ({c.name!r}, {c.selector!r})," for c in invalid)
            text += self.INVALID.format(cases=f"[{rows}\n]")
        return text
