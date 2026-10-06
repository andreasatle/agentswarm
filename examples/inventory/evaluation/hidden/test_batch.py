import pytest
from inventory import Inventory, Item
from inventory.batch import BatchError, apply_lines
from inventory.cli import main

SCRIPT = [
    "# delivery",
    'add A1 "Hex bolt" 10 25',
    "",
    "add B2 Nut 5 5",
    "adjust A1 -3",
    "remove B2",
]


def test_applies_to_a_copy():
    original = Inventory()
    original.add(Item("Z9", "Old", 1, 1))
    result, applied = apply_lines(original, SCRIPT)
    assert applied == 4
    assert [(i.sku, i.name, i.quantity) for i in result.items()] == [("A1", "Hex bolt", 7), ("Z9", "Old", 1)]
    assert [i.sku for i in original.items()] == ["Z9"]


@pytest.mark.parametrize(
    ("bad", "line"),
    [
        ("frobnicate A1", 3),
        ("adjust A1", 3),
        ("adjust A1 lots", 3),
        ("remove Q7", 3),
        ("adjust A1 -11", 3),
        ("add C3 X -1 5", 3),
    ],
)
def test_failures_name_the_line(bad, line):
    lines = ["# start", "add A1 Bolt 10 25", bad, "add B2 Nut 1 1"]
    with pytest.raises(BatchError) as failed:
        apply_lines(Inventory(), lines)
    assert failed.value.line == line and str(failed.value).startswith(f"line {line}: ")
    assert isinstance(failed.value, ValueError)


def test_cli_all_or_nothing(tmp_path, capsys):
    path = tmp_path / "inv.json"
    good = tmp_path / "good.txt"
    good.write_text("\n".join(SCRIPT) + "\n")
    assert main(["--file", str(path), "batch", str(good)]) == 0
    assert capsys.readouterr().out.strip() == "applied 4 commands"
    before = path.read_text()
    bad = tmp_path / "bad.txt"
    bad.write_text("adjust A1 5\nremove NOPE\n")
    assert main(["--file", str(path), "batch", str(bad)]) == 1
    assert capsys.readouterr().err.strip().startswith("error: line 2: ")
    assert path.read_text() == before
