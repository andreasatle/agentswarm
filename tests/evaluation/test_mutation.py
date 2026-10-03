import sys
from pathlib import Path

from agentswarm.evaluation.mutation import ChangedLines, MutantGenerator, MutationTesting
from agentswarm.workspace.sandbox import LocalSandbox
from tests.support.harness import Harness

PYTEST = (sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider")

FAREWELL = 'def farewell(name: str) -> str:\n    return f"Goodbye, {name}!"\n'
GOOD_TEST = (
    'from greeter import farewell\n\n\ndef test_farewell():\n    assert farewell("Ada") == "Goodbye, Ada!"\n'
)
TRIVIAL_TEST = "def test_it_runs():\n    assert True\n"


class Changes:
    """A harness repository with one artifact whose branch holds the given files."""

    def __init__(self, tmp_path: Path, files: dict[str, str]) -> None:
        self.h = Harness(tmp_path)
        artifact = self.h.create()
        self.base = self.h.repository.tip("experimental")
        self.commit = self.h.push(artifact, files)

    def run(self) -> MutationTesting:
        return MutationTesting(PYTEST, 60, LocalSandbox())


class TestChangedLines:
    def test_parses_new_side_lines_per_file(self) -> None:
        diff = (
            "diff --git a/greeter.py b/greeter.py\n--- a/greeter.py\n+++ b/greeter.py\n"
            "@@ -2,0 +3,2 @@\n+a\n+b\n@@ -9 +11 @@\n-x\n+y\n"
            "diff --git a/old.py b/old.py\n--- a/old.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-gone\n"
        )
        assert ChangedLines.parse(diff) == {"greeter.py": {3, 4, 11}}

    def test_recognises_test_files(self) -> None:
        assert ChangedLines.is_test("tests/test_x.py")
        assert ChangedLines.is_test("test_y.py")
        assert ChangedLines.is_test("pkg/z_test.py")
        assert not ChangedLines.is_test("greeter.py")
        assert not ChangedLines.is_test("tests/data.json")


class TestMutantGenerator:
    def test_mutates_only_changed_lines_and_skips_docstrings(self) -> None:
        source = (
            '"""Module docstring."""\n\n\ndef f(x):\n    return x == 1\n\n\ndef g(x):\n    return x + 1\n'
        )
        mutants = MutantGenerator().mutants("m.py", source, {5})
        assert {m.line for m in mutants} == {5}
        assert {m.description for m in mutants} == {"Eq -> NotEq", "1 -> 2", "return value -> None"}
        assert all("x + 1" in m.source for m in mutants)  # line 9 untouched

    def test_unparsable_source_has_no_mutants(self) -> None:
        assert MutantGenerator().mutants("m.py", "def (:", {1}) == []


class TestMutationTesting:
    def test_real_tests_kill_most_mutants(self, tmp_path: Path) -> None:
        c = Changes(tmp_path, {"greeter.py": FAREWELL, "tests/test_farewell.py": GOOD_TEST})
        report = c.run().run(c.h.repository, c.commit, c.base, "good")
        assert report is not None
        assert report.test_files == ("tests/test_farewell.py",)
        assert report.score >= MutationTesting.MIN_SCORE
        assert c.run().truth(report)

    def test_trivial_tests_kill_nothing(self, tmp_path: Path) -> None:
        c = Changes(tmp_path, {"greeter.py": FAREWELL, "tests/test_trivial.py": TRIVIAL_TEST})
        report = c.run().run(c.h.repository, c.commit, c.base, "trivial")
        assert report is not None
        assert (report.killed, report.score) == (0, 0.0)
        assert not c.run().truth(report)

    def test_a_change_without_tests_is_not_relevant(self, tmp_path: Path) -> None:
        c = Changes(tmp_path, {"greeter.py": FAREWELL})
        report = c.run().run(c.h.repository, c.commit, c.base, "none")
        assert report is not None and report.test_files == ()
        assert not c.run().truth(report)

    def test_no_answer_when_the_tests_fail_unmutated(self, tmp_path: Path) -> None:
        broken = GOOD_TEST.replace("Goodbye", "Farewell")
        c = Changes(tmp_path, {"greeter.py": FAREWELL, "tests/test_farewell.py": broken})
        assert c.run().run(c.h.repository, c.commit, c.base, "broken") is None

    def test_no_answer_without_mutable_code(self, tmp_path: Path) -> None:
        c = Changes(tmp_path, {"README.md": "docs\n", "tests/test_x.py": TRIVIAL_TEST})
        assert c.run().run(c.h.repository, c.commit, c.base, "docs") is None
