import subprocess
import sys


def run(*args, cwd=None):
    """Run the package's command line as `python -m <package> ...` from the project folder."""
    return subprocess.run([sys.executable, "-m", *args], capture_output=True, text=True, cwd=cwd)


CSV = """name,date
Ada,2026-01-03
 bob ,2026-01-05
ADA,2026-02-01
Ada,2026-01-20
,2026-01-01
Cy,2026-13-01
Bob,2026-01-02
Dee,2026-03-03
"""


def test_report(tmp_path):
    (tmp_path / "in.csv").write_text(CSV)
    result = run("visits", str(tmp_path / "in.csv"), str(tmp_path / "out.md"))
    assert result.returncode == 0
    assert (
        (tmp_path / "out.md").read_text()
        == """# Visits

| Name | Visits | Last visit |
| --- | --- | --- |
| Ada | 3 | 2026-02-01 |
| bob | 2 | 2026-01-05 |
| Dee | 1 | 2026-03-03 |

## Regulars

- Hello, Ada!

Skipped 2 invalid rows.
"""
    )


def test_min_and_no_regulars(tmp_path):
    (tmp_path / "in.csv").write_text("name,date\nAda,2026-01-01\nBob,2026-01-01\n")
    run("visits", str(tmp_path / "in.csv"), str(tmp_path / "out.md"), "--min", "2")
    text = (tmp_path / "out.md").read_text()
    assert text.endswith("## Regulars\n\nNo regulars.\n")
    assert "Skipped" not in text
    run("visits", str(tmp_path / "in.csv"), str(tmp_path / "one.md"), "--min", "1")
    assert "- Hello, Ada!\n- Hello, Bob!\n" in (tmp_path / "one.md").read_text()


def test_bad_input(tmp_path):
    missing = run("visits", str(tmp_path / "none.csv"), str(tmp_path / "out.md"))
    assert missing.returncode == 1 and missing.stderr and not (tmp_path / "out.md").exists()
    (tmp_path / "cols.csv").write_text("who,when\nAda,2026-01-01\n")
    wrong = run("visits", str(tmp_path / "cols.csv"), str(tmp_path / "out.md"))
    assert wrong.returncode == 1 and not (tmp_path / "out.md").exists()
