import dataclasses

from inventory import Inventory, Item
from inventory.report import Summary, summarize


def test_summary():
    inv = Inventory()
    for item in (Item("C3", "a", 2, 50), Item("A1", "b", 4, 25), Item("B2", "c", 1, 10)):
        inv.add(item)
    assert summarize(inv) == Summary(items=3, units=7, value_cents=210, top_sku="A1")  # A1 and C3 tie at 100


def test_empty_and_frozen():
    summary = summarize(Inventory())
    assert summary == Summary(items=0, units=0, value_cents=0, top_sku=None)
    assert dataclasses.is_dataclass(summary)
    try:
        summary.items = 5
    except dataclasses.FrozenInstanceError:
        pass
    else:
        raise AssertionError("Summary must be frozen")
