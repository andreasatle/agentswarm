import json
from pathlib import Path

from agentswarm.evaluation.jsonpath import JsonPathImport, JsonPathSuite

TESTS = [
    {"name": "basic, root", "selector": "$", "document": [1], "result": [[1]], "result_paths": ["$"]},
    *(
        {"name": f"basic, name {n}", "selector": f"$.a{n}", "document": {f"a{n}": n}, "result": [n]}
        for n in range(5)
    ),
    {"name": "basic, wildcard", "selector": "$.*", "document": {"a": 1, "b": 2}, "results": [[1, 2], [2, 1]]},
    {"name": "filter, bad", "selector": "$[?", "invalid_selector": True},
]


class Source:
    def __init__(self, root: Path) -> None:
        root.mkdir()
        (root / "cts.json").write_text(json.dumps({"tests": TESTS}))
        (root / "rfc.txt").write_text("RFC 9535 JSONPath\n")
        self.root = root


class TestJsonPathSuite:
    def test_names_groups_orders_and_rejections(self, tmp_path: Path) -> None:
        cases = JsonPathSuite(Source(tmp_path / "s").root / "cts.json").cases()
        assert [g for g, _ in cases] == ["basic"] * 7 + ["filter"]
        assert cases[0][1].name == "cts-001-basic-root" and " " not in "".join(c.name for _, c in cases)
        assert cases[6][1].results == ("[1, 2]", "[2, 1]")  # either order is right
        assert cases[7][1].rejected and cases[7][1].document == "null"


class TestJsonPathImport:
    def test_supervised_and_without_labels(self, tmp_path: Path) -> None:
        source = Source(tmp_path / "s").root
        seen = JsonPathImport(source, source / "rfc.txt", tmp_path / "sup").write()
        assert (seen.groups, seen.visible, seen.held_out) == (2, 7, 1)
        hidden = JsonPathImport(source, source / "rfc.txt", tmp_path / "self", hide_all=True).write()
        assert (hidden.visible, hidden.held_out) == (0, 8)
        assert (hidden.seed / "docs" / "rfc9535.txt").read_text() == "RFC 9535 JSONPath\n"
        for path in [*seen.seed.glob("tests/acceptance/test_*.py"), *hidden.held_out_tests.glob("*.py")]:
            compile(path.read_text(), str(path), "exec")
