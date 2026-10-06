import pytest
from inventory import Inventory, Item


def make():
    inv = Inventory()
    inv.add(Item("A1", "Bolt", 3, 25))
    inv.add(Item("B2", "Nut", 1, 99))
    return inv


def test_set_price():
    inv = make()
    assert inv.set_price("A1", 30).price_cents == 30
    with pytest.raises(KeyError):
        inv.set_price("Z9", 1)
    with pytest.raises(ValueError):
        inv.set_price("A1", -1)
    assert inv.get("A1").price_cents == 30


def test_discount_rounds_half_up():
    inv = make()
    inv.discount(10)
    assert inv.get("A1").price_cents == 23
    inv = make()
    inv.discount(50)
    assert inv.get("B2").price_cents == 50
    inv = make()
    inv.discount(100)
    assert inv.get("A1").price_cents == 0


@pytest.mark.parametrize("percent", [-1, 101])
def test_bad_percent_changes_nothing(percent):
    inv = make()
    with pytest.raises(ValueError):
        inv.discount(percent)
    assert [i.price_cents for i in inv.items()] == [25, 99]
