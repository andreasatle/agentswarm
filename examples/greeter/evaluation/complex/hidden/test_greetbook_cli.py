import subprocess
import sys


def run(*args, cwd=None):
    """Run the package's command line as `python -m <package> ...` from the project folder."""
    return subprocess.run([sys.executable, "-m", *args], capture_output=True, text=True, cwd=cwd)


def test_add_get_list_search_remove(tmp_path):
    book = str(tmp_path / "book.json")
    assert run("greetbook", "--book", book, "add", "Bob", "Yo").returncode == 0
    assert run("greetbook", "--book", book, "add", "ada", "Hi").returncode == 0
    assert run("greetbook", "--book", book, "add", "Adele", "Hey").returncode == 0
    assert run("greetbook", "--book", book, "get", "BOB").stdout == "Yo\n"
    assert run("greetbook", "--book", book, "list").stdout == "ada: Hi\nAdele: Hey\nBob: Yo\n"
    assert run("greetbook", "--book", book, "search", "AD").stdout == "ada\nAdele\n"
    assert run("greetbook", "--book", book, "remove", "ADELE").returncode == 0
    assert run("greetbook", "--book", book, "list").stdout == "ada: Hi\nBob: Yo\n"


def test_errors(tmp_path):
    book = str(tmp_path / "book.json")
    run("greetbook", "--book", book, "add", "Ada", "Hi")
    before = (tmp_path / "book.json").read_text()
    duplicate = run("greetbook", "--book", book, "add", "ADA", "Again")
    assert duplicate.returncode == 1 and duplicate.stderr
    assert (tmp_path / "book.json").read_text() == before
    missing = run("greetbook", "--book", book, "get", "Nobody")
    assert missing.returncode == 1 and "no entry for Nobody" in missing.stderr
    assert run("greetbook", "--book", book, "remove", "Nobody").returncode == 1


def test_empty_and_invalid_books(tmp_path):
    assert run("greetbook", "--book", str(tmp_path / "none.json"), "list").stdout == ""
    bad = tmp_path / "bad.json"
    bad.write_text("not json")
    assert run("greetbook", "--book", str(bad), "list").returncode == 1


def test_usage_errors(tmp_path):
    book = str(tmp_path / "b.json")
    for args in (["list"], ["--book", book, "wave"], ["--book", book, "add", "Ada"]):
        assert run("greetbook", *args).returncode == 2
