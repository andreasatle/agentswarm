"""The acceptance team: an artifact's acceptance tests, written before its producer starts.

See docs/design/acceptance-checks.md.

Whoever does the work doesn't decide what counts as done (RESEARCH.md P1, P12). This team writes the tests
from the task alone, checks that they fail on the current code (a test that already passes tests nothing new),
commits them to the artifact's branch and records them in an `AcceptanceWritten` event. The executor waits for
that event and can't change the tests: plain code puts them back before every commit it makes.
"""

import hashlib
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field

from agentswarm.core.domain import (
    AcceptanceFile,
    AcceptanceWritten,
    ArtifactContext,
    ArtifactId,
    CommitPushed,
    Event,
    Level,
    Paused,
    Sized,
    TeamName,
)
from agentswarm.gating.checks import CheckStatus, CommandCheck
from agentswarm.llm.workers import ProviderUnavailable, Worker, WorkerError
from agentswarm.teams.routing import RoutedTiers
from agentswarm.teams.runner import Relevance
from agentswarm.workspace.git import Worktree


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class SourceFile(Frozen):
    path: str = Field(description="relative to the project root, e.g. greeter.py or tests/test_greeter.py")
    content: str = Field(description="the complete text of the file")


class AcceptRequest(Frozen):
    """The payload an acceptance writer receives."""

    north_star: str
    task: str
    files: tuple[SourceFile, ...]  # every file of the project as it is, before the task is done


class AcceptAnswer(Frozen):
    """The acceptance tests for the task: written before the work, by someone other than its producer."""

    test: str = Field(description="a complete pytest file that checks every requirement the task states")
    summary: str = Field(description="one line: what the tests cover")


class AcceptanceTester:
    """Writes the acceptance tests of each new leaf artifact at the lowest level, before the executor starts.

    Not for umbrellas (their children get their own) or fix artifacts (the tests they must satisfy are merged
    already). In a project that sizes tasks first, it waits until the artifact is sized as fitting.
    """

    FOLDER = "tests/acceptance"
    TRIES = 2  # an answer that tests nothing, or can't run, is thrown away and asked for again once
    TEXT_LIMIT = 100_000  # characters per file given to the model
    FAILING: ClassVar[tuple[int, ...]] = (1, 2)  # pytest: tests failed, or couldn't import what they test
    INSTRUCTIONS = """\
You write the acceptance tests for one task in a small Python project, before anyone implements it, in a
single answer. The data holds the task and every file of the project as it is now.

- Write one complete pytest file that checks every requirement the task states: one test per stated rule
  or example, with the exact expected values.
- Test only through the public interface the task describes (functions, classes, commands), importing the
  code the way the project's own tests do. The test runs from the project root.
- The tests must fail now, because the feature doesn't exist yet, and pass once it is done correctly.
- Do not test anything the task doesn't ask for, and do not implement the feature."""

    # Projects without labels (docs/design/self-supervised.md): models get hand-computed expected values wrong
    # often, the more so for the inputs most likely to find bugs, so these checks need none.
    DERIVED = """\
You write the checks for one step of a project that has a specification but no tests with known answers,
before anyone implements the step, in a single answer. The data holds the step and every file of the
project, the specification included.

- Write one complete pytest file of checks that need no expected values worked out by hand:
  - round trips: build values in the test, write them in the project's format with a small writer you
    include in the test file, read them back with the project's code, and compare with the values you built;
  - equivalences: inputs the specification says mean the same (other whitespace or comments, other quoting,
    another but equivalent layout) must give the same result;
  - rules the specification states for every input: what must be rejected, which types results have.
  Quote the specification's words in a comment above each test.
- Where the specification itself shows an input and its result, check exactly that, quoting the passage.
- Generate many inputs with random.Random and a fixed seed, so a failure can be reproduced. Use only the
  standard library and pytest. Keep the file focused: at most about 300 lines, a few strong checks per rule
  rather than many similar ones.
- Check only what this step covers, through the project's public interface, importing it the way the
  project's own tests do. The test runs from the project root.
- Every check must hold for the finished program as the specification describes it, not only for this step:
  never require that an input the specification allows is rejected, even if a later step implements it.
- Never compare with another implementation of the same thing (a library for the format, in the standard
  library or elsewhere): its answers are known answers, which this project doesn't have.
- Make sure every input your generators build is what the test claims it is (a "malformed" input must stay
  malformed whatever the random choices; a "valid" one must be valid).
- The checks must fail now, because the step isn't done yet, and pass once it is done correctly.
- Do not implement the step."""

    def __init__(
        self,
        watches: Level,
        worker: Worker,
        runner: CommandCheck,
        requires_sizing: bool = False,
        name: TeamName = TeamName("acceptance_tester"),
        routing: RoutedTiers | None = None,  # instead of `worker`: tiers, and a router to start on one
        derived: bool = False,  # checks without hand-computed answers (DERIVED), for projects without labels
    ) -> None:
        self._instructions = self.DERIVED if derived else self.INSTRUCTIONS
        self._routing = routing
        self._watches = watches
        self._worker = worker
        self._runner = runner
        self._requires_sizing = requires_sizing
        self._name = name

    @property
    def name(self) -> TeamName:
        return self._name

    @property
    def watches(self) -> Level:
        return self._watches

    @property
    def pausable(self) -> bool:
        return True

    @classmethod
    def path_for(cls, artifact: ArtifactId) -> str:
        """One file per artifact, each with its own basename.

        Pytest refuses two test modules of the same name in folders without `__init__.py`, and the merged
        acceptance tests of a split's children live side by side.
        """
        return f"{cls.FOLDER}/test_{artifact.replace('-', '_').replace('.', '_')}.py"

    def relevant(self, ctx: ArtifactContext) -> Relevance:
        """Leaves, and the umbrellas of a split: their tests come from the original task, not the planner's.

        An umbrella's tests stay on its own branch, which never merges; its last child inherits them.
        """
        history = ctx.history
        sized = [e for e in history if isinstance(e, Sized)]
        if ctx.artifact.fixes is not None:
            return Relevance.NO
        if self._requires_sizing and not sized:
            return Relevance.NO  # wait for the decomposition team
        if ctx.waits_for is not None:
            return Relevance.NO  # written against the code the earlier children of the split have merged
        if ctx.closes is not None and ctx.parent_acceptance is None:
            return Relevance.NO  # the last child inherits its parent's tests: wait for them
        started = any(isinstance(e, AcceptanceWritten | CommitPushed) for e in history)
        return Relevance.NO if started else Relevance.YES

    def request(self, ctx: ArtifactContext, worktree: Worktree) -> AcceptRequest:
        files: list[SourceFile] = []
        for path in worktree.files():
            try:
                content = worktree.read(path, self.TEXT_LIMIT)
            except (OSError, UnicodeDecodeError):
                continue  # binary or unreadable: not something a test is written against
            files.append(SourceFile(path=path, content=content))
        return AcceptRequest(north_star=ctx.north_star, task=ctx.artifact.task, files=tuple(files))

    async def act(self, ctx: ArtifactContext, worktree: Worktree) -> list[Event]:
        events = await self.write(ctx, worktree)
        parent = ctx.parent_acceptance
        if parent is None or not parent.files:
            return events
        # The last child of a split: its parent's tests join its own, so the whole meets the original task.
        for file in parent.files:
            worktree.write(file.path, worktree.show(parent.commit, file.path))
        commit = worktree.commit(
            f"{ctx.artifact.id}: acceptance tests of {parent.artifact}, whose split it closes"
        )
        written = events[-1]
        assert isinstance(written, AcceptanceWritten)
        inherited = written.model_copy(update={"commit": commit, "files": (*written.files, *parent.files)})
        return [*events[:-1], inherited]

    async def write(self, ctx: ArtifactContext, worktree: Worktree) -> list[Event]:
        """The artifact's own acceptance tests, committed; or a record that none could be written."""
        artifact = ctx.artifact.id
        path = self.path_for(artifact)
        request = self.request(ctx, worktree)
        why = ""
        start, routed = self._routing.start(ctx, worktree.head()) if self._routing is not None else (0, [])
        for number in range(self.TRIES):
            try:
                answer = await self.worker(start + number).run(self._instructions, request, AcceptAnswer)
            except ProviderUnavailable as error:
                return [*routed, Paused(reason=f"the model provider is unavailable: {error}")]
            except WorkerError as error:
                why = f"no valid answer: {error}"
                continue
            try:
                compile(answer.test, path, "exec")
            except SyntaxError as error:
                why = f"not valid Python: {error}"
                continue
            worktree.write(path, answer.test)
            # Before the work, a test usually can't even import what it tests: pytest's collection error (2)
            # counts as failing here, like a failed assertion (1); the file itself compiled above.
            result = self._runner.run_on(worktree.path, [path], failure_exit_codes=self.FAILING)
            if result.status is CheckStatus.FAILED:
                commit = worktree.commit(f"{artifact}: acceptance tests ({answer.summary})")
                digest = hashlib.sha256(answer.test.encode()).hexdigest()
                files = (AcceptanceFile(path=path, sha256=digest),)
                return [
                    *routed,
                    AcceptanceWritten(artifact=artifact, team=self._name, commit=commit, files=files),
                ]
            why = (
                "they pass before the work is done"
                if result.status is CheckStatus.PASSED
                else "they can't run"
            )
            worktree.discard_changes()
        # No usable tests: recorded as done without files, so the executor isn't blocked; the reason is kept.
        skipped = AcceptanceWritten(
            artifact=artifact, team=self._name, commit=worktree.head(), files=(), note=why
        )
        return [*routed, skipped]

    def worker(self, tier: int) -> Worker:
        """The worker for a try: from the routed tier, the next try one tier up, staying on the strongest."""
        if self._routing is None:
            return self._worker
        tiers = self._routing.tiers
        return tiers[min(tier, len(tiers) - 1)].worker
