from pathlib import Path

import yaml

from agentswarm.evaluation.jq import JqImport, JqTestFile


class Source:
    """A jq checkout in miniature: a test file with two sections, the manual, and the base64 tests."""

    def __init__(self, root: Path) -> None:
        self.root = root
        tests = root / "tests"
        tests.mkdir(parents=True)
        cases = "".join(f".+{n}\n1\n{n + 1}\n\n" for n in range(1, 7))
        (tests / "jq.test").write_text(
            "# Tests are groups of three lines\n\n"
            "#\n# Simple values\n#\n\n"
            f"{cases}"
            "%%FAIL\n{(0):1}\njq: error: Cannot use number (0) as object key\n\n"
            "#\n# Modules\n#\n\n"
            'import "a" as foo; foo::a\nnull\n1\n\n'
            ".[] | error\n[1]\n# Runtime error: 1\n"
        )
        (tests / "base64.test").write_text('@base64\n"a"\n"YQ=="\n')
        manual = root / "docs" / "content" / "manual"
        manual.mkdir(parents=True)
        example = {"program": ".a", "input": '{"a": 1}', "output": ["1"]}
        entry = {"title": "Object Identifier-Index: `.foo`", "body": "Gets a field.", "examples": [example]}
        (manual / "manual.yml").write_text(
            yaml.safe_dump(
                {
                    "headline": "jq Manual",
                    "body": "A jq program is a filter.",
                    "sections": [{"title": "Basic filters", "body": "Filters.", "entries": [entry]}],
                }
            )
        )


class TestJqTestFile:
    def test_sections_cases_rejections_and_comments_inside_a_test(self, tmp_path: Path) -> None:
        sections = JqTestFile(Source(tmp_path).root / "tests" / "jq.test").sections()
        assert [title for title, _ in sections] == ["Simple values", "Modules"]
        simple, modules = sections[0][1], sections[1][1]
        assert (simple[0].program, simple[0].given, simple[0].outputs) == (".+1", "1", ("2",))
        assert simple[-1].rejected and simple[-1].program == "{(0):1}"  # jq's error message isn't kept
        assert modules[-1].outputs == ()  # the comment naming the error is skipped


class TestJqImport:
    def test_groups_held_out_left_out_and_the_manual_without_examples(self, tmp_path: Path) -> None:
        summary = JqImport(Source(tmp_path / "jq").root, tmp_path / "out").write()
        # 7 simple cases (one held out), the module group left out, then the manual and base64
        assert (summary.groups, summary.visible, summary.held_out) == (4, 9, 1)
        acceptance = summary.seed / "tests" / "acceptance"
        assert sorted(p.name for p in acceptance.glob("test_*.py")) == [
            "test_jq_01_simple_values.py",
            "test_jq_02_modules.py",  # the runtime-error case stays; only the import is left out
            "test_jq_03_manual_basic_filters.py",
            "test_jq_04_base64.py",
        ]
        assert sorted(p.name for p in summary.held_out_tests.glob("test_*.py")) == [
            "test_held_out_01_simple_values.py"
        ]
        manual = (summary.seed / "docs" / "jq-manual.md").read_text()
        assert "## Basic filters" in manual and "Gets a field." in manual and '{"a": 1}' not in manual
        for path in acceptance.glob("*.py"):
            compile(path.read_text(), str(path), "exec")
        assert "return iter(())" in (summary.seed / "jqpy" / "__init__.py").read_text()  # starts near 0%

    def test_large_sections_are_cut_without_splitting_an_entry(self) -> None:
        source = JqTestFile(Path("unused"))
        unit = [source.case([".", "1", "1"], f"x:{n}") for n in range(30)]
        groups = JqImport.cut("Builtin functions", [unit[:25], unit[25:], unit[:20]])
        assert [(g.name, len(g.cases)) for g in groups] == [
            ("builtin_functions_1", 30),
            ("builtin_functions_2", 20),
        ]
