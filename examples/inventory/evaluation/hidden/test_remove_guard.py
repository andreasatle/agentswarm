import json

import pytest
from inventory import Inventory, Item
from inventory.cli import main


def test_store_guard():
    inv = Inventory()
    inv.add(Item("A1", "Bolt", 3, 25))
    inv.add(Item("B2", "Nut", 0, 5))
    with pytest.raises(ValueError):
        inv.remove("A1")
    assert inv.remove("B2").sku == "B2"
    assert inv.remove("A1", force=True).sku == "A1"
    with pytest.raises(KeyError):
        inv.remove("Z9")


def test_cli_guard(tmp_path, capsys):
    path = tmp_path / "inv.json"
    assert main(["--file", str(path), "add", "A1", "Bolt", "3", "25"]) == 0
    before = path.read_text()
    assert main(["--file", str(path), "remove", "A1"]) == 1
    assert "error" in capsys.readouterr().err
    assert path.read_text() == before
    assert main(["--file", str(path), "remove", "A1", "--force"]) == 0
    assert json.loads(path.read_text())["items"] == []
