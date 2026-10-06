from inventory import Inventory, Item
from inventory.cli import main


def setup(tmp_path):
    path = str(tmp_path / "inv.json")
    for args in (["C3", "Hex Bolt", "2", "30"], ["A1", "bolt", "3", "25"], ["B2", "Nut", "1", "5"]):
        assert main(["--file", path, "add", *args]) == 0
    return path


def test_store_search():
    inv = Inventory()
    inv.add(Item("B2", "Bolt", 1, 1))
    inv.add(Item("A1", "big bolt", 1, 1))
    assert [i.sku for i in inv.search("BOLT")] == ["A1", "B2"]


def test_cli_search(tmp_path, capsys):
    path = setup(tmp_path)
    capsys.readouterr()
    assert main(["--file", path, "search", "BOLT"]) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert lines == ["A1  bolt  3  0.25", "C3  Hex Bolt  2  0.30"]


def test_no_match_and_blank(tmp_path, capsys):
    path = setup(tmp_path)
    capsys.readouterr()
    assert main(["--file", path, "search", "washer"]) == 0
    assert capsys.readouterr().out.strip() == "no items"
    assert main(["--file", path, "search", "  "]) == 1
    assert "error" in capsys.readouterr().err
