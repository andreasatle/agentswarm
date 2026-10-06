import pytest
from inventory import Inventory, Item
from inventory.cli import main
from inventory.importer import ImportFailed, from_csv


def test_any_column_order():
    items = from_csv('name,sku,price_cents,quantity\nBolt,A1,25,3\n"Nut, big",B2,5,10\n')
    assert items == [Item("A1", "Bolt", 3, 25), Item("B2", "Nut, big", 10, 5)]


def test_every_problem_is_reported():
    text = "sku,name,quantity,price_cents\nA1,Bolt,three,25\nB2,Nut,1,5\nB2,Nut,1,5\nC3,,1,5\nD4,Box,1\n"
    with pytest.raises(ImportFailed) as failed:
        from_csv(text)
    lines = [error.split(":")[0] for error in failed.value.errors]
    assert lines == ["line 2", "line 4", "line 5", "line 6"]
    assert "duplicate sku B2" in failed.value.errors[1]
    assert isinstance(failed.value, ValueError)


def test_bad_header():
    with pytest.raises(ImportFailed) as failed:
        from_csv("sku,name,qty,price_cents\nA1,Bolt,3,25\n")
    assert failed.value.errors[0].startswith("line 1")


def test_cli_all_or_nothing(tmp_path, capsys):
    path = str(tmp_path / "inv.json")
    good = tmp_path / "good.csv"
    good.write_text("sku,name,quantity,price_cents\nA1,Bolt,3,25\nB2,Nut,10,5\n")
    assert main(["--file", path, "import", str(good)]) == 0
    assert capsys.readouterr().out.strip() == "imported 2 items"
    clash = tmp_path / "clash.csv"
    clash.write_text("sku,name,quantity,price_cents\nC3,Glue,1,400\nA1,Bolt,3,25\n")
    assert main(["--file", path, "import", str(clash)]) == 1
    capsys.readouterr()
    bad = tmp_path / "bad.csv"
    bad.write_text("sku,name,quantity,price_cents\nD4,Box,x,1\nE5,Tape,1,y\n")
    assert main(["--file", path, "import", str(bad)]) == 1
    assert len(capsys.readouterr().err.strip().splitlines()) == 2
    assert [i.sku for i in Inventory.load(path).items()] == ["A1", "B2"]
