import sys
from pathlib import Path

from agentswarm.board.artifacts import ArtifactFactory
from agentswarm.control.worklist import Worklist
from agentswarm.core.config import CheckConfig, WorklistConfig
from agentswarm.core.domain import Escalated, Paused
from agentswarm.gating.checks import CheckStatus, RatchetCheck
from agentswarm.gating.ratchet import Outcomes, Progress
from agentswarm.workspace.sandbox import LocalSandbox
from tests.support.harness import Harness
from tests.support.samples import Samples

PYTEST = CheckConfig(
    command=(sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"),
    failure_exit_codes=(1, 2),
    ratchet=Samples.EXPERIMENTAL,
)
SPEC = """\
from shout import shout, whisper


def test_shout():
    assert shout("hi") == "HI"


def test_whisper():
    assert whisper("HI") == "hi"


def test_both():
    assert whisper(shout("a")) == "a"
"""


class Suite:
    """A repository whose level holds a spec test file and a module that passes one of its three tests."""

    def __init__(self, tmp_path: Path) -> None:
        self.h = Harness(tmp_path)
        level = self.h.repository.checkout_level(Samples.EXPERIMENTAL)
        level.write("tests/acceptance/test_spec.py", SPEC)
        level.write("shout.py", "def shout(s):\n    return s.upper()\n\n\ndef whisper(s):\n    return s\n")
        level.commit("the spec")
        level.remove()
        self.artifact = self.h.create()
        self.worktree = self.h.repository.checkout_artifact(self.artifact)
        self.check = RatchetCheck("spec", PYTEST, Samples.EXPERIMENTAL, LocalSandbox())

    def module(self, whisper: str) -> None:
        self.worktree.write(
            "shout.py", f"def shout(s):\n    return s.upper()\n\n\ndef whisper(s):\n    {whisper}\n"
        )


class TestOutcomes:
    def test_reads_pytests_one_line_per_test_summary(self) -> None:
        output = "PASSED t.py::test_a\nFAILED t.py::test_b - AssertionError\nERROR t.py::test_c\nnoise\n"
        outcomes = Outcomes.parse(output)
        assert outcomes.passed == {"t.py::test_a"} and outcomes.failed == {"t.py::test_b", "t.py::test_c"}

    def test_progress_needs_a_fixed_test_and_nothing_broken(self) -> None:
        base = Outcomes(passed=frozenset({"a"}), failed=frozenset({"b", "c"}))
        forward = Progress(
            base=base, now=Outcomes(passed=frozenset({"a", "b", "new"}), failed=frozenset({"c"}))
        )
        assert forward.fixed == {"b"} and forward.moves_forward
        broke = Progress(base=base, now=Outcomes(passed=frozenset({"b"}), failed=frozenset()))
        assert broke.broken == {"a"} and not broke.moves_forward  # a test that vanished counts as broken
        only_new = Progress(base=base, now=Outcomes(passed=frozenset({"a", "new"}), failed=frozenset({"b"})))
        assert not only_new.moves_forward  # tests the producer added aren't progress
        assert only_new.acceptable(progress=False) and not broke.acceptable(progress=False)  # a refactor
        own_failing = Progress(
            base=base, now=Outcomes(passed=frozenset({"a", "b"}), failed=frozenset({"c", "mine"}))
        )
        assert own_failing.added_failing == {"mine"} and not own_failing.moves_forward
        assert not own_failing.acceptable(progress=False)


class TestProtected:
    """Only the independent acceptance tests are held to the ratchet; the producer's own are its work."""

    BASE = Outcomes(
        passed=frozenset({"tests/acceptance/t.py::a", "tests/own.py::old_path"}),
        failed=frozenset({"tests/acceptance/t.py::b"}),
    )

    def test_the_producers_own_tests_may_be_deleted_but_not_left_failing(self) -> None:
        protected = ("tests/acceptance/",)
        deleted = Outcomes(
            passed=frozenset({"tests/acceptance/t.py::a", "tests/acceptance/t.py::b"}), failed=frozenset()
        )
        assert Progress(base=self.BASE, now=deleted, protected=protected).moves_forward
        left = Outcomes(
            passed=frozenset({"tests/acceptance/t.py::a", "tests/acceptance/t.py::b"}),
            failed=frozenset({"tests/own.py::old_path"}),
        )
        failing = Progress(base=self.BASE, now=left, protected=protected)
        assert failing.added_failing == {"tests/own.py::old_path"} and not failing.moves_forward
        assert "the producer's own tests" in failing.summary(Samples.EXPERIMENTAL, "c0ffee")

    def test_an_acceptance_test_still_may_not_disappear_or_break(self) -> None:
        gone = Outcomes(passed=frozenset({"tests/acceptance/t.py::b"}), failed=frozenset())
        progress = Progress(base=self.BASE, now=gone, protected=("tests/acceptance/",))
        assert progress.broken == {"tests/acceptance/t.py::a"} and not progress.moves_forward

    def test_without_protected_paths_every_test_is_held(self) -> None:
        deleted = Outcomes(
            passed=frozenset({"tests/acceptance/t.py::a", "tests/acceptance/t.py::b"}), failed=frozenset()
        )
        assert Progress(base=self.BASE, now=deleted).broken == {"tests/own.py::old_path"}


class TestNothingLeft:
    def test_with_every_test_passing_there_is_nothing_left_and_that_is_acceptable(self) -> None:
        done = Outcomes(passed=frozenset({"tests/acceptance/t.py::a"}), failed=frozenset())
        progress = Progress(base=done, now=done, protected=("tests/acceptance/",))
        assert progress.nothing_left and progress.acceptable(progress=True) and not progress.moves_forward
        assert "NOTHING LEFT" in progress.summary(Samples.EXPERIMENTAL, "c0ffee")
        left = Outcomes(passed=frozenset(), failed=frozenset({"tests/acceptance/t.py::a"}))
        assert not Progress(base=left, now=left).acceptable(progress=True)  # still work to do


class TestRatchetCheck:
    def test_passes_progress_and_fails_standing_still_or_breaking(self, tmp_path: Path) -> None:
        s = Suite(tmp_path)
        assert s.check.run(s.worktree.path).status is CheckStatus.FAILED  # no change, no progress
        s.module("return s.lower()")
        result = s.check.run(s.worktree.path)
        assert result.status is CheckStatus.PASSED, result.output
        assert "2 newly passing, 0 broken" in result.output
        s.worktree.write(
            "shout.py", "def shout(s):\n    return s\n\n\ndef whisper(s):\n    return s.lower()\n"
        )
        broken = s.check.run(s.worktree.path)  # whisper fixed, shout broken: not forward
        assert broken.status is CheckStatus.FAILED and "BROKEN" in broken.output


class TestWorklist:
    def test_one_item_per_failing_file_while_none_is_open(self, tmp_path: Path) -> None:
        s = Suite(tmp_path)
        config = WorklistConfig(check="spec", max_open=1, attempts_per_file=2, shown=5)
        worklist = Worklist(
            s.h.board,
            ArtifactFactory(s.h.board, Harness.LADDER),
            s.h.repository,
            Harness.LADDER,
            s.check,
            config,
        )
        s.h.board.append([Samples.commit(s.artifact.id)])  # the harness's own artifact isn't the worklist's
        assert worklist.step() == 1
        (item,) = [a for a in s.h.board.current().artifacts() if a.created_by == Worklist.NAME]
        assert Worklist.file_of(item.task) == "tests/acceptance/test_spec.py"
        assert "2 of its 3 tests fail now" in item.task and "test_both" in item.task
        assert worklist.step() == 0  # one in progress at a time
        s.h.board.append([Escalated(artifact=item.id, reason="stuck"), Paused(reason="provider unavailable")])
        assert worklist.step() == 0  # nothing new while the project is paused

    def test_only_the_named_test_files_become_items(self, tmp_path: Path) -> None:
        s = Suite(tmp_path)
        config = WorklistConfig(check="spec", only=("tests/acceptance/test_other*.py",))
        worklist = Worklist(
            s.h.board,
            ArtifactFactory(s.h.board, Harness.LADDER),
            s.h.repository,
            Harness.LADDER,
            s.check,
            config,
        )
        s.h.board.append([Samples.commit(s.artifact.id)])
        assert worklist.step() == 0  # test_spec.py fails, but isn't one of them
