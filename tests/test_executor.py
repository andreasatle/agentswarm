from pathlib import Path

import pytest

from agentswarm.domain import CommitPushed, DecisionId, Escalated, FindingAdded, Rejected
from agentswarm.executor import ExecutorReport, ExecutorRequest, ExecutorTeam, FakeExecutor, WorktreeToolbox
from agentswarm.git import Worktree
from agentswarm.teams import Relevance
from agentswarm.workers import FakeWorker, LangChainWorker, Toolbox, WorkerError
from tests.fakes import Replies
from tests.harness import Checks, Harness
from tests.samples import Samples


class Workspace:
    """A harness with one artifact checked out, ready for an executor to work in."""

    def __init__(self, tmp_path: Path) -> None:
        self.h = Harness(tmp_path)
        self.artifact = self.h.create(task="Add a farewell function")
        self.worktree = self.h.repository.checkout_artifact(self.artifact)

    def ctx(self):
        return self.h.contexts.build(self.artifact.id)


class Toolboxes:
    """A check that passes when greeting.py defines `farewell`."""

    CHECK = Checks.python(
        "has_farewell",
        "import pathlib, sys; "
        "sys.exit(0 if 'def farewell' in pathlib.Path('greeting.py').read_text() else 1)",
    )

    @classmethod
    def of(cls, worktree: Worktree) -> WorktreeToolbox:
        return WorktreeToolbox(worktree, [cls.CHECK])


class Steps:
    """Scripted executor behaviour for FakeWorker."""

    @staticmethod
    def write(files: dict[str, str], summary: str = "add farewell", complete: bool = True):
        def step(toolbox: Toolbox | None) -> ExecutorReport:
            assert isinstance(toolbox, WorktreeToolbox)
            for path, content in files.items():
                toolbox.write_file(path, content)
            return ExecutorReport(summary=summary, complete=complete)

        return step


class TestWorktreeToolbox:
    def test_write_read_list_and_delete(self, tmp_path: Path) -> None:
        w = Workspace(tmp_path)
        box = Toolboxes.of(w.worktree)
        assert box.list_files() == "(the project has no files yet)"
        assert box.write_file("pkg/a.py", "x = 1\n") == "wrote pkg/a.py (6 characters)"
        assert box.read_file("pkg/a.py") == "x = 1\n"
        assert box.list_files() == "pkg/a.py"
        assert box.delete_file("pkg/a.py") == "deleted pkg/a.py"
        assert box.read_file("pkg/a.py").startswith("error:")

    @pytest.mark.parametrize(
        "path", ["/etc/passwd", "../outside.txt", "a/../../outside.txt", ".git/config", ""]
    )
    def test_refuses_paths_outside_the_project(self, tmp_path: Path, path: str) -> None:
        box = Toolboxes.of(Workspace(tmp_path).worktree)
        assert box.write_file(path, "x").startswith("error:")
        assert box.read_file(path).startswith("error:")
        assert not (tmp_path / "outside.txt").exists()

    def test_refuses_to_follow_a_link_out_of_the_project(self, tmp_path: Path) -> None:
        w = Workspace(tmp_path)
        (w.worktree.path / "escape").symlink_to(tmp_path)
        assert WorktreeToolbox(w.worktree, []).write_file("escape/x.txt", "x").startswith("error:")
        assert not (tmp_path / "x.txt").exists()

    def test_run_checks_reports_failures_with_output(self, tmp_path: Path) -> None:
        w = Workspace(tmp_path)
        box = Toolboxes.of(w.worktree)
        box.write_file("greeting.py", "def hello(): ...\n")
        assert "has_farewell: FAILED" in box.run_checks()
        box.write_file("greeting.py", "def farewell(): ...\n")
        assert box.run_checks() == "has_farewell: passed"
        assert box.check_report().passed


class TestExecutor:
    def test_relevant_before_the_first_commit_and_after_a_rejection_only(self, tmp_path: Path) -> None:
        h = Harness(tmp_path)
        executor = FakeExecutor(Samples.RAW)
        artifact = h.create()
        assert executor.relevant(h.contexts.build(artifact.id)) is Relevance.YES
        h.push(artifact, {"a.txt": "a"})
        assert executor.relevant(h.contexts.build(artifact.id)) is Relevance.NO
        h.board.append(
            [
                Rejected(
                    artifact=artifact.id, level=Samples.RAW, findings=(Samples.finding(),), gate_decision=None
                )
            ]
        )
        assert executor.relevant(h.contexts.build(artifact.id)) is Relevance.YES


class TestFakeExecutor:
    async def test_commits_the_task_and_requested_fixes(self, tmp_path: Path) -> None:
        w = Workspace(tmp_path)
        w.h.board.append([FindingAdded(finding=Samples.finding())])
        (event,) = await FakeExecutor(Samples.RAW).act(w.ctx(), w.worktree)
        assert isinstance(event, CommitPushed)
        assert "fixed: Does not address every part of the task" in w.worktree.read("a-0001.md")


class TestExecutorTeam:
    async def test_commits_work_that_passes_the_checks(self, tmp_path: Path) -> None:
        w = Workspace(tmp_path)
        worker = FakeWorker([Steps.write({"greeting.py": "def farewell(name): ...\n"})])
        (event,) = await ExecutorTeam(worker, [Toolboxes.CHECK], Samples.RAW).act(w.ctx(), w.worktree)
        assert isinstance(event, CommitPushed)
        assert w.h.repository.tip(w.artifact.branch) == event.commit
        assert w.h.repository.files_at(w.artifact.branch) == ["greeting.py"]

    async def test_request_carries_task_findings_constraints_and_files(self, tmp_path: Path) -> None:
        w = Workspace(tmp_path)
        w.worktree.write("existing.py", "")
        w.h.board.append(
            [
                CommitPushed(artifact=w.artifact.id, commit=w.worktree.commit("seed")),
                Rejected(
                    artifact=w.artifact.id,
                    level=Samples.RAW,
                    findings=(Samples.finding(),),
                    gate_decision=DecisionId("d-1"),
                ),
            ]
        )
        worker = FakeWorker([Steps.write({"greeting.py": "def farewell(): ...\n"})])
        team = ExecutorTeam(worker, [Toolboxes.CHECK], Samples.RAW, constraints=["No new dependencies"])
        await team.act(w.ctx(), w.worktree)
        ((instructions, payload),) = worker.calls
        assert isinstance(payload, ExecutorRequest)
        assert payload.instruction.goal == "Add a farewell function"
        assert payload.instruction.must_fix == (Samples.finding(),)
        assert payload.instruction.constraints == ("No new dependencies",)
        assert payload.files == ("existing.py",)
        assert payload.north_star == Harness.NORTH_STAR
        assert "must_fix" in instructions

    async def test_failing_checks_are_committed_but_escalated(self, tmp_path: Path) -> None:
        w = Workspace(tmp_path)
        worker = FakeWorker([Steps.write({"greeting.py": "def hello(): ...\n"})])
        commit, escalated = await ExecutorTeam(worker, [Toolboxes.CHECK], Samples.RAW).act(
            w.ctx(), w.worktree
        )
        assert isinstance(commit, CommitPushed)
        assert isinstance(escalated, Escalated)
        assert "has_farewell: FAILED" in escalated.reason

    async def test_incomplete_work_is_escalated(self, tmp_path: Path) -> None:
        w = Workspace(tmp_path)
        worker = FakeWorker(
            [Steps.write({"greeting.py": "def farewell(): ...\n"}, "half done", complete=False)]
        )
        events = await ExecutorTeam(worker, [Toolboxes.CHECK], Samples.RAW).act(w.ctx(), w.worktree)
        assert isinstance(events[-1], Escalated)
        assert "incomplete: half done" in events[-1].reason

    async def test_no_changes_is_escalated_without_a_commit(self, tmp_path: Path) -> None:
        w = Workspace(tmp_path)
        worker = FakeWorker([Steps.write({})])
        (event,) = await ExecutorTeam(worker, [Toolboxes.CHECK], Samples.RAW).act(w.ctx(), w.worktree)
        assert isinstance(event, Escalated)
        assert "no changes" in event.reason

    async def test_worker_failure_keeps_the_work_done_so_far(self, tmp_path: Path) -> None:
        w = Workspace(tmp_path)

        def half(toolbox: Toolbox | None) -> ExecutorReport:
            assert isinstance(toolbox, WorktreeToolbox)
            toolbox.write_file("greeting.py", "def farewell(): ...\n")
            raise WorkerError("ran out of model calls")

        commit, escalated = await ExecutorTeam(FakeWorker([half]), [], Samples.RAW).act(w.ctx(), w.worktree)
        assert isinstance(commit, CommitPushed)
        assert isinstance(escalated, Escalated)
        assert w.h.repository.files_at(w.artifact.branch) == ["greeting.py"]

    async def test_worker_failure_is_escalated(self, tmp_path: Path) -> None:
        w = Workspace(tmp_path)

        def fail(toolbox: Toolbox | None) -> ExecutorReport:
            raise WorkerError("model unreachable")

        (event,) = await ExecutorTeam(FakeWorker([fail]), [], Samples.RAW).act(w.ctx(), w.worktree)
        assert isinstance(event, Escalated)
        assert "model unreachable" in event.reason

    async def test_drives_the_toolbox_through_a_real_langchain_agent(self, tmp_path: Path) -> None:
        w = Workspace(tmp_path)
        model = (
            Replies()
            .call("list_files")
            .call("write_file", path="greeting.py", content="def farewell(name):\n    return f'Bye {name}'\n")
            .call("run_checks")
            .call("ExecutorReport", summary="add farewell", complete=True)
            .model()
        )
        team = ExecutorTeam(LangChainWorker(model), [Toolboxes.CHECK], Samples.RAW)
        (event,) = await team.act(w.ctx(), w.worktree)
        assert isinstance(event, CommitPushed)
        assert "def farewell" in w.worktree.read("greeting.py")
        message = w.h.repository.git.run(w.worktree.path, "log", "-1", "--format=%s")
        assert message == "a-0001: add farewell"
