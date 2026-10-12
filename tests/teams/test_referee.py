import ast
import asyncio
import sys
from pathlib import Path

from pydantic import BaseModel

from agentswarm.core.config import CheckConfig
from agentswarm.core.domain import ArtifactId, CheckDisputed, CommitPushed, Escalated
from agentswarm.gating.checks import CommandCheck
from agentswarm.llm.workers import FakeWorker, Step, Toolbox
from agentswarm.teams.acceptance import AcceptanceTester, AcceptAnswer
from agentswarm.teams.executor import ExecutorReport, ExecutorTeam, WorktreeToolbox
from agentswarm.teams.referee import Dispute, Referee, RulingAnswer
from agentswarm.workspace.sandbox import LocalSandbox
from tests.support.harness import Harness
from tests.support.samples import Samples

PYTEST = CheckConfig(
    command=(sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"), failure_exit_codes=(1,)
)
SPEC = "# Greeting\n\nfarewell(name) returns 'Goodbye, <name>!' for any name.\n"
WRONG = (  # a model-written check that demands what the spec doesn't say
    '"""Checks."""\n\nfrom greeting import farewell\n\n\ndef helper():\n    return "Ada"\n\n\n'
    "def test_farewell():\n    assert farewell(helper()) == 'Bye, Ada!'\n"
)


def ruling(upheld: bool) -> Step[BaseModel]:
    def step(toolbox: Toolbox | None) -> BaseModel:
        return RulingAnswer(upheld=upheld, reason="the spec says 'Goodbye, <name>!'")

    return step


class Disputing:
    """One artifact whose acceptance test is wrong, and a builder that disputes it."""

    def __init__(self, tmp_path: Path) -> None:
        self.h = Harness(tmp_path)
        self.artifact = self.h.create(task="Add farewell(name) as docs/spec.md states it")
        self.runner = CommandCheck("unit_tests", PYTEST, LocalSandbox())
        self.path = AcceptanceTester.path_for(self.artifact.id)

    async def write_tests(self) -> None:
        def answer(toolbox: Toolbox | None) -> BaseModel:
            return AcceptAnswer(test=WRONG, summary="farewell")

        tester = AcceptanceTester(Samples.RAW, FakeWorker([answer]), self.runner)
        assert await self.h.runner(tester).step() == 1

    def builder(self, quote: str) -> ExecutorReport:
        return ExecutorReport(
            summary="farewell, as the spec states",
            complete=True,
            disputes=(
                Dispute(test=f"{self.path}::test_farewell", quote=quote, reason="the spec says Goodbye"),
            ),
        )

    async def build(self, referee: FakeWorker, quote: str) -> list[CheckDisputed]:
        def work(toolbox: Toolbox | None) -> ExecutorReport:
            assert isinstance(toolbox, WorktreeToolbox)
            toolbox.write_file("docs/spec.md", SPEC)
            toolbox.write_file("greeting.py", "def farewell(name):\n    return f'Goodbye, {name}!'\n")
            return self.builder(quote)

        executor = ExecutorTeam(
            FakeWorker([work]),
            [self.runner],
            Samples.RAW,
            requires_acceptance=True,
            referee=Referee(referee, "deepseek", ["docs/spec.md"]),
        )
        assert await self.h.runner(executor).step() == 1
        history = self.h.board.current().history(self.artifact.id)
        self.history = history
        return [e for e in history if isinstance(e, CheckDisputed)]


class TestReferee:
    async def test_an_upheld_dispute_skips_the_test_and_the_work_goes_on(self, tmp_path: Path) -> None:
        d = Disputing(tmp_path)
        await d.write_tests()
        rulings = await d.build(FakeWorker([ruling(True)]), quote="returns 'Goodbye, <name>!'")
        assert [(r.test, r.upheld, r.referee) for r in rulings] == [
            (f"{d.path}::test_farewell", True, "deepseek")
        ]
        assert any(isinstance(e, CommitPushed) for e in d.history)  # the checks pass without the wrong test
        text = d.h.repository.checkout_artifact(d.artifact).read(d.path)
        assert (
            "@pytest.mark.skip(reason=\"ruled wrong by the referee: the spec says 'Goodbye, <name>!'\")"
            in text
        )
        assert text.startswith("import pytest\n")

    async def test_a_rejected_dispute_leaves_the_test(self, tmp_path: Path) -> None:
        d = Disputing(tmp_path)
        await d.write_tests()
        rulings = await d.build(FakeWorker([ruling(False)]), quote="returns 'Goodbye, <name>!'")
        assert [r.upheld for r in rulings] == [False]
        assert any(isinstance(e, Escalated) for e in d.history)  # the test stands, so the checks still fail
        assert "skip" not in d.h.repository.checkout_artifact(d.artifact).read(d.path)

    async def test_a_quote_not_in_the_spec_is_rejected_without_asking_a_model(self, tmp_path: Path) -> None:
        d = Disputing(tmp_path)
        await d.write_tests()
        referee = FakeWorker([])  # any call would fail the test
        rulings = await d.build(referee, quote="farewell returns 'Bye'")
        assert [(r.upheld, r.referee, r.ruling) for r in rulings] == [
            (False, None, "the quote is not in the spec, word for word")
        ]

    def test_only_acceptance_tests_can_be_disputed(self, tmp_path: Path) -> None:
        h = Harness(tmp_path)
        artifact = h.create(task="x")
        worktree = h.repository.checkout_artifact(artifact)
        referee = Referee(FakeWorker([]), "deepseek", ["docs/spec.md"])
        dispute = Dispute(test="tests/test_mine.py::test_x", quote="q", reason="r")
        ruled = asyncio.run(referee.rule(worktree, ArtifactId(artifact.id), dispute, "sol"))
        assert ruled is not None and not ruled.upheld and "not an acceptance test" in ruled.ruling

    def test_skipping_marks_the_function_above_its_decorators(self) -> None:
        source = "import pytest\n\n\n@pytest.mark.parametrize('n', [1, 2])\ndef test_n(n):\n    assert n\n"
        skipped = Referee.skipped(source, "test_n", "wrong")
        tree = ast.parse(skipped)
        function = tree.body[-1]
        assert isinstance(function, ast.FunctionDef) and len(function.decorator_list) == 2
        assert skipped.count("import pytest") == 1
