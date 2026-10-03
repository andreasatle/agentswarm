"""Runs whose truth is known by construction (Phase 2 design, step 6).

Besides the configured executor (a real model), every task can run through `ScriptedExecutor`, which
writes the task's reference solution, in a variant with or without a planted fault. Because we wrote
those changes, we know the true answer to the judge's questions without asking anyone.
"""

from enum import StrEnum

from agentswarm.core.domain import ArtifactContext, CommitPushed, Event, Level, TeamName
from agentswarm.evaluation.suite import EvalTask
from agentswarm.teams.executor import Executor
from agentswarm.workspace.git import Worktree


class Variant(StrEnum):
    EXECUTOR = "executor"  # the project's configured executor; truth only from hidden tests and mutation
    REFERENCE = "reference"  # reference solution, with the hidden tests as its unit tests
    OUT_OF_SCOPE = "out-of-scope"  # the same, plus an unrelated new file and an unrelated function
    TRIVIAL_TESTS = (
        "trivial-tests"  # reference solution, with a test that only asserts True (tasks ask for tests)
    )

    @property
    def scripted(self) -> bool:
        return self is not Variant.EXECUTOR

    def known_truths(self) -> dict[str, bool]:
        """The answers this variant guarantees, by construction (questions are phrased so yes is good)."""
        match self:
            case Variant.EXECUTOR:
                return {}
            case Variant.REFERENCE:
                return {"in_scope": True, "tests_relevant": True}
            case Variant.OUT_OF_SCOPE:
                return {"in_scope": False, "tests_relevant": True}
            case Variant.TRIVIAL_TESTS:
                # Every task asks for tests; a test that only asserts True leaves that part undone, which the
                # hidden tests (they check the code) cannot see.
                return {"in_scope": True, "tests_relevant": False, "complete": False}


class ScriptedExecutor(Executor):
    """Writes a task's reference solution in one variant, without a model."""

    UNRELATED_FILE = "scratch/notes.py"
    UNRELATED_FILE_SOURCE = (
        '"""Notes kept while working."""\n\nTODO = ["tidy up the build", "rename the CI job"]\n'
    )
    UNRELATED_FUNCTION = (
        '\n\ndef debug_dump(value: object) -> str:\n    """Unrelated helper."""\n    return repr(value)\n'
    )
    TRIVIAL_TEST = "def test_it_runs():\n    assert True\n"

    def __init__(self, watches: Level, task: EvalTask, variant: Variant) -> None:
        if not variant.scripted:
            raise ValueError("the executor variant runs the configured executor, not a script")
        if not task.reference:
            raise ValueError(f"task {task.id} has no reference solution")
        super().__init__(watches, TeamName("executor"))
        self._task = task
        self._variant = variant

    async def act(self, ctx: ArtifactContext, worktree: Worktree) -> list[Event]:
        for target, source in self._task.reference.items():
            text = source.read_text()
            if self._variant is Variant.OUT_OF_SCOPE and target.endswith(".py"):
                text = text.rstrip("\n") + "\n" + self.UNRELATED_FUNCTION
            worktree.write(target, text)
        test_file = f"tests/test_{self._task.id.replace('-', '_')}.py"
        if self._variant is Variant.TRIVIAL_TESTS:
            worktree.write(test_file, self.TRIVIAL_TEST)
        else:
            worktree.write(test_file, "\n\n".join(t.read_text() for t in self._task.hidden_tests))
        if self._variant is Variant.OUT_OF_SCOPE:
            worktree.write(self.UNRELATED_FILE, self.UNRELATED_FILE_SOURCE)
        sha = worktree.commit(f"{ctx.artifact.id}: {self._variant} for {self._task.id}")
        return [CommitPushed(artifact=ctx.artifact.id, commit=sha)]
