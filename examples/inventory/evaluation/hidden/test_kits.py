import json

import pytest
from inventory import Inventory, Item
from inventory.cli import main
from inventory.report import stock_report


def make():
    inv = Inventory()
    inv.add(Item("A1", "Bolt", 10, 25))
    inv.add(Item("B2", "Nut", 9, 5))
    inv.add(Item("C3", "Glue", 3, 400))
    inv.define_kit("K1", "Repair kit", 1500, {"A1": 4, "B2": 4, "C3": 1})
    return inv


def test_availability_and_selling():
    inv = make()
    assert inv.kit_available("K1") == 2
    assert inv.kit("K1").components == {"A1": 4, "B2": 4, "C3": 1}
    inv.sell_kit("K1", 2)
    assert [i.quantity for i in inv.items()] == [2, 1, 1] and inv.kit_available("K1") == 0
    with pytest.raises(ValueError):
        inv.sell_kit("K1", 1)
    assert [i.quantity for i in inv.items()] == [2, 1, 1]


def test_definition_rules():
    inv = make()
    for bad in [
        ("K2", " ", 1, {"A1": 1}),
        ("A1", "x", 1, {"B2": 1}),
        ("K1", "x", 1, {"B2": 1}),
        ("K2", "x", -1, {"A1": 1}),
        ("K2", "x", 1, {}),
        ("K2", "x", 1, {"A1": 0}),
    ]:
        with pytest.raises(ValueError):
            inv.define_kit(*bad)
    with pytest.raises(KeyError):
        inv.define_kit("K2", "x", 1, {"Z9": 1})
    with pytest.raises(ValueError):
        inv.add(Item("K1", "Clash", 1, 1))
    with pytest.raises(ValueError):
        inv.remove("C3")
    assert inv.get("C3").quantity == 3
    with pytest.raises(ValueError):
        inv.sell_kit("K1", 0)


def test_persistence_and_report(tmp_path):
    inv = make()
    path = tmp_path / "inv.json"
    inv.save(path)
    again = Inventory.load(path)
    assert [k.sku for k in again.kits()] == ["K1"] and again.kit_available("K1") == 2
    report = stock_report(again).splitlines()
    assert report[-2:] == ["Kits:", "K1  Repair kit  available 2"]
    assert report[-3].startswith("Total:")
    old = tmp_path / "old.json"
    old.write_text(json.dumps({"items": [{"sku": "E5", "name": "Tape", "quantity": 1, "price_cents": 9}]}))
    assert Inventory.load(old).kits() == []
    assert "Kits:" not in stock_report(Inventory.load(old))


def test_cli(tmp_path, capsys):
    path = str(tmp_path / "inv.json")
    for args in (["A1", "Bolt", "10", "25"], ["B2", "Nut", "9", "5"]):
        assert main(["--file", path, "add", *args]) == 0
    assert main(["--file", path, "kit", "K1", "Pair kit", "100", "A1=4", "B2=4"]) == 0
    assert main(["--file", path, "sell-kit", "K1", "3"]) == 1
    assert main(["--file", path, "sell-kit", "K1", "2"]) == 0
    assert [i.quantity for i in Inventory.load(path).items()] == [2, 1]
    assert main(["--file", path, "remove", "A1"]) == 1
