"""The tutorial's scripts: the offline ones run, and the tutorial and its folder name the same scripts."""

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from tests.support.paths import RepoPaths

TUTORIAL = RepoPaths.ROOT / "tutorial"


class Scripts:
    @staticmethod
    def run(command: list[str], runs: Path) -> str:
        env = os.environ | {"AGENTSWARM_RUNS": str(runs)}
        result = subprocess.run(command, cwd=TUTORIAL, env=env, capture_output=True, text=True, timeout=300)
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout

    @staticmethod
    def in_folder() -> set[str]:
        return {p.name for p in TUTORIAL.iterdir() if re.match(r"^\d\d[a-z]?_.+\.(sh|py)$", p.name)}

    @staticmethod
    def named_in_tutorial() -> set[str]:
        return set(
            re.findall(r"tutorial/(\d\d[a-z]?_[a-z_]+\.(?:sh|py))", (TUTORIAL / "README.md").read_text())
        )


class TestTutorialScripts:
    def test_the_tutorial_and_its_folder_name_the_same_scripts(self) -> None:
        assert Scripts.named_in_tutorial() == Scripts.in_folder()

    @pytest.mark.parametrize(
        ("script", "start"),
        [
            ("01_board.py", "from agentswarm"),
            ("02_claims.py", "# Claims go"),
            ("03b_project_api.py", "# Load and check"),
        ],
    )
    def test_the_tutorial_shows_the_python_scripts_as_they_are(self, script: str, start: str) -> None:
        source = (TUTORIAL / script).read_text()
        shown = source[source.index(start) :].rstrip("\n")
        assert shown in (TUTORIAL / "README.md").read_text()

    def test_shell_scripts_are_executable(self) -> None:
        for name in Scripts.in_folder():
            if name.endswith(".sh"):
                assert os.access(TUTORIAL / name, os.X_OK), name

    @pytest.mark.parametrize(
        ("script", "expected"),
        [
            ("01_board.py", "replay gives the same views: True"),
            ("02_claims.py", "red team's late result accepted: False"),
            ("03b_project_api.py", "a-0002 is at experimental"),
        ],
    )
    def test_python_steps(self, tmp_path: Path, script: str, expected: str) -> None:
        assert expected in Scripts.run([sys.executable, script], tmp_path)

    def test_the_first_run(self, tmp_path: Path) -> None:
        out = Scripts.run(["bash", "03_first_run.sh"], tmp_path)
        assert "promoted raw -> experimental" in out
        assert (tmp_path / "tutorial" / "step03-quickstart" / "blackboard.sqlite").is_file()
        assert "Promote a-0002 to experimental" in Scripts.run(
            ["bash", "03_first_run.sh"], tmp_path
        )  # rerunnable

    def test_a_step_that_needs_an_earlier_one_says_which(self, tmp_path: Path) -> None:
        env = os.environ | {"AGENTSWARM_RUNS": str(tmp_path)}
        result = subprocess.run(
            ["bash", "07_findings.sh"], cwd=TUTORIAL, env=env, capture_output=True, text=True
        )
        assert result.returncode == 1 and "run tutorial/05_shadow.sh first" in result.stderr
