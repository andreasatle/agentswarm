from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import ClassVar

from agentswarm.evaluation.seed import SeedGroup, SeedImport


class Numbers(SeedImport[int]):
    """A seed from two groups of numbers: one test per number."""

    PREFIX: ClassVar[str] = "test_numbers"

    def groups(self) -> list[SeedGroup[int]]:
        return self.cut("Small ones", [[n] for n in range(1, 8)], 40) + self.cut("Big", [[100]], 40)

    def files(self) -> Mapping[str, str]:
        return {"numbers/__init__.py": "", "docs/numbers.md": "# Numbers\n"}

    def test(self, title: str, cases: Sequence[int]) -> str:
        return f"# {title}: {list(cases)}\n"

    def extra(self) -> Mapping[str, str]:
        return {"conftest.py": "# limits\n"}


class TestSeedImport:
    def test_writes_files_visible_tests_and_every_fifth_held_out(self, tmp_path: Path) -> None:
        summary = Numbers(tmp_path).write()
        assert (summary.groups, summary.cases, summary.visible, summary.held_out) == (2, 8, 7, 1)
        acceptance = summary.seed / "tests" / "acceptance"
        assert sorted(p.name for p in acceptance.iterdir()) == [
            "test_numbers_01_small_ones.py",
            "test_numbers_02_big.py",
        ]
        assert (
            acceptance / "test_numbers_01_small_ones.py"
        ).read_text() == "# small_ones: [1, 2, 3, 4, 6, 7]\n"
        assert sorted(p.name for p in summary.held_out_tests.iterdir()) == [
            "conftest.py",
            "test_held_out_01_small_ones.py",
        ]
        assert (summary.seed / "docs" / "numbers.md").read_text() == "# Numbers\n"

    def test_cut_keeps_units_whole(self) -> None:
        units = [list(range(25)), list(range(5)), list(range(20))]
        groups = Numbers.cut("Builtin functions", units, 40)
        assert [(g.name, len(g.cases)) for g in groups] == [
            ("builtin_functions_1", 30),
            ("builtin_functions_2", 20),
        ]
        assert Numbers.cut("Empty", [], 40) == []
