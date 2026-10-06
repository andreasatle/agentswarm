from inventory import Inventory, Item


def test_sums_every_item():
    inv = Inventory()
    inv.add(Item("A1", "Bolt", 3, 25))
    inv.add(Item("B2", "Nut", 10, 7))
    assert inv.total_value_cents() == 145


def test_empty_is_zero():
    assert Inventory().total_value_cents() == 0
