import json

import pytest
from inventory import Inventory, Item
from inventory.cli import main


def test_default_and_blank_unit():
    assert Item("A1", "Bolt", 3, 25).unit == "pcs"
    assert Item("A1", "Rope", 3, 25, "m").unit == "m"
    with pytest.raises(ValueError):
        Item("A1", "Bolt", 3, 25, " ")


def test_saved_and_old_files_load(tmp_path):
    inv = Inventory()
    inv.add(Item("A1", "Rope", 3, 25, "m"))
    path = tmp_path / "inv.json"
    inv.save(path)
    assert Inventory.load(path).get("A1").unit == "m"
    old = tmp_path / "old.json"
    old.write_text(json.dumps({"items": [{"sku": "B2", "name": "Nut", "quantity": 1, "price_cents": 5}]}))
    assert Inventory.load(old).get("B2").unit == "pcs"


def test_report_and_cli(tmp_path, capsys):
    path = str(tmp_path / "inv.json")
    assert main(["--file", path, "add", "A1", "Rope", "3", "25", "--unit", "m"]) == 0
    assert main(["--file", path, "add", "B2", "Bolt", "2", "10"]) == 0
    assert main(["--file", path, "report"]) == 0
    out = capsys.readouterr().out
    assert "A1  Rope  3 m x 0.25 = 0.75" in out and "B2  Bolt  2 pcs x 0.10 = 0.20" in out
