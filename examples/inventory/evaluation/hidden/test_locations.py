import json

import pytest
from inventory import Inventory, Item
from inventory.cli import main
from inventory.report import location_report


def make():
    inv = Inventory()
    inv.add(Item("A1", "Bolt", 10, 25))
    inv.add(Item("B2", "Nut", 0, 5))
    return inv


def test_moves_keep_the_total():
    inv = make()
    assert inv.locations("A1") == {"main": 10}
    inv.transfer("A1", "main", "shelf", 4)
    inv.receive("B2", "shelf", 7)
    inv.ship("B2", "shelf", 2)
    assert inv.locations("A1") == {"main": 6, "shelf": 4}
    assert inv.get("A1").quantity == 10 and inv.get("B2").quantity == 5
    assert inv.stock_at("A1", "attic") == 0
    inv.adjust("A1", -6)
    assert inv.locations("A1") == {"shelf": 4}


def test_refusals_change_nothing():
    inv = make()
    with pytest.raises(ValueError):
        inv.transfer("A1", "main", "shelf", 11)
    with pytest.raises(ValueError):
        inv.ship("A1", "shelf", 1)
    with pytest.raises(ValueError):
        inv.transfer("A1", "main", "main", 1)
    with pytest.raises(KeyError):
        inv.receive("Z9", "main", 1)
    with pytest.raises(ValueError):
        inv.receive("A1", "main", -1)
    assert inv.locations("A1") == {"main": 10}


def test_persistence_and_old_files(tmp_path):
    inv = make()
    inv.transfer("A1", "main", "shelf", 4)
    path = tmp_path / "inv.json"
    inv.save(path)
    again = Inventory.load(path)
    assert again.locations("A1") == {"main": 6, "shelf": 4} and again.get("A1").quantity == 10
    old = tmp_path / "old.json"
    old.write_text(json.dumps({"items": [{"sku": "E5", "name": "Tape", "quantity": 3, "price_cents": 9}]}))
    assert Inventory.load(old).locations("E5") == {"main": 3}


def test_report_and_cli(tmp_path, capsys):
    path = str(tmp_path / "inv.json")
    assert main(["--file", path, "add", "A1", "Bolt", "10", "25"]) == 0
    assert main(["--file", path, "transfer", "A1", "main", "shelf", "4"]) == 0
    assert main(["--file", path, "transfer", "A1", "main", "shelf", "40"]) == 1
    capsys.readouterr()
    assert main(["--file", path, "report", "--by-location"]) == 0
    assert capsys.readouterr().out.strip().splitlines() == [
        "== main ==",
        "A1  Bolt  6",
        "== shelf ==",
        "A1  Bolt  4",
    ]
    assert location_report(Inventory()) == ""
