import json

import pytest
from inventory import Inventory, Item
from inventory.cli import main
from inventory.report import stock_report


def make():
    inv = Inventory()
    inv.add(Item("A1", "Bolt", 10, 25))
    inv.add(Item("B2", "Nut", 3, 5))
    return inv


def test_reserve_fulfil_cancel():
    inv = make()
    inv.reserve("o1", {"A1": 4, "B2": 1})
    inv.reserve("o2", {"A1": 6})
    assert inv.available("A1") == 0 and inv.reserved("A1") == 10 and inv.available("B2") == 2
    inv.fulfil("o1")
    assert inv.get("A1").quantity == 6 and inv.get("B2").quantity == 2 and inv.available("A1") == 0
    inv.cancel("o2")
    assert inv.available("A1") == 6 and inv.orders() == {}
    with pytest.raises(KeyError):
        inv.fulfil("o1")


def test_all_or_nothing_and_refusals():
    inv = make()
    inv.reserve("o1", {"A1": 8})
    with pytest.raises(ValueError):
        inv.reserve("o2", {"B2": 1, "A1": 3})
    assert inv.orders() == {"o1": {"A1": 8}} and inv.available("B2") == 3
    with pytest.raises(KeyError):
        inv.reserve("o3", {"Z9": 1})
    for bad in [("o1", {"B2": 1}), (" ", {"B2": 1}), ("o4", {}), ("o5", {"B2": 0})]:
        with pytest.raises(ValueError):
            inv.reserve(*bad)
    with pytest.raises(ValueError):
        inv.adjust("A1", -3)
    assert inv.adjust("A1", -2).quantity == 8
    with pytest.raises(ValueError):
        inv.remove("A1")


def test_persistence_and_report(tmp_path):
    inv = make()
    inv.reserve("o1", {"B2": 2, "A1": 1})
    path = tmp_path / "inv.json"
    inv.save(path)
    again = Inventory.load(path)
    assert again.orders() == {"o1": {"A1": 1, "B2": 2}} and again.available("B2") == 1
    assert "B2  Nut  3 x 0.05 = 0.15 (reserved 2)" in stock_report(again)
    assert "A1  Bolt  10 x 0.25 = 2.50 (reserved 1)" in stock_report(again)
    old = tmp_path / "old.json"
    old.write_text(json.dumps({"items": [{"sku": "E5", "name": "Tape", "quantity": 1, "price_cents": 9}]}))
    assert Inventory.load(old).orders() == {}


def test_cli(tmp_path, capsys):
    path = str(tmp_path / "inv.json")
    assert main(["--file", path, "add", "A1", "Bolt", "10", "25"]) == 0
    assert main(["--file", path, "add", "B2", "Nut", "3", "5"]) == 0
    assert main(["--file", path, "reserve", "o2", "B2=1", "A1=2"]) == 0
    assert main(["--file", path, "reserve", "o1", "A1=8"]) == 0
    assert main(["--file", path, "reserve", "o3", "A1=1"]) == 1
    capsys.readouterr()
    assert main(["--file", path, "orders"]) == 0
    assert capsys.readouterr().out.strip().splitlines() == ["o1  A1=8", "o2  A1=2 B2=1"]
    assert main(["--file", path, "fulfil", "o1"]) == 0
    assert main(["--file", path, "cancel", "o2"]) == 0
    assert Inventory.load(path).get("A1").quantity == 2
    assert main(["--file", path, "cancel", "o2"]) == 1
