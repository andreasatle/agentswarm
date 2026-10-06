import pytest
from inventory import Inventory, Item


def test_strictly_below_fewest_first_then_sku():
    inv = Inventory()
    for item in (Item("C3", "a", 2, 1), Item("A1", "b", 5, 1), Item("B2", "c", 2, 1), Item("D4", "d", 0, 1)):
        inv.add(item)
    assert [i.sku for i in inv.below(5)] == ["D4", "B2", "C3"]
    assert inv.below(0) == []


def test_negative_threshold_is_refused():
    with pytest.raises(ValueError):
        Inventory().below(-1)
