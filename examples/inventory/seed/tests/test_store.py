import pytest
from inventory import Inventory, Item


def test_add_get_adjust_remove():
    inventory = Inventory()
    inventory.add(Item("A1", "Bolt", 3, 25))
    assert inventory.adjust("A1", 2).quantity == 5
    assert inventory.get("A1").quantity == 5
    assert inventory.remove("A1").sku == "A1"
    assert len(inventory) == 0


def test_duplicates_and_negative_stock_are_refused():
    inventory = Inventory()
    inventory.add(Item("A1", "Bolt", 3, 25))
    with pytest.raises(ValueError):
        inventory.add(Item("A1", "Nut", 1, 5))
    with pytest.raises(ValueError):
        inventory.adjust("A1", -4)


def test_save_and_load(tmp_path):
    inventory = Inventory()
    inventory.add(Item("B2", "Nut", 10, 5))
    inventory.add(Item("A1", "Bolt", 3, 25))
    path = tmp_path / "inv.json"
    inventory.save(path)
    assert [i.sku for i in Inventory.load(path).items()] == ["A1", "B2"]
    assert len(Inventory.load(tmp_path / "missing.json")) == 0
