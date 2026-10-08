from pathlib import Path

import pytest

from agentswarm.core.domain import (
    AttemptFailed,
    CommitPushed,
    DecisionId,
    Escalated,
    Event,
    Finding,
    FindingAdded,
    FindingKind,
    Paused,
    Rejected,
    Severity,
    ToolsUsed,
)
from agentswarm.llm.workers import FakeWorker, LangChainWorker, ProviderUnavailable, Toolbox, WorkerError
from agentswarm.teams.executor import (
    ExecutorReport,
    ExecutorRequest,
    ExecutorTeam,
    FakeExecutor,
    WorktreeToolbox,
)
from agentswarm.teams.runner import Relevance
from agentswarm.workspace.git import Worktree
from tests.support.fakes import Replies
from tests.support.harness import Checks, Harness
from tests.support.samples import Samples


class Traces:
    @staticmethod
    def drop(events: list[Event]) -> list[Event]:
        """An executor's events without its tool-call records (ToolsUsed), which every attempt adds."""
        return [e for e in events if not isinstance(e, ToolsUsed)]


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
        (event,) = Traces.drop(
            await ExecutorTeam(worker, [Toolboxes.CHECK], Samples.RAW).act(w.ctx(), w.worktree)
        )
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
        Traces.drop(await team.act(w.ctx(), w.worktree))
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
        commit, escalated = Traces.drop(
            await ExecutorTeam(worker, [Toolboxes.CHECK], Samples.RAW).act(w.ctx(), w.worktree)
        )
        assert isinstance(commit, CommitPushed)
        assert isinstance(escalated, Escalated)
        assert "has_farewell: FAILED" in escalated.reason

    async def test_attempts_keep_the_first_that_passes_and_throw_the_others_away(
        self, tmp_path: Path
    ) -> None:
        w = Workspace(tmp_path)
        worker = FakeWorker(
            [
                Steps.write({"greeting.py": "def hello(): ...\n", "junk.py": "x = 1\n"}),  # fails the check
                Steps.write({"greeting.py": "def farewell(name): ...\n"}),
                Steps.write({"never.py": ""}),
            ]
        )
        team = ExecutorTeam(worker, [Toolboxes.CHECK], Samples.RAW, attempts=3)
        failed, event = Traces.drop(await team.act(w.ctx(), w.worktree))
        assert (
            isinstance(failed, AttemptFailed) and failed.reason == "checks fail"
        )  # counted against the model
        assert isinstance(event, CommitPushed) and len(worker.calls) == 2
        assert w.h.repository.files_at(w.artifact.branch) == [
            "greeting.py"
        ]  # the first attempt's junk is gone

    async def test_when_every_attempt_fails_the_last_is_committed_and_escalated(self, tmp_path: Path) -> None:
        w = Workspace(tmp_path)
        worker = FakeWorker([Steps.write({"greeting.py": "def hello(): ...\n"}) for _ in range(2)])
        failed, commit, escalated = Traces.drop(
            await ExecutorTeam(worker, [Toolboxes.CHECK], Samples.RAW, attempts=2).act(w.ctx(), w.worktree)
        )
        assert isinstance(failed, AttemptFailed)
        assert isinstance(commit, CommitPushed) and isinstance(escalated, Escalated)
        assert len(worker.calls) == 2

    async def test_incomplete_work_is_escalated(self, tmp_path: Path) -> None:
        w = Workspace(tmp_path)
        worker = FakeWorker(
            [Steps.write({"greeting.py": "def farewell(): ...\n"}, "half done", complete=False)]
        )
        events = Traces.drop(
            await ExecutorTeam(worker, [Toolboxes.CHECK], Samples.RAW).act(w.ctx(), w.worktree)
        )
        assert isinstance(events[-1], Escalated)
        assert "incomplete: half done" in events[-1].reason

    async def test_no_changes_is_told_once_then_escalated_without_a_commit(self, tmp_path: Path) -> None:
        w = Workspace(tmp_path)
        worker = FakeWorker([Steps.write({}), Steps.write({})])
        first, second, event = Traces.drop(
            await ExecutorTeam(worker, [Toolboxes.CHECK], Samples.RAW).act(w.ctx(), w.worktree)
        )
        assert isinstance(first, AttemptFailed) and first.changed_nothing
        assert isinstance(second, AttemptFailed) and isinstance(event, Escalated)
        assert "no changes" in event.reason
        told = worker.calls[1][1]
        assert isinstance(told, ExecutorRequest)
        assert told.instruction.must_fix[-1].detail == ExecutorTeam.NOTHING_CHANGED

    async def test_told_that_nothing_changed_the_model_does_the_work(self, tmp_path: Path) -> None:
        w = Workspace(tmp_path)
        worker = FakeWorker([Steps.write({}), Steps.write({"greeting.py": "def farewell(name): ...\n"})])
        failed, commit = Traces.drop(
            await ExecutorTeam(worker, [Toolboxes.CHECK], Samples.RAW).act(w.ctx(), w.worktree)
        )
        assert isinstance(failed, AttemptFailed) and isinstance(commit, CommitPushed)  # one attempt, nudged

    async def test_worker_failure_keeps_the_work_done_so_far(self, tmp_path: Path) -> None:
        w = Workspace(tmp_path)

        def half(toolbox: Toolbox | None) -> ExecutorReport:
            assert isinstance(toolbox, WorktreeToolbox)
            toolbox.write_file("greeting.py", "def farewell(): ...\n")
            raise WorkerError("ran out of model calls")

        failed, commit, escalated = Traces.drop(
            await ExecutorTeam(FakeWorker([half]), [], Samples.RAW).act(w.ctx(), w.worktree)
        )
        assert isinstance(failed, AttemptFailed)  # the work in progress was not the model's answer
        assert isinstance(commit, CommitPushed) and commit.model is None
        assert isinstance(escalated, Escalated)
        assert w.h.repository.files_at(w.artifact.branch) == ["greeting.py"]

    async def test_worker_failure_is_escalated(self, tmp_path: Path) -> None:
        w = Workspace(tmp_path)

        def fail(toolbox: Toolbox | None) -> ExecutorReport:
            raise WorkerError("model unreachable")

        failed, event = Traces.drop(
            await ExecutorTeam(FakeWorker([fail]), [], Samples.RAW).act(w.ctx(), w.worktree)
        )
        assert isinstance(failed, AttemptFailed) and isinstance(event, Escalated)
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
        (event,) = Traces.drop(await team.act(w.ctx(), w.worktree))
        assert isinstance(event, CommitPushed)
        assert "def farewell" in w.worktree.read("greeting.py")
        message = w.h.repository.git.run(w.worktree.path, "log", "-1", "--format=%s")
        assert message == "a-0001: add farewell"


class TestSyncWithTheBase:
    """Before each action the base level is merged in: what siblings merged meanwhile is there to build on."""

    @staticmethod
    def advance_base(w: Workspace, files: dict[str, str]) -> None:
        level = w.h.repository.checkout_level(Samples.EXPERIMENTAL)
        for path, content in files.items():
            level.write(path, content)
        level.commit("a sibling merged")
        level.remove()

    @staticmethod
    def conflicted(w: Workspace) -> None:
        commit = w.worktree.head()
        finding = Finding(
            artifact=w.artifact.id,
            level=Samples.RAW,
            team=Samples.TESTER,
            kind=FindingKind.CONFLICT,
            severity=Severity.BLOCKER,
            detail="Conflicts with experimental; rebase onto it and resolve",
        )
        rejected = Rejected(
            artifact=w.artifact.id, level=Samples.RAW, findings=(finding,), gate_decision=None
        )
        w.h.board.append([CommitPushed(artifact=w.artifact.id, commit=commit, model="coder"), rejected])

    async def test_a_rejection_for_conflicts_alone_is_settled_by_the_merge_without_a_model(
        self, tmp_path: Path
    ) -> None:
        w = Workspace(tmp_path)
        w.worktree.write("greeting.py", "def farewell(name): ...\n")
        w.worktree.commit("farewell")
        self.advance_base(w, {"other.py": "x = 1\n"})
        self.conflicted(w)
        team = ExecutorTeam(FakeWorker([]), [Toolboxes.CHECK], Samples.RAW, sync_from=Samples.EXPERIMENTAL)
        (event,) = Traces.drop(await team.act(w.ctx(), w.worktree))
        assert isinstance(event, CommitPushed) and event.model is None
        assert set(w.worktree.files()) == {"greeting.py", "other.py"}

    async def test_a_real_conflict_goes_to_a_person(self, tmp_path: Path) -> None:
        w = Workspace(tmp_path)
        w.worktree.write("greeting.py", "def farewell(name): ...\n")
        w.worktree.commit("farewell")
        self.advance_base(w, {"greeting.py": "def hello(): ...\n"})
        self.conflicted(w)
        team = ExecutorTeam(FakeWorker([]), [Toolboxes.CHECK], Samples.RAW, sync_from=Samples.EXPERIMENTAL)
        (event,) = Traces.drop(await team.act(w.ctx(), w.worktree))
        assert isinstance(event, Escalated) and "conflicts with experimental" in event.reason


class TestRepairsAndPartialWork:
    async def test_failing_checks_are_repaired_on_the_work_so_far(self, tmp_path: Path) -> None:
        w = Workspace(tmp_path)
        worker = FakeWorker(
            [
                Steps.write({"greeting.py": "def hello(): ...\n", "kept.py": "x = 1\n"}),  # fails the check
                Steps.write({"greeting.py": "def farewell(name): ...\n"}),
            ]
        )
        (commit,) = Traces.drop(
            await ExecutorTeam(worker, [Toolboxes.CHECK], Samples.RAW, repairs=2).act(w.ctx(), w.worktree)
        )
        assert isinstance(commit, CommitPushed)
        assert set(w.h.repository.files_at(w.artifact.branch)) == {
            "greeting.py",
            "kept.py",
        }  # nothing restarted
        told = worker.calls[1][1]
        assert isinstance(told, ExecutorRequest)
        assert told.instruction.must_fix[-1].detail.startswith(ExecutorTeam.STILL_FAILING)

    async def test_partial_work_that_passes_is_committed_for_the_gate(self, tmp_path: Path) -> None:
        w = Workspace(tmp_path)
        step = Steps.write({"greeting.py": "def farewell(): ...\n"}, "some of it", complete=False)
        (commit,) = Traces.drop(
            await ExecutorTeam(FakeWorker([step]), [Toolboxes.CHECK], Samples.RAW, partial=True).act(
                w.ctx(), w.worktree
            )
        )
        assert isinstance(commit, CommitPushed)  # no escalation: the gate judges the progress


class TestProviderFailures:
    def test_refusals_server_errors_and_lost_connections_are_the_providers(self) -> None:
        class Refused(Exception):
            def __init__(self, status_code: int) -> None:
                self.status_code = status_code

        class APIConnectionError(Exception):
            pass

        assert ProviderUnavailable.describes(Refused(402)) and ProviderUnavailable.describes(Refused(503))
        assert ProviderUnavailable.describes(APIConnectionError())
        assert not ProviderUnavailable.describes(Refused(400)) and not ProviderUnavailable.describes(
            ValueError()
        )

    async def test_a_provider_failure_pauses_the_project_instead_of_failing_the_work(
        self, tmp_path: Path
    ) -> None:
        w = Workspace(tmp_path)

        def broke(toolbox: Toolbox | None) -> ExecutorReport:
            raise ProviderUnavailable("APIStatusError: Error code: 402 - out of credits")

        (event,) = Traces.drop(
            await ExecutorTeam(FakeWorker([broke]), [], Samples.RAW).act(w.ctx(), w.worktree)
        )
        assert isinstance(event, Paused) and "402" in event.reason  # no AttemptFailed, no escalation


class TestToolTraces:
    async def test_every_attempt_records_its_tool_calls_by_size(self, tmp_path: Path) -> None:
        w = Workspace(tmp_path)
        worker = FakeWorker([Steps.write({"greeting.py": "def farewell(name): ...\n"})])
        used, commit = await ExecutorTeam(worker, [Toolboxes.CHECK], Samples.RAW).act(w.ctx(), w.worktree)
        assert isinstance(used, ToolsUsed) and isinstance(commit, CommitPushed)
        (call,) = used.calls
        assert (call.tool, call.output_chars) == ("write_file", len("wrote greeting.py (24 characters)"))
        assert call.input_chars == len("greeting.py") + len("def farewell(name): ...\n")
