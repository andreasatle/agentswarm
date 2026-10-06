import pytest
from inventory import Inventory, Item


def test_renames_and_keeps_the_rest():
    inv = Inventory()
    inv.add(Item("A1", "Bolt", 3, 25))
    assert inv.rename("A1", "Hex bolt") == Item("A1", "Hex bolt", 3, 25)
    assert inv.get("A1").name == "Hex bolt"


def test_unknown_sku_and_blank_name():
    inv = Inventory()
    inv.add(Item("A1", "Bolt", 3, 25))
    with pytest.raises(KeyError):
        inv.rename("Z9", "X")
    with pytest.raises(ValueError):
        inv.rename("A1", "  ")
    assert inv.get("A1").name == "Bolt"
