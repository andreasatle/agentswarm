import pytest
from inventory import Inventory, Item
from inventory.cli import main
from inventory.report import stock_report


def make():
    inv = Inventory()
    for item in (Item("C3", "apple", 1, 100), Item("A1", "Cherry", 2, 10), Item("B2", "banana", 5, 20)):
        inv.add(item)
    return inv


def skus(report):
    return [line.split()[0] for line in report.splitlines()[:-1]]


def test_sorts():
    assert skus(stock_report(make())) == ["A1", "B2", "C3"]
    assert skus(stock_report(make(), by="name")) == ["C3", "B2", "A1"]
    assert skus(stock_report(make(), by="value")) == ["B2", "C3", "A1"]
    assert stock_report(make(), by="value").splitlines()[-1] == "Total: 2.20"


def test_unknown_sort():
    with pytest.raises(ValueError):
        stock_report(make(), by="price")


def test_cli_sort(tmp_path, capsys):
    path = str(tmp_path / "inv.json")
    make().save(path)
    assert main(["--file", path, "report", "--sort", "value"]) == 0
    assert skus(capsys.readouterr().out.strip()) == ["B2", "C3", "A1"]
    with pytest.raises(SystemExit):
        main(["--file", path, "report", "--sort", "price"])
