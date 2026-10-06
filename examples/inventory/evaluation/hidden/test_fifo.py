import json

import pytest
from inventory import Inventory, Item
from inventory.cli import main
from inventory.report import valuation_report


def make():
    inv = Inventory()
    inv.add(Item("A1", "Bolt", 10, 20))
    inv.receive("A1", 5, 30)
    return inv


def test_oldest_first():
    inv = make()
    assert inv.get("A1").quantity == 15 and inv.cost_value_cents("A1") == 350
    assert inv.sell("A1", 12) == 10 * 20 + 2 * 30
    assert inv.get("A1").quantity == 3 and inv.cost_value_cents("A1") == 90
    assert inv.sell("A1", 3) == 90 and inv.cost_value_cents() == 0


def test_too_little_changes_nothing():
    inv = make()
    with pytest.raises(ValueError):
        inv.sell("A1", 16)
    assert inv.get("A1").quantity == 15 and inv.cost_value_cents("A1") == 350
    with pytest.raises(KeyError):
        inv.receive("Z9", 1, 1)
    with pytest.raises(ValueError):
        inv.receive("A1", 1, -1)


def test_adjust_goes_through_the_layers():
    inv = make()
    inv.adjust("A1", -11)
    assert inv.cost_value_cents("A1") == 4 * 30
    inv.adjust("A1", 2)
    assert inv.get("A1").quantity == 6 and inv.cost_value_cents("A1") == 4 * 30 + 2 * 20


def test_persistence(tmp_path):
    inv = make()
    inv.sell("A1", 4)
    path = tmp_path / "inv.json"
    inv.save(path)
    again = Inventory.load(path)
    assert again.get("A1").quantity == 11 and again.cost_value_cents("A1") == 6 * 20 + 5 * 30
    assert again.sell("A1", 7) == 6 * 20 + 30
    old = tmp_path / "old.json"
    old.write_text(json.dumps({"items": [{"sku": "E5", "name": "Tape", "quantity": 3, "price_cents": 9}]}))
    assert Inventory.load(old).cost_value_cents() == 27


def test_report_and_cli(tmp_path, capsys):
    path = str(tmp_path / "inv.json")
    assert main(["--file", path, "add", "A1", "Bolt", "10", "20"]) == 0
    assert main(["--file", path, "receive", "A1", "5", "30"]) == 0
    capsys.readouterr()
    assert main(["--file", path, "sell", "A1", "12"]) == 0
    assert capsys.readouterr().out.strip() == "cost of goods sold: 2.60"
    assert main(["--file", path, "sell", "A1", "12"]) == 1
    assert main(["--file", path, "valuation"]) == 0
    assert capsys.readouterr().out.strip().splitlines() == ["A1  3  0.90", "Total cost: 0.90"]
    assert valuation_report(Inventory()) == "Total cost: 0.00"
