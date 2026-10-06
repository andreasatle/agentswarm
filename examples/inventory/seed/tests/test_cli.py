from inventory.cli import main


def test_add_and_report(tmp_path, capsys):
    path = str(tmp_path / "inv.json")
    assert main(["--file", path, "add", "A1", "Bolt", "3", "25"]) == 0
    assert main(["--file", path, "report"]) == 0
    out = capsys.readouterr().out
    assert "A1  Bolt  3 x 0.25 = 0.75" in out and "Total: 0.75" in out


def test_errors_exit_1(tmp_path, capsys):
    path = str(tmp_path / "inv.json")
    assert main(["--file", path, "remove", "NOPE"]) == 1
    assert "error:" in capsys.readouterr().err
