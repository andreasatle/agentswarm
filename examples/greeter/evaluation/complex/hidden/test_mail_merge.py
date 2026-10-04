import subprocess
import sys


def run(*args, cwd=None):
    """Run the package's command line as `python -m <package> ...` from the project folder."""
    return subprocess.run([sys.executable, "-m", *args], capture_output=True, text=True, cwd=cwd)


def test_merge(tmp_path):
    (tmp_path / "t.txt").write_text("Dear {name}, {{see}} you in {city}.\n")
    (tmp_path / "c.csv").write_text(
        "name,city\nAda Lovelace,London\nada lovelace!,Paris\n,Nowhere\nBob,Oslo\n"
    )
    result = run("merge", str(tmp_path / "t.txt"), str(tmp_path / "c.csv"), str(tmp_path / "out"))
    assert result.returncode == 0 and result.stdout == "Wrote 3 files.\n"
    out = tmp_path / "out"
    assert sorted(p.name for p in out.iterdir()) == ["ada-lovelace-2.txt", "ada-lovelace.txt", "bob.txt"]
    assert (out / "ada-lovelace.txt").read_text() == "Dear Ada Lovelace, {see} you in London.\n"
    assert (out / "ada-lovelace-2.txt").read_text() == "Dear ada lovelace!, {see} you in Paris.\n"


def test_unknown_field_writes_nothing(tmp_path):
    (tmp_path / "t.txt").write_text("Dear {name} of {country}\n")
    (tmp_path / "c.csv").write_text("name,city\nAda,London\n")
    result = run("merge", str(tmp_path / "t.txt"), str(tmp_path / "c.csv"), str(tmp_path / "out"))
    assert result.returncode == 1 and result.stderr and not (tmp_path / "out").exists()


def test_no_name_column_and_missing_files(tmp_path):
    (tmp_path / "t.txt").write_text("Hi\n")
    (tmp_path / "c.csv").write_text("who\nAda\n")
    assert (
        run("merge", str(tmp_path / "t.txt"), str(tmp_path / "c.csv"), str(tmp_path / "out")).returncode == 1
    )
    assert (
        run("merge", str(tmp_path / "none.txt"), str(tmp_path / "c.csv"), str(tmp_path / "out")).returncode
        == 1
    )
    assert not (tmp_path / "out").exists()
