import pytest
from inventory import Inventory, Item
from inventory.cli import main


def make():
    inv = Inventory()
    for item in (
        Item("A1", "Hex bolt", 10, 25),
        Item("B2", "bolt", 3, 25),
        Item("C3", "Nut", 10, 5),
        Item("D4", "Glue", 1, 400),
        Item("E5", "Big Bolt", 0, 90),
    ):
        inv.add(item)
    return inv


def skus(items):
    return [item.sku for item in items]


def test_filters_combine():
    inv = make()
    assert skus(inv.query(name="BOLT")) == ["A1", "B2", "E5"]
    assert skus(inv.query(name="bolt", min_quantity=1, max_price=25)) == ["A1", "B2"]
    assert skus(inv.query(min_quantity=10, max_quantity=10)) == ["A1", "C3"]
    assert skus(inv.query(min_price=26)) == ["D4", "E5"]
    assert inv.query(name="washer") == []


def test_sorting_ties_and_limit():
    inv = make()
    assert skus(inv.query(sort="quantity")) == ["E5", "D4", "B2", "A1", "C3"]
    assert skus(inv.query(sort="quantity", descending=True)) == ["A1", "C3", "B2", "D4", "E5"]
    assert skus(inv.query(sort="price", descending=True, limit=3)) == ["D4", "E5", "A1"]
    assert skus(inv.query(sort="name")) == ["E5", "B2", "D4", "A1", "C3"]
    assert skus(inv.query(sort="value", descending=True, limit=2)) == ["D4", "A1"]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"sort": "colour"},
        {"min_quantity": 5, "max_quantity": 2},
        {"min_price": 9, "max_price": 1},
        {"limit": 0},
        {"name": " "},
    ],
)
def test_meaningless_requests(kwargs):
    with pytest.raises(ValueError):
        make().query(**kwargs)


def test_cli(tmp_path, capsys):
    path = str(tmp_path / "inv.json")
    make().save(path)
    assert (
        main(["--file", path, "find", "--name", "bolt", "--sort", "quantity", "--desc", "--limit", "2"]) == 0
    )
    assert capsys.readouterr().out.strip().splitlines() == ["A1  Hex bolt  10  0.25", "B2  bolt  3  0.25"]
    assert main(["--file", path, "find", "--min-price", "1000"]) == 0
    assert capsys.readouterr().out.strip() == "no items"
    assert main(["--file", path, "find", "--min-qty", "5", "--max-qty", "1"]) == 1
    assert "error" in capsys.readouterr().err
