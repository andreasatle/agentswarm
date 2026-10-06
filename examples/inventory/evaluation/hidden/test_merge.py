import pytest
from inventory import Inventory, Item


def inv(*items):
    result = Inventory()
    for item in items:
        result.add(item)
    return result


def test_adds_new_and_sums_matching():
    mine = inv(Item("A1", "Bolt", 3, 25))
    mine.merge(inv(Item("A1", "Bolt", 2, 25), Item("B2", "Nut", 4, 5)))
    assert [(i.sku, i.quantity) for i in mine.items()] == [("A1", 5), ("B2", 4)]


@pytest.mark.parametrize("other", [Item("A1", "Screw", 1, 25), Item("A1", "Bolt", 1, 30)])
def test_any_conflict_changes_nothing(other):
    mine = inv(Item("A1", "Bolt", 3, 25))
    with pytest.raises(ValueError):
        mine.merge(inv(Item("B2", "Nut", 4, 5), other))
    assert [(i.sku, i.quantity) for i in mine.items()] == [("A1", 3)]
