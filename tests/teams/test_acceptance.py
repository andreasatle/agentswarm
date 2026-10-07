import sys
from pathlib import Path

from pydantic import BaseModel

from agentswarm.board.artifacts import ArtifactFactory
from agentswarm.core.config import CheckConfig
from agentswarm.core.domain import (
    AcceptanceRestored,
    AcceptanceWritten,
    CommitPushed,
    Sized,
)
from agentswarm.gating.checks import CommandCheck
from agentswarm.llm.workers import FakeWorker, Step, Toolbox
from agentswarm.teams.acceptance import AcceptanceTester, AcceptAnswer, AcceptRequest
from agentswarm.teams.executor import ExecutorReport, ExecutorTeam, WorktreeToolbox
from agentswarm.teams.runner import Relevance
from agentswarm.workspace.sandbox import LocalSandbox
from tests.support.harness import Harness
from tests.support.samples import Samples

PYTEST = CheckConfig(
    command=(sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"), failure_exit_codes=(1,)
)
GOOD = (
    "from greeting import farewell\n\n\ndef test_farewell():\n    assert farewell('Ada') == 'Goodbye, Ada!'\n"
)


class Accepting:
    """A harness with one artifact, and the acceptance team on a scripted model."""

    def __init__(self, tmp_path: Path) -> None:
        self.h = Harness(tmp_path)
        self.artifact = self.h.create(task="Add farewell(name) returning 'Goodbye, <name>!'")
        self.runner = CommandCheck("unit_tests", PYTEST, LocalSandbox())

    @staticmethod
    def answer(test: str) -> Step[BaseModel]:
        def step(toolbox: Toolbox | None) -> BaseModel:
            return AcceptAnswer(test=test, summary="farewell")

        return step

    def team(self, *tests: str, requires_sizing: bool = False) -> tuple[AcceptanceTester, FakeWorker]:
        worker = FakeWorker([self.answer(t) for t in tests])
        return AcceptanceTester(Samples.RAW, worker, self.runner, requires_sizing=requires_sizing), worker

    async def act(self, team: AcceptanceTester) -> list[AcceptanceWritten]:
        assert await self.h.runner(team).step() == 1
        history = self.h.board.current().history(self.artifact.id)
        return [e for e in history if isinstance(e, AcceptanceWritten)]


class TestAcceptanceTester:
    async def test_commits_tests_that_fail_before_the_work_and_records_them(self, tmp_path: Path) -> None:
        a = Accepting(tmp_path)
        team, worker = a.team(GOOD)
        (written,) = await a.act(team)
        path = AcceptanceTester.path_for(a.artifact.id)
        assert [f.path for f in written.files] == [path] and written.note == ""
        assert a.h.repository.tip(a.artifact.branch) == written.commit
        assert path in a.h.repository.files_at(a.artifact.branch)
        assert isinstance(worker.calls[0][1], AcceptRequest)
        assert team.relevant(a.h.contexts.build(a.artifact.id)) is Relevance.NO  # once per artifact

    async def test_tests_that_already_pass_or_dont_compile_are_retried_then_skipped(
        self, tmp_path: Path
    ) -> None:
        a = Accepting(tmp_path)
        team, worker = a.team("def test_nothing():\n    assert True\n", "def oops(:\n")
        (written,) = await a.act(team)
        assert written.files == () and "not valid Python" in written.note
        assert len(worker.calls) == 2
        assert a.h.repository.files_at(a.artifact.branch) == []  # nothing was committed

    def test_not_for_fixes_umbrellas_or_unsized_tasks_in_a_planned_project(self, tmp_path: Path) -> None:
        a = Accepting(tmp_path)
        team, _ = a.team()
        assert team.relevant(a.h.contexts.build(a.artifact.id)) is Relevance.YES
        planned, _ = a.team(requires_sizing=True)
        assert planned.relevant(a.h.contexts.build(a.artifact.id)) is Relevance.NO
        fix = ArtifactFactory(a.h.board, Harness.LADDER).create(
            "fix", Samples.EXECUTOR, a.artifact.id, fixes=Samples.finding().id
        )
        assert team.relevant(a.h.contexts.build(fix.id)) is Relevance.NO
        a.h.board.append([Sized(artifact=a.artifact.id, fits=False)])
        assert team.relevant(a.h.contexts.build(a.artifact.id)) is Relevance.NO


class TestProtectedAcceptanceTests:
    async def test_the_executor_waits_for_them_and_cannot_change_them(self, tmp_path: Path) -> None:
        a = Accepting(tmp_path)
        path = AcceptanceTester.path_for(a.artifact.id)

        def work(toolbox: Toolbox | None) -> ExecutorReport:
            assert isinstance(toolbox, WorktreeToolbox)
            toolbox.write_file("greeting.py", "def farewell(name):\n    return f'Goodbye, {name}!'\n")
            toolbox.write_file(path, "def test_weakened():\n    assert True\n")  # tampering
            toolbox.write_file("tests/acceptance/extra.py", "x = 1\n")
            return ExecutorReport(summary="farewell", complete=True)

        executor = ExecutorTeam(FakeWorker([work]), [], Samples.RAW, requires_acceptance=True)
        assert executor.relevant(a.h.contexts.build(a.artifact.id)) is Relevance.NO  # no acceptance tests yet
        team, _ = a.team(GOOD)
        await a.act(team)
        assert await a.h.runner(executor).step() == 1
        history = a.h.board.current().history(a.artifact.id)
        restored = [e for e in history if isinstance(e, AcceptanceRestored)]
        assert len(restored) == 1 and path in restored[0].paths
        assert any(isinstance(e, CommitPushed) for e in history)
        worktree = a.h.repository.checkout_artifact(a.artifact)
        assert worktree.read(path) == GOOD
        assert "tests/acceptance/extra.py" not in worktree.files()
