import csv
import io

from inventory import Inventory, Item
from inventory.cli import main
from inventory.export import to_csv


def test_header_rows_and_quoting():
    inv = Inventory()
    inv.add(Item("B2", 'Nut, "large"', 10, 5))
    inv.add(Item("A1", "Bolt", 3, 25))
    text = to_csv(inv)
    assert text.splitlines()[0] == "sku,name,quantity,price_cents"
    rows = list(csv.reader(io.StringIO(text)))
    assert rows[1:] == [["A1", "Bolt", "3", "25"], ["B2", 'Nut, "large"', "10", "5"]]
    assert "\r" not in text


def test_cli_export(tmp_path):
    path = tmp_path / "inv.json"
    out = tmp_path / "out.csv"
    assert main(["--file", str(path), "add", "A1", "Bolt", "3", "25"]) == 0
    assert main(["--file", str(path), "export", str(out)]) == 0
    assert out.read_text() == "sku,name,quantity,price_cents\nA1,Bolt,3,25\n"
