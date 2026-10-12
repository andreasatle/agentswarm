import sys
from pathlib import Path

from pydantic import BaseModel

from agentswarm.board.artifacts import ArtifactFactory
from agentswarm.core.config import CheckConfig
from agentswarm.core.domain import (
    AcceptanceRestored,
    AcceptanceWritten,
    ArtifactCreated,
    ArtifactId,
    CommitPushed,
    Promoted,
    Sized,
)
from agentswarm.gating.checks import CheckStatus, CommandCheck
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
        assert team.relevant(a.h.contexts.build(a.artifact.id)) is Relevance.YES  # an umbrella gets its own


class TestSplitAcceptance:
    async def test_the_last_child_inherits_the_parents_tests(self, tmp_path: Path) -> None:
        a = Accepting(tmp_path)
        factory = ArtifactFactory(a.h.board, Harness.LADDER)
        kids = [factory.child(a.artifact, i, f"part {i}", Samples.EXECUTOR) for i in (1, 2)]
        sized = Sized(artifact=a.artifact.id, fits=False, children=tuple(k.id for k in kids))
        a.h.board.append([*(ArtifactCreated(artifact=k) for k in kids), sized])
        team, _ = a.team(GOOD, GOOD.replace("farewell", "parting"))
        last = a.h.contexts.build(kids[1].id)
        assert last.closes == a.artifact.id and team.relevant(last) is Relevance.NO  # waits for its sibling
        merged = Promoted(
            artifact=kids[0].id,
            from_level=Samples.RAW,
            to_level=Samples.EXPERIMENTAL,
            gate_decision=None,
            commit="m",
        )
        a.h.board.append([merged])
        assert team.relevant(a.h.contexts.build(kids[1].id)) is Relevance.NO  # and for its parent's tests
        # One step: the umbrella's tests, from the original task, on its own branch; then the last child's.
        assert await a.h.runner(team).step() == 2
        views = a.h.board.current()
        (parent,) = [e for e in views.history(a.artifact.id) if isinstance(e, AcceptanceWritten)]
        assert [f.path for f in parent.files] == [AcceptanceTester.path_for(a.artifact.id)]
        history = views.history(kids[1].id)
        (written,) = [e for e in history if isinstance(e, AcceptanceWritten)]
        paths = [f.path for f in written.files]
        assert paths == [AcceptanceTester.path_for(kids[1].id), AcceptanceTester.path_for(a.artifact.id)]
        assert set(paths) <= set(a.h.repository.files_at(kids[1].branch))


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


class TestAcceptanceFiles:
    def test_the_tests_of_several_artifacts_run_together(self, tmp_path: Path) -> None:
        """Merged acceptance tests of a split's children sit side by side; pytest must collect them all."""
        a = Accepting(tmp_path)
        worktree = a.h.repository.checkout_artifact(a.artifact)
        for i in (1, 2):
            worktree.write(
                AcceptanceTester.path_for(ArtifactId(f"a-0001.{i}")), f"def test_{i}():\n    pass\n"
            )
        result = a.runner.run_on(worktree.path, [AcceptanceTester.FOLDER])
        assert result.status is CheckStatus.PASSED, result.output


class TestDerivedChecks:
    async def test_projects_without_labels_get_checks_without_hand_computed_answers(
        self, tmp_path: Path
    ) -> None:
        a = Accepting(tmp_path)
        seen: list[str] = []

        class Recording(FakeWorker):
            async def run[T: BaseModel](
                self, instructions: str, payload: BaseModel, output: type[T], toolbox: Toolbox | None = None
            ) -> T:
                seen.append(instructions)
                return await super().run(instructions, payload, output, toolbox)

        team = AcceptanceTester(Samples.RAW, Recording([a.answer(GOOD)]), a.runner, derived=True)
        await a.act(team)
        assert seen == [AcceptanceTester.DERIVED] and "round trips" in seen[0]
