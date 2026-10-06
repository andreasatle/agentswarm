import json

import pytest
from inventory import Inventory, Item
from inventory.cli import main
from inventory.report import category_report


def make():
    inv = Inventory()
    inv.add(Item("A1", "Bolt", 3, 25, "hardware"))
    inv.add(Item("C3", "Glue", 1, 400, "chemicals"))
    inv.add(Item("B2", "Nut", 10, 5, "hardware"))
    inv.add(Item("D4", "Box", 2, 50))
    return inv


def test_field_and_categories(tmp_path):
    assert Item("A1", "Bolt", 3, 25).category == "general"
    with pytest.raises(ValueError):
        Item("A1", "Bolt", 3, 25, " ")
    inv = make()
    assert inv.categories() == ["chemicals", "general", "hardware"]
    path = tmp_path / "inv.json"
    inv.save(path)
    assert Inventory.load(path).get("C3").category == "chemicals"
    old = tmp_path / "old.json"
    old.write_text(json.dumps({"items": [{"sku": "E5", "name": "Tape", "quantity": 1, "price_cents": 9}]}))
    assert Inventory.load(old).get("E5").category == "general"


def test_category_report():
    assert category_report(make()).splitlines() == [
        "== chemicals ==",
        "C3  Glue  1 x 4.00 = 4.00",
        "Subtotal: 4.00",
        "== general ==",
        "D4  Box  2 x 0.50 = 1.00",
        "Subtotal: 1.00",
        "== hardware ==",
        "A1  Bolt  3 x 0.25 = 0.75",
        "B2  Nut  10 x 0.05 = 0.50",
        "Subtotal: 1.25",
        "Total: 6.25",
    ]


def test_cli(tmp_path, capsys):
    path = str(tmp_path / "inv.json")
    assert main(["--file", path, "add", "A1", "Bolt", "3", "25", "--category", "hardware"]) == 0
    assert main(["--file", path, "add", "B2", "Box", "2", "50"]) == 0
    capsys.readouterr()
    assert main(["--file", path, "list", "--category", "hardware"]) == 0
    assert capsys.readouterr().out.strip().splitlines() == ["A1  Bolt  3  0.25"]
    assert main(["--file", path, "report", "--by-category"]) == 0
    out = capsys.readouterr().out
    assert "== general ==" in out and "== hardware ==" in out and out.strip().endswith("Total: 1.75")
