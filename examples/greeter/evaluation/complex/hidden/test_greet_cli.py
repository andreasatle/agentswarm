import json
import subprocess
import sys

import pytest


def run(*args, cwd=None):
    """Run the package's command line as `python -m <package> ...` from the project folder."""
    return subprocess.run([sys.executable, "-m", *args], capture_output=True, text=True, cwd=cwd)


def test_hello_styles():
    assert run("greetcli", "hello", "Ada").stdout == "Hello, Ada!\n"
    assert (
        run("greetcli", "hello", "Ada", "--style", "formal", "--title", "Dr.").stdout
        == "Good day, Dr. Ada.\n"
    )
    assert run("greetcli", "hello", "Ada", "--style", "shout").stdout == "HELLO, ADA!\n"
    assert run("greetcli", "bye", "Ada").stdout == "Goodbye, Ada!\n"


def test_config_defaults(tmp_path):
    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps({"style": "formal", "title": "Prof."}))
    result = run("greetcli", "--config", str(cfg), "hello", "Ada")
    assert result.returncode == 0 and result.stdout == "Good day, Prof. Ada.\n"
    plain = run("greetcli", "--config", str(cfg), "hello", "Ada", "--style", "plain", "--title", "")
    assert plain.returncode == 0 and plain.stdout == "Hello, Ada!\n"


@pytest.mark.parametrize(
    "args",
    [["wave", "Ada"], ["hello"], ["hello", "Ada", "--style", "whisper"], ["hello", "Ada", "--title", "Dr."]],
)
def test_usage_errors(args):
    result = run("greetcli", *args)
    assert result.returncode == 2 and result.stderr and not result.stdout


def test_bad_config(tmp_path):
    missing = run("greetcli", "--config", str(tmp_path / "none.json"), "hello", "Ada")
    assert missing.returncode == 1 and missing.stderr
    bad = tmp_path / "bad.json"
    bad.write_text("{oops")
    broken = run("greetcli", "--config", str(bad), "hello", "Ada")
    assert broken.returncode == 1 and broken.stderr
