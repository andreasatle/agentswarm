from pathlib import Path

from agentswarm.gating.harness import TestHarness


class TestTestHarness:
    def test_the_producers_levers_on_pytest_go_and_the_evaluators_stay(self, tmp_path: Path) -> None:
        files = {
            "conftest.py": "x",
            "tests/conftest.py": "x",
            "tests/acceptance/conftest.py": "kept",
            "tests/acceptance/test_a.py": "kept",
            "pytest.py": "x",
            "_pytest/__init__.py": "x",
            "sitecustomize.py": "x",
            "pytest.ini": "[pytest]\naddopts = -p evil\n",
            "pyproject.toml": "kept",
            "shout.py": "kept",
        }
        for name, text in files.items():
            (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
            (tmp_path / name).write_text(text)
        removed = TestHarness(("tests/acceptance/",)).guard(tmp_path)
        assert set(removed) == {
            "conftest.py",
            "tests/conftest.py",
            "pytest.py",
            "_pytest/",
            "sitecustomize.py",
            "pytest.ini",
        }
        assert (tmp_path / "pytest.ini").read_text() == TestHarness.INI
        kept = [n for n, t in files.items() if t == "kept"]
        assert all((tmp_path / n).read_text() == "kept" for n in kept)

    def test_with_nothing_protected_every_conftest_is_the_producers_and_stays(self, tmp_path: Path) -> None:
        (tmp_path / "conftest.py").write_text("fixtures")
        TestHarness(()).guard(tmp_path)
        assert (tmp_path / "conftest.py").read_text() == "fixtures"

    def test_a_conftest_stays_only_as_it_was_before_the_work(self, tmp_path: Path) -> None:
        for name in ("conftest.py", "tests/conftest.py", "tests/unit/conftest.py"):
            (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
            (tmp_path / name).write_text("hooked" if name != "tests/conftest.py" else "fixtures")
        trusted = {"tests/conftest.py": "fixtures", "conftest.py": "fixtures"}  # the root one was changed
        removed = TestHarness((), trusted).guard(tmp_path)
        assert set(removed) == {"conftest.py", "tests/unit/conftest.py"}
        assert (tmp_path / "tests/conftest.py").read_text() == "fixtures"

    def test_a_guarded_copy_leaves_the_source_alone_and_goes_away(self, tmp_path: Path) -> None:
        source = tmp_path / "work"
        source.mkdir()
        (source / "conftest.py").write_text("x")
        with TestHarness(("tests/",)).copy(source) as copy:
            assert not (copy / "conftest.py").exists() and (copy / "pytest.ini").is_file()
        assert (source / "conftest.py").is_file() and not copy.exists()
