"""The stateless builder: plain code runs the loop, the model gives one typed answer per round (ROADMAP.md).

A tool-using agent keeps its memory in its conversation, which is resent on every step and grows with every
file it reads (a CommonMark item used up to 190,000-token prompts). Here the control is outside the model,
as in Agentless (Xia et al. 2024) and Anthropic's advice to prefer workflows to agents: each round, plain
code gathers the state (the task, the findings, the latest checks, what the model asked for last round, how
its edits went, and the model's own notes) and asks one question; the answer lists edits, and reads,
searches and test runs for the next round. The code applies the edits and carries out the requests with the
same tools the agent uses (`WorktreeToolbox`), so prompts stay the size of the state, every step is a typed
record, and a loop can't run away. The model's memory between rounds is only its `notes`, which it writes
itself.
"""

from collections.abc import Sequence
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field

from agentswarm.core.domain import Instruction, Level
from agentswarm.gating.checks import HardCheck
from agentswarm.llm.workers import Worker
from agentswarm.teams.executor import ExecutorReport, ExecutorRequest, ExecutorTeam, WorktreeToolbox
from agentswarm.teams.routing import Tier


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Edit(Frozen):
    path: str = Field(description="relative to the project root")
    old: str = Field(
        description="the exact text to replace, which must occur once in the file (include a few lines "
        "around it); empty to create the file, or replace all of it, with `new`"
    )
    new: str = Field(description="the text that replaces it")


class FileRead(Frozen):
    path: str
    start: int = Field(default=1, description="first line, 1-based")
    end: int | None = Field(default=None, description="last line, inclusive; none for up to 300 lines")


class BuildStep(Frozen):
    """One round's answer: edits to make now, and what to look at in the next round."""

    notes: str = Field(
        description="what you have learned and plan to do next; it is all you will remember next round"
    )
    edits: tuple[Edit, ...] = Field(default=(), description="made now, in order, before the requests below")
    reads: tuple[FileRead, ...] = Field(default=(), description="parts of files to see next round")
    searches: tuple[str, ...] = Field(default=(), description="regular expressions to find in the files")
    run_tests: tuple[str, ...] = Field(
        default=(), description="test files or folders to run, with failures shown"
    )
    done: bool = Field(default=False, description="stop: the task is done, or you can do no more")
    complete: bool = Field(default=False, description="with done: whether every part of the task is done")
    summary: str = Field(default="", description="with done: one line, what was changed")


class Outcome(Frozen):
    """What one request or edit from the last round gave."""

    request: str  # e.g. "read mdconv/__init__.py 1-300", "edit mdconv/__init__.py"
    result: str


class BuildState(Frozen):
    """Everything the model sees in one round: it remembers nothing else."""

    north_star: str
    instruction: Instruction
    files: tuple[str, ...]
    round: int
    rounds_left: int
    notes: str  # the model's own notes from the last round
    outcomes: tuple[Outcome, ...]  # the last round's edits and requests, and what they gave
    checks: str  # the project's checks on the current state of the work


class StatelessBuilder(ExecutorTeam):
    """An executor whose loop is plain code: one typed model answer per round, at most `rounds` rounds."""

    RESULT_LIMIT: ClassVar[int] = 12_000  # characters of one request's result
    NOTES_LIMIT: ClassVar[int] = 4_000
    STUCK: ClassVar[int] = 3  # rounds in a row without an edit or a new request end the work
    STEP_INSTRUCTIONS = """\
You build one task in a project, in rounds. Each round you see the current state: the task, the findings to
fix, the project's files, what your edits and requests of the last round gave, the latest check results, and
your own notes. You remember nothing else between rounds, so write in `notes` what you need next round.

- Each answer may edit files, and ask to read parts of files, search for a regular expression, or run
  test files. Edits are made now; the results of your requests come in the next round.
- Edit by replacing exact text that occurs once (include a few surrounding lines); an empty `old` creates
  the file or replaces all of it. Change only what the task needs.
- Fix every finding in `instruction.must_fix`, and follow every rule in `instruction.constraints`.
- Tests under tests/acceptance/ were written by someone else: make them pass. You can't change them.
- When the checks pass, or you can do no more, set `done`, say whether the task is `complete`, and give a
  one-line `summary`."""

    async def work(
        self, worker: Worker, request: ExecutorRequest, toolbox: WorktreeToolbox
    ) -> ExecutorReport:
        """Rounds of one typed answer each, until the model is done, stuck or out of rounds."""
        rounds = self.rounds
        notes = ""
        outcomes: tuple[Outcome, ...] = ()
        checks = toolbox.check_report().summary()
        stuck = 0
        summary = "ran out of rounds"
        for number in range(1, rounds + 1):
            state = BuildState(
                north_star=request.north_star,
                instruction=request.instruction,
                files=tuple(toolbox.list_files().splitlines()),
                round=number,
                rounds_left=rounds - number,
                notes=notes,
                outcomes=outcomes,
                checks=checks,
            )
            toolbox.seen.clear()  # nothing carries over between rounds, so asking again is not a loop
            step = await worker.run(self.STEP_INSTRUCTIONS, state, BuildStep)
            notes = step.notes[: self.NOTES_LIMIT]
            edited = [self.apply(toolbox, edit) for edit in step.edits]
            if any(not o.result.startswith("error:") for o in edited):
                checks = toolbox.check_report().summary()
            outcomes = (*edited, *self.carry_out(toolbox, step))
            if step.done:
                return ExecutorReport(summary=step.summary or summary, complete=step.complete)
            stuck = stuck + 1 if not step.edits and len(outcomes) == 0 else 0
            if stuck >= self.STUCK:
                summary = "stopped: no edits or requests for several rounds"
                break
        return ExecutorReport(summary=summary, complete=False)

    def __init__(
        self,
        workers: Worker | Sequence[Tier],
        checks: Sequence[HardCheck],
        watches: Level,
        constraints: Sequence[str] = (),
        *,
        rounds: int = 20,  # model answers per attempt at most
        **options: Any,  # everything else an ExecutorTeam takes, by name
    ) -> None:
        super().__init__(workers, checks, watches, constraints, **options)
        self._rounds = rounds

    @property
    def rounds(self) -> int:
        return self._rounds

    @staticmethod
    def apply(toolbox: WorktreeToolbox, edit: Edit) -> Outcome:
        if edit.old:
            result = toolbox.edit_file(edit.path, edit.old, edit.new)
        else:
            result = toolbox.write_file(edit.path, edit.new)
        return Outcome(request=f"edit {edit.path}", result=result)

    @classmethod
    def carry_out(cls, toolbox: WorktreeToolbox, step: BuildStep) -> list[Outcome]:
        outcomes: list[Outcome] = []
        for read in step.reads:
            span = f"{read.start}-{read.end}" if read.end is not None else f"from {read.start}"
            text = toolbox.read_file(read.path, read.start, read.end)
            outcomes.append(Outcome(request=f"read {read.path} {span}", result=text))
        outcomes += [Outcome(request=f"search {p}", result=toolbox.search(p)) for p in step.searches]
        outcomes += [Outcome(request=f"run_tests {p}", result=toolbox.run_tests(p)) for p in step.run_tests]
        return [o.model_copy(update={"result": cls.bounded(o.result)}) for o in outcomes]

    @classmethod
    def bounded(cls, text: str) -> str:
        if len(text) <= cls.RESULT_LIMIT:
            return text
        return text[: cls.RESULT_LIMIT] + f"\n... ({len(text) - cls.RESULT_LIMIT} more characters not shown)"
