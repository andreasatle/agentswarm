import sys
from pathlib import Path

import pytest
import yaml

from agentswarm.cli import Cli
from tests.support.samples import Samples
from tests.support.suites import FarewellSuite


class Configs:
    @staticmethod
    def write(tmp_path: Path) -> Path:
        seed = tmp_path / "seed"
        seed.mkdir()
        (seed / "README.md").write_text("seed\n")
        config = {
            "project": "demo",
            "domain": "software",
            "north_star": "A demo",
            "levels": ["raw", "experimental"],
            "seed": "seed",
            "sandbox": {"kind": "local"},
            "checks": {"ok": {"command": [sys.executable, "-c", "pass"]}},
            "teams": [{"use": "fake_executor", "watches": "raw"}],
            "gates": [{"from": "raw", "to": "experimental", "hard_checks": ["ok"]}],
        }
        path = tmp_path / "project.yaml"
        path.write_text(yaml.safe_dump(config))
        return path


class TestCli:
    def test_run_adds_tasks_runs_and_reports(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        config = Configs.write(tmp_path)
        root = tmp_path / "run"
        assert Cli.main(["run", str(config), "--root", str(root), "--task", "Write a note"]) == 0
        out = capsys.readouterr().out
        assert f"{Samples.A} [experimental] Write a note" in out
        assert "promoted raw -> experimental" in out

        assert Cli.main(["status", str(config), "--root", str(root)]) == 0
        assert "promoted raw -> experimental" in capsys.readouterr().out

    def test_seed_is_the_first_commit_on_every_level(self, tmp_path: Path) -> None:
        config = Configs.write(tmp_path)
        root = tmp_path / "run"
        Cli.main(["run", str(config), "--root", str(root), "--task", "Write a note"])
        files = (root / "repo" / "README.md").read_text()
        assert files == "seed\n"


class TestCliEvaluation:
    def test_evaluate_then_calibrate(self, tmp_path: Path) -> None:
        suite = FarewellSuite.write(tmp_path)
        shown: list[str] = []
        cli = Cli(show=shown.append, evaluation_services=FarewellSuite.services)
        workdir = tmp_path / "eval"
        assert (
            cli.dispatch(
                [
                    "evaluate",
                    str(tmp_path / "suite.yaml"),
                    "--workdir",
                    str(workdir),
                    "--variant",
                    "reference",
                ]
            )
            == 0
        )
        assert suite.tasks[0].id in shown[0] and "hidden=pass" in shown[0]
        assert "gate:raw->experimental · complete" in shown[-1]

        shown.clear()
        assert cli.dispatch(["calibrate", str(workdir / "decisions.sqlite")]) == 0
        assert "ECE" in shown[0]

    def test_label_needs_an_existing_log(self, tmp_path: Path) -> None:
        with pytest.raises(SystemExit, match="no decision log"):
            Cli(show=lambda _: None).dispatch(["label", str(tmp_path / "missing.sqlite")])
