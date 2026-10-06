import pytest
from inventory import Inventory, Item


def make():
    inv = Inventory()
    for item in (Item("C3", "Hex Bolt", 1, 1), Item("A1", "bolt", 1, 1), Item("B2", "Nut", 1, 1)):
        inv.add(item)
    return inv


def test_matches_ignoring_case_sorted_by_sku():
    assert [i.sku for i in make().search("BOLT")] == ["A1", "C3"]
    assert make().search("washer") == []


@pytest.mark.parametrize("text", ["", "   "])
def test_blank_text_is_refused(text):
    with pytest.raises(ValueError):
        make().search(text)
