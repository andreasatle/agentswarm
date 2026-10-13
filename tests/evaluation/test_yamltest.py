from pathlib import Path

from agentswarm.evaluation.yamltest import YamlImport, YamlSpec, YamlSuite


class Suite:
    """The suite's data release in miniature: six anchor tests, one with two cases, and one to reject."""

    def __init__(self, root: Path) -> None:
        self.root = root
        for n in range(1, 7):
            self.test(
                root / f"A{n:03d}", f"Anchor {n}", f"&a{n} x{n}\n", f"=VAL &a{n} :x{n}", f'"x{n}"', ["anchor"]
            )
        self.test(root / "M001" / "00", "Two cases, one", "a: 1\n", "=VAL :a", '{"a": 1}', ["mapping"])
        self.test(root / "M001" / "01", "Two cases, two", "!x a\n", "=VAL <!x> :a", None, ["mapping"])
        self.test(root / "E001", "Bad flow", "[a\n", "+SEQ []", None, ["flow", "error"])
        (root / "E001" / "error").write_text("")

    def test(
        self, folder: Path, title: str, text: str, event: str, values: str | None, tags: list[str]
    ) -> None:
        folder.mkdir(parents=True)
        (folder / "===").write_text(f"{title}\n")
        (folder / "in.yaml").write_text(text)
        (folder / "test.event").write_text(f"+STR\n+DOC\n{event}\n-DOC\n-STR\n")
        if values is not None:
            (folder / "in.json").write_text(values + "\n")
        test = folder.parent.name if folder.name.isdigit() else folder.name
        for tag in tags:
            (self.root / "tags" / tag).mkdir(parents=True, exist_ok=True)
            link = self.root / "tags" / tag / test
            if not link.exists():
                link.symlink_to(Path("..") / ".." / test)


class TestYamlSuite:
    def test_cases_from_folders_and_subfolders_with_their_tags(self, tmp_path: Path) -> None:
        cases = {c.name: c for c in YamlSuite(Suite(tmp_path).root).cases()}
        assert sorted(cases) == ["A001", "A002", "A003", "A004", "A005", "A006", "E001", "M001-00", "M001-01"]
        assert cases["M001-01"].values is None and cases["M001-01"].tags == ("mapping",)
        assert cases["E001"].rejected and set(cases["E001"].tags) == {"error", "flow"}
        assert cases["A001"].events[2] == "=VAL &a1 :x1"


class TestYamlImport:
    def test_groups_tests_held_out_and_the_notation(self, tmp_path: Path) -> None:
        summary = YamlImport(Suite(tmp_path / "suite").root, tmp_path / "out").write()
        # mapping: 2 cases, 3 tests; anchor: 6 cases, the fifth held out, 10 tests; rejected: 1 test
        assert (summary.groups, summary.cases, summary.visible, summary.held_out) == (3, 9, 14, 2)
        acceptance = summary.seed / "tests" / "acceptance"
        assert sorted(p.name for p in acceptance.glob("test_*.py")) == [
            "test_yaml_01_mapping.py",
            "test_yaml_02_anchor.py",
            "test_yaml_03_rejected.py",
        ]
        assert [p.name for p in summary.held_out_tests.glob("test_*.py")] == ["test_held_out_02_anchor.py"]
        assert "'A005'" in (summary.held_out_tests / "test_held_out_02_anchor.py").read_text()
        assert "`+SEQ []`" in (summary.seed / "docs" / "event-notation.md").read_text()
        for path in acceptance.glob("*.py"):
            compile(path.read_text(), str(path), "exec")
        assert "return []" in (summary.seed / "yamlpy" / "__init__.py").read_text()  # starts near 0%


class TestYamlSpec:
    def test_examples_are_left_out_and_productions_kept(self, tmp_path: Path) -> None:
        spec = tmp_path / "spec.md"
        spec.write_text(
            "Text before.\n\n"
            "**Example #. Invalid Byte\nOrder Mark**\n\n```\n- bad\n```\n<!-- note -->\n\n"
            "```\nERROR:\n bad\n```\n"
            "**Legend:**\n* [c-byte-order-mark] <!-- 1:1 -->\n\n"
            "Text after.\n\n```\n[#] c-byte-order-mark ::= xFEFF\n```\n"
        )
        text = YamlSpec(spec).text()
        assert "*Example #. Invalid Byte Order Mark: left out (the examples are tests).*" in text
        assert "- bad" not in text and "ERROR" not in text and "Legend" not in text
        assert "Text before." in text and "Text after." in text and "[#] c-byte-order-mark" in text


class TestYamlHideAll:
    def test_every_case_is_held_out(self, tmp_path: Path) -> None:
        summary = YamlImport(Suite(tmp_path / "suite").root, tmp_path / "out", hide_all=True).write()
        assert (summary.visible, summary.held_out) == (0, 16)
        assert not list((summary.seed / "tests" / "acceptance").glob("test_*.py"))
