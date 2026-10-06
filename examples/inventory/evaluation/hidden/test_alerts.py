import json

import pytest
from inventory import Inventory, Item
from inventory.cli import main


def make():
    inv = Inventory()
    for item in (
        Item("A1", "Bolt", 3, 25),
        Item("B2", "Nut", 10, 5),
        Item("C3", "Glue", 0, 400),
        Item("D4", "Box", 5, 1),
    ):
        inv.add(item)
    return inv


def test_points_and_alerts():
    inv = make()
    inv.set_reorder_point("A1", 5)
    inv.set_reorder_point("B2", 4)
    inv.set_reorder_point("C3", 2)
    inv.set_reorder_point("D4", 5)
    assert inv.reorder_point("B2") == 4 and inv.reorder_point("Z9") is None
    assert inv.alerts() == [("A1", 2), ("C3", 2), ("D4", 0)]
    with pytest.raises(KeyError):
        inv.set_reorder_point("Z9", 1)
    with pytest.raises(ValueError):
        inv.set_reorder_point("A1", -1)


def test_remove_and_persistence(tmp_path):
    inv = make()
    inv.set_reorder_point("A1", 5)
    inv.set_reorder_point("C3", 1)
    inv.remove("C3")
    assert inv.alerts() == [("A1", 2)]
    path = tmp_path / "inv.json"
    inv.save(path)
    again = Inventory.load(path)
    assert again.reorder_point("A1") == 5 and again.alerts() == [("A1", 2)]
    old = tmp_path / "old.json"
    old.write_text(json.dumps({"items": [{"sku": "E5", "name": "Tape", "quantity": 1, "price_cents": 9}]}))
    assert Inventory.load(old).alerts() == []


def test_cli(tmp_path, capsys):
    path = str(tmp_path / "inv.json")
    assert main(["--file", path, "add", "A1", "Bolt", "3", "25"]) == 0
    capsys.readouterr()
    assert main(["--file", path, "alerts"]) == 0
    assert capsys.readouterr().out.strip() == "no alerts"
    assert main(["--file", path, "reorder", "A1", "4"]) == 0
    assert main(["--file", path, "alerts"]) == 0
    assert capsys.readouterr().out.strip() == "A1  short 1"
    assert main(["--file", path, "reorder", "Z9", "4"]) == 1
