import pytest
from inventory.models import Item


def test_value_is_quantity_times_price():
    assert Item("A1", "Bolt", 3, 25).value_cents == 75


@pytest.mark.parametrize(
    "args", [("", "Bolt", 1, 1), ("A1", " ", 1, 1), ("A1", "Bolt", -1, 1), ("A1", "Bolt", 1, -1)]
)
def test_invalid_items_are_refused(args):
    with pytest.raises(ValueError):
        Item(*args)
