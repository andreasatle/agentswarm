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
    Sized,
    TeamName,
)
from agentswarm.gating.checks import CheckStatus, CommandCheck
from agentswarm.llm.workers import Worker, WorkerError
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

    def __init__(
        self,
        watches: Level,
        worker: Worker,
        runner: CommandCheck,
        requires_sizing: bool = False,
        name: TeamName = TeamName("acceptance_tester"),
        routing: RoutedTiers | None = None,  # instead of `worker`: tiers, and a router to start on one
    ) -> None:
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
        return f"{cls.FOLDER}/{artifact.replace('-', '_').replace('.', '_')}/test_acceptance.py"

    def relevant(self, ctx: ArtifactContext) -> Relevance:
        history = ctx.history
        sized = [e for e in history if isinstance(e, Sized)]
        if ctx.artifact.fixes is not None or any(not s.fits for s in sized):
            return Relevance.NO
        if self._requires_sizing and not sized:
            return Relevance.NO  # wait for the decomposition team
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
        artifact = ctx.artifact.id
        path = self.path_for(artifact)
        request = self.request(ctx, worktree)
        why = ""
        start, routed = self._routing.start(ctx, worktree.head()) if self._routing is not None else (0, [])
        for number in range(self.TRIES):
            try:
                answer = await self.worker(start + number).run(self.INSTRUCTIONS, request, AcceptAnswer)
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
