import asyncio
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

from agentswarm.cli import Cli
from agentswarm.evaluation.probe import (
    CritiqueAnswer,
    CritiqueRequest,
    Critiquing,
    Outcome,
    ProbeExperiment,
    ProbeFiles,
    ProbeKind,
    ProbeRun,
    ProduceAnswer,
    ProduceRequest,
    ProjectFiles,
    SourceFile,
)
from agentswarm.llm.workers import FakeWorker, FakeWorkers, Step, Toolbox
from agentswarm.workspace.sandbox import LocalSandbox
from tests.support.suites import EXAMPLES, PYTEST, GreeterSuite


class Probes:
    """The farewell task as a probe experiment, run on the host with one scripted model."""

    TEST_ON_CLEAN_FAILS = "def test_always_fails():\n    assert False\n"

    def __init__(self, tmp_path: Path) -> None:
        self.task = GreeterSuite.task("farewell")
        project: dict[str, Any] = {
            "project": "greeter",
            "domain": "software",
            "north_star": "A small greeting library",
            "levels": ["raw", "experimental"],
            "seed": str(EXAMPLES / "seed"),
            "models": {"coder": {"model": "ollama:unused"}},
            "teams": [{"use": "fake_executor", "watches": "raw"}],
            "gates": [{"from": "raw", "to": "experimental"}],
        }
        (tmp_path / "project.yaml").write_text(yaml.safe_dump(project))
        suite = {
            "project": "project.yaml",
            "test_command": list(PYTEST),
            "tasks": [
                {
                    "id": "farewell",
                    "task": self.task.task,
                    "hidden_tests": [str(p) for p in self.task.hidden_tests],
                    "reference": {k: str(v) for k, v in self.task.reference.items()},
                }
            ],
        }
        (tmp_path / "suite.yaml").write_text(yaml.safe_dump(suite))
        experiment = {
            "suites": ["suite.yaml"],
            "bugs_per_task": 1,
            "constraints": ["Use only the Python standard library."],
            "sandbox": {"kind": "local"},
            "models": {"small": {"model": "ollama:small"}},
        }
        (tmp_path / "probe.yaml").write_text(yaml.safe_dump(experiment))
        self.experiment = ProbeExperiment.load(tmp_path / "probe.yaml")
        self.workdir = tmp_path / "work"
        self.reference = self.task.reference["greeter.py"].read_text()
        self.hidden = "\n\n".join(t.read_text() for t in self.task.hidden_tests)

    @staticmethod
    def answer(result: BaseModel) -> Step[BaseModel]:
        def step(toolbox: Toolbox | None) -> BaseModel:
            assert toolbox is None  # probes give the model no tools
            return result

        return step

    def run(self, answers: list[BaseModel]) -> tuple[FakeWorker, list[Outcome]]:
        worker = FakeWorker([self.answer(a) for a in answers])
        run = ProbeRun(self.experiment, self.workdir, FakeWorkers({"small": worker}), LocalSandbox())
        report = asyncio.run(run.run())
        outcomes = [r.outcome for r in report.results]
        return worker, outcomes

    def produce(self, content: str) -> ProduceAnswer:
        return ProduceAnswer(files=(SourceFile(path="greeter.py", content=content),), summary="farewell")

    @staticmethod
    def critique(problem: bool, test: str = "") -> CritiqueAnswer:
        return CritiqueAnswer(problem=problem, explanation="looked", test=test)


class TestProbeRun:
    def test_scores_produce_and_critique_against_the_known_truth(self, tmp_path: Path) -> None:
        p = Probes(tmp_path)
        answers: list[BaseModel] = [
            p.produce(p.reference),  # solves the task
            p.critique(False),  # clean code, nothing reported
            p.critique(True, p.hidden),  # the hidden tests fail on the planted bug only
        ]
        worker, outcomes = p.run(answers)
        assert outcomes == [Outcome.SOLVED, Outcome.QUIET, Outcome.CAUGHT]
        produce, clean, bug = (payload for _, payload in worker.calls)
        assert isinstance(produce, ProduceRequest) and {f.path for f in produce.files} >= {"greeter.py"}
        assert produce.constraints == ("Use only the Python standard library.",)
        assert isinstance(clean, CritiqueRequest) and isinstance(bug, CritiqueRequest)
        assert "+def farewell" in clean.diff and clean.diff != bug.diff
        assert worker.calls[1][0] == Critiquing.INSTRUCTIONS
        stored = ProbeFiles(p.workdir).done()[2].answer
        assert stored is not None and CritiqueAnswer.model_validate_json(stored).test == p.hidden

    def test_failures_false_alarms_and_tests_that_prove_nothing(self, tmp_path: Path) -> None:
        p = Probes(tmp_path)
        answers: list[BaseModel] = [
            p.produce("def greet(name):\n    return name\n"),  # no farewell at all
            p.critique(True, Probes.TEST_ON_CLEAN_FAILS),  # clean code "proven" wrong
            p.critique(True, Probes.TEST_ON_CLEAN_FAILS),  # fails on the bug, but on the reference too
        ]
        _, outcomes = p.run(answers)
        assert outcomes == [Outcome.UNSOLVED, Outcome.FALSE_ALARM, Outcome.WRONG_EVIDENCE]

    def test_no_answer_is_recorded_and_a_rerun_resumes(self, tmp_path: Path) -> None:
        p = Probes(tmp_path)
        _, outcomes = p.run([p.produce(p.reference)])  # the script runs out after the first probe
        assert outcomes == [Outcome.SOLVED, Outcome.NO_ANSWER, Outcome.NO_ANSWER]
        worker, again = p.run([])
        assert worker.calls == [] and again == outcomes  # everything was done: nothing asked again
        summary = ProbeFiles(p.workdir).done()
        assert [r.kind for r in summary] == [ProbeKind.PRODUCE, ProbeKind.CRITIQUE, ProbeKind.CRITIQUE]

    def test_a_path_that_leaves_the_project_is_unsolved(self, tmp_path: Path) -> None:
        p = Probes(tmp_path)
        escape = ProduceAnswer(files=(SourceFile(path="../outside.py", content="x"),), summary="escape")
        _, outcomes = p.run([escape, p.critique(False), p.critique(False)])
        assert outcomes[0] is Outcome.UNSOLVED and not (tmp_path / "outside.py").exists()
        assert outcomes[2] is Outcome.MISSED


class TestProjectFiles:
    def test_reads_text_files_and_diffs_versions(self, tmp_path: Path) -> None:
        (tmp_path / "pkg").mkdir()
        (tmp_path / "pkg" / "a.py").write_text("x = 1\n")
        (tmp_path / ".hidden").write_text("secret")
        (tmp_path / "__pycache__").mkdir()
        (tmp_path / "__pycache__" / "a.pyc").write_text("junk")
        files = ProjectFiles.of(tmp_path)
        assert files == {"pkg/a.py": "x = 1\n"}
        diff = ProjectFiles.diff(files, files | {"pkg/a.py": "x = 2\n", "new.py": "y = 1\n"})
        assert "--- /dev/null" in diff and "-x = 1" in diff and "+x = 2" in diff


class TestCliProbe:
    def test_prints_a_line_per_answer_and_the_table(self, tmp_path: Path) -> None:
        p = Probes(tmp_path)
        answers = [p.produce(p.reference), p.critique(False), p.critique(False)]
        worker = FakeWorker([p.answer(a) for a in answers])
        shown: list[str] = []
        cli = Cli(show=shown.append, probe_workers=FakeWorkers({"small": worker}))
        args = ["probe", str(tmp_path / "probe.yaml"), "--workdir", str(p.workdir)]
        assert cli.dispatch(args) == 0
        table = "\n".join(shown)
        assert "farewell/produce" in table and "solved" in table
        assert "small" in table and "produce" in table and "1/1" in table
