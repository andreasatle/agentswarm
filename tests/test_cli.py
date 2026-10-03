import sys
from pathlib import Path

import pytest
import yaml

from agentswarm.cli import Cli
from agentswarm.core.config import ProjectConfig
from tests.support.paths import RepoPaths
from tests.support.projects import Decomposing
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


class TestCliConfig:
    def test_prints_the_effective_configuration_which_loads_again(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        path = tmp_path / "p.yaml"
        path.write_text(
            yaml.safe_dump(
                {
                    "project": "p",
                    "domain": "software",
                    "north_star": "n",
                    "models": {"coder": {"model": "ollama:m"}},
                }
            )
        )
        assert Cli.main(["config", str(path)]) == 0
        printed = capsys.readouterr().out
        assert "ChangeReadiness" in printed
        assert ProjectConfig.model_validate(yaml.safe_load(printed)).levels.levels[-1] == "production"


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


class TestCliEscalations:
    async def test_list_accept_and_carry_on(self, tmp_path: Path) -> None:
        root = tmp_path / "project"
        project = Decomposing.open(root, join=0.05, persistent_decisions=True)  # the join judge will say no
        goal = project.create_artifact("Write a greeting library with hello and goodbye")
        await project.run_until_idle()
        project.close()
        config = tmp_path / "project.yaml"
        config.write_text(yaml.safe_dump(Decomposing.CONFIG))
        shown: list[str] = []
        cli = Cli(show=shown.append)

        assert cli.dispatch(["escalations", str(config), "--root", str(root)]) == 0
        assert f"{goal.id} [raw]" in shown[-1] and "The parts don't add up" in shown[-1]
        assert "join:raw->experimental: reject (active)" in shown[-1]

        assert (
            cli.dispatch(["resolve", str(config), "--root", str(root), goal.id, "--accept", "--note", "fine"])
            == 0
        )
        cli.dispatch(["escalations", str(config), "--root", str(root)])
        assert shown[-1] == "No open escalations."

        project = Decomposing.open(root, join=0.05, persistent_decisions=True)
        await project.run_until_idle()
        views = project.views()
        assert views.artifact(goal.id).level == "experimental"  # the accepted join went through...
        assert views.open_escalation(goal.id) is not None  # ...and the next one asks again
        project.close()

    def test_resolving_without_an_escalation_is_refused(self, tmp_path: Path) -> None:
        root = tmp_path / "project"
        Decomposing.open(root, join=0.95).close()
        config = tmp_path / "project.yaml"
        config.write_text(yaml.safe_dump(Decomposing.CONFIG))
        with pytest.raises(SystemExit, match="no open escalation"):
            Cli(show=lambda _: None).dispatch(
                ["resolve", str(config), "--root", str(root), "a-0001", "--retry"]
            )


class TestCliSwarm:
    def test_runs_the_quickstart_pair_with_tasks_per_project(self, tmp_path: Path) -> None:
        quickstart = RepoPaths.EXAMPLES / "quickstart"
        swarm = {
            "projects": [
                {"config": str(quickstart / "notes-a.yaml"), "root": str(tmp_path / "a")},
                {"config": str(quickstart / "notes-b.yaml"), "root": str(tmp_path / "b")},
            ]
        }
        path = tmp_path / "swarm.yaml"
        path.write_text(yaml.safe_dump(swarm))
        shown: list[str] = []
        assert Cli(show=shown.append).dispatch(["swarm", str(path), "--task", "notes-a=Write a note"]) == 0
        assert shown[0].startswith("=== notes-a (1 rounds)") and "[experimental] Write a note" in shown[0]
        assert shown[1].startswith("=== notes-b (0 rounds)") and "(no artifacts)" in shown[1]

    def test_a_task_for_an_unknown_project_is_refused(self, tmp_path: Path) -> None:
        path = tmp_path / "swarm.yaml"
        path.write_text(
            yaml.safe_dump(
                {
                    "projects": [
                        {
                            "config": str(RepoPaths.EXAMPLES / "quickstart" / "notes-a.yaml"),
                            "root": str(tmp_path / "a"),
                        }
                    ]
                }
            )
        )
        with pytest.raises(SystemExit, match="PROJECT=TEXT"):
            Cli(show=lambda _: None).dispatch(["swarm", str(path), "--task", "nobody=Write"])
