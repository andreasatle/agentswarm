import pytest
from inventory import Inventory, Item


def test_any_spelling_finds_the_same_item():
    inv = Inventory()
    inv.add(Item(" a1 ", "Bolt", 3, 25))
    assert inv.get("A1").sku == "A1"
    assert inv.get(" a1").name == "Bolt"
    assert inv.adjust("a1", 2).quantity == 5
    assert [i.sku for i in inv.items()] == ["A1"]
    assert inv.remove("A1 ").quantity == 5


def test_duplicates_in_any_spelling():
    inv = Inventory()
    inv.add(Item("A1", "Bolt", 3, 25))
    with pytest.raises(ValueError):
        inv.add(Item("a1", "Nut", 1, 1))
