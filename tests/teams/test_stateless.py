from pathlib import Path

from pydantic import BaseModel

from agentswarm.core.domain import CommitPushed, Escalated, ToolsUsed
from agentswarm.llm.workers import FakeWorker, Step, Toolbox
from agentswarm.teams.stateless import BuildState, BuildStep, Edit, FileRead, StatelessBuilder
from tests.support.samples import Samples
from tests.teams.test_executor import Toolboxes, Workspace


class Rounds:
    @staticmethod
    def answer(step: BuildStep) -> Step[BaseModel]:
        def scripted(toolbox: Toolbox | None) -> BaseModel:
            assert toolbox is None  # no tools: one typed answer per call
            return step

        return scripted


class TestStatelessBuilder:
    async def test_plain_code_applies_edits_and_carries_requests_and_notes_to_the_next_round(
        self, tmp_path: Path
    ) -> None:
        w = Workspace(tmp_path)
        first = BuildStep(
            notes="greeting.py needs farewell",
            edits=(Edit(path="greeting.py", old="", new="def farewell(name):\n    return name\n"),),
            reads=(FileRead(path="greeting.py", start=1, end=1),),
        )
        second = BuildStep(notes="done", done=True, complete=True, summary="add farewell")
        worker = FakeWorker([Rounds.answer(first), Rounds.answer(second)])
        team = StatelessBuilder(worker, [Toolboxes.CHECK], Samples.RAW, rounds=5)
        events = await team.act(w.ctx(), w.worktree)
        assert any(isinstance(e, CommitPushed) for e in events)
        assert not any(isinstance(e, Escalated) for e in events)
        state = worker.calls[1][1]
        assert (
            isinstance(state, BuildState) and state.round == 2 and state.notes == "greeting.py needs farewell"
        )
        assert [o.request for o in state.outcomes] == ["edit greeting.py", "read greeting.py 1-1"]
        assert state.outcomes[1].result == "    1| def farewell(name):"
        assert "has_farewell: passed" in state.checks  # the checks were run after the edit
        (used,) = [e for e in events if isinstance(e, ToolsUsed)]
        assert [c.tool for c in used.calls] == ["list_files", "write_file", "read_file", "list_files"]

    async def test_a_model_that_neither_edits_nor_asks_is_stopped(self, tmp_path: Path) -> None:
        w = Workspace(tmp_path)
        idle = BuildStep(notes="thinking")
        worker = FakeWorker([Rounds.answer(idle)] * 10)
        team = StatelessBuilder(worker, [Toolboxes.CHECK], Samples.RAW, rounds=10)
        await team.act(w.ctx(), w.worktree)
        assert len(worker.calls) == 2 * StatelessBuilder.STUCK  # stopped, told nothing changed, stopped again
