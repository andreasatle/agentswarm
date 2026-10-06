import json

import pytest
from inventory import Inventory, Item
from inventory.cli import main


def test_undo_each_kind_of_change():
    inv = Inventory()
    inv.add(Item("A1", "Bolt", 3, 25))
    inv.add(Item("B2", "Nut", 1, 5))
    inv.adjust("A1", -2)
    inv.remove("B2")
    assert inv.history() == ["add A1", "add B2", "adjust A1 by -2", "remove B2"]
    assert inv.undo() == "remove B2" and inv.get("B2") == Item("B2", "Nut", 1, 5)
    assert inv.undo() == "adjust A1 by -2" and inv.get("A1").quantity == 3
    assert inv.undo() == "add B2" and len(inv) == 1
    assert inv.undo() == "add A1" and len(inv) == 0
    with pytest.raises(ValueError):
        inv.undo()


def test_history_is_capped_and_saved(tmp_path):
    inv = Inventory()
    inv.add(Item("A1", "Bolt", 0, 25))
    for _ in range(60):
        inv.adjust("A1", 1)
    assert len(inv.history()) == 50
    path = tmp_path / "inv.json"
    inv.save(path)
    again = Inventory.load(path)
    assert again.history() == inv.history()
    assert again.undo() == "adjust A1 by 1" and again.get("A1").quantity == 59
    old = tmp_path / "old.json"
    old.write_text(json.dumps({"items": [{"sku": "E5", "name": "Tape", "quantity": 1, "price_cents": 9}]}))
    assert Inventory.load(old).history() == []


def test_cli_undo_across_runs(tmp_path, capsys):
    path = str(tmp_path / "inv.json")
    assert main(["--file", path, "add", "A1", "Bolt", "3", "25"]) == 0
    assert main(["--file", path, "adjust", "A1", "4"]) == 0
    capsys.readouterr()
    assert main(["--file", path, "undo"]) == 0
    assert capsys.readouterr().out.strip() == "undid: adjust A1 by 4"
    assert Inventory.load(path).get("A1").quantity == 3
    assert main(["--file", path, "undo"]) == 0
    assert main(["--file", path, "undo"]) == 1
    assert "error" in capsys.readouterr().err
