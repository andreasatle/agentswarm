"""Executor teams: they implement an artifact's task on its raw branch (proposal §10.3)."""

import functools
import re
from abc import ABC, abstractmethod
from collections.abc import Callable, Sequence
from typing import ClassVar

from pydantic import BaseModel, ConfigDict

from agentswarm.core.domain import (
    AcceptanceRestored,
    AcceptanceWritten,
    ArtifactContext,
    AttemptFailed,
    CommitPushed,
    Escalated,
    EscalationResolved,
    Event,
    Finding,
    FindingKind,
    Instruction,
    Level,
    Paused,
    Promoted,
    Rejected,
    Resolution,
    Severity,
    Sized,
    TeamName,
    ToolCall,
    ToolsUsed,
)
from agentswarm.gating.checks import CheckResult, CommandCheck, HardCheck
from agentswarm.llm.workers import ProviderUnavailable, Worker, WorkerError
from agentswarm.teams.acceptance import AcceptanceTester
from agentswarm.teams.routing import RoutedTiers, Router, Tier
from agentswarm.teams.runner import Relevance
from agentswarm.workspace.files import UnsafePath
from agentswarm.workspace.git import Worktree
from agentswarm.workspace.repomap import RepoMap


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class CheckReport(Frozen):
    results: tuple[CheckResult, ...]

    @property
    def passed(self) -> bool:
        return all(r.passed for r in self.results)

    def summary(self) -> str:
        if not self.results:
            return "no checks configured"
        return "\n\n".join(
            f"{r.name}: {'passed' if r.passed else r.status.upper()}" + ("" if r.passed else f"\n{r.output}")
            for r in self.results
        )


class Traced:
    """Records each call of a toolbox's tools, by size, and stops a model that repeats itself.

    A read-only call (listing, reading, searching, running tests) repeated with the same arguments while no
    file has changed returns the same answer, so it isn't run again: the model is told so. After
    `REPEATS` such calls it is told to finish. Small models loop like this until their step limit (Phase 8:
    one run listed the files 80 times in a row).
    """

    REPEATS: ClassVar[int] = 3
    REPEATED = (
        "You already called {tool} with these arguments, and no file has changed since: the answer is the "
        "same. Do something else: change a file with edit_file or write_file, or give your final answer."
    )
    FINISH = "You keep repeating calls that change nothing. Stop now and give your final answer."

    @staticmethod
    def tool(method: Callable[..., str]) -> Callable[..., str]:
        """A tool that changes nothing: repeats are refused until a file changes."""

        @functools.wraps(method)
        def traced(self: "WorktreeToolbox", *args: object, **kwargs: object) -> str:
            key = (method.__name__, repr(args), repr(sorted(kwargs.items())))
            if key in self.seen:
                self.repeats += 1
                result = (
                    Traced.FINISH if self.repeats >= Traced.REPEATS else Traced.REPEATED.format(tool=key[0])
                )
            else:
                self.seen.add(key)
                result = method(self, *args, **kwargs)
            given = sum(len(str(a)) for a in (*args, *kwargs.values()))
            self.calls.append(ToolCall(tool=method.__name__, input_chars=given, output_chars=len(result)))
            return result + self.left()

        return traced

    @staticmethod
    def change(method: Callable[..., str]) -> Callable[..., str]:
        """A tool that changes files: always run, and afterwards every read-only call may be made again."""

        @functools.wraps(method)
        def traced(self: "WorktreeToolbox", *args: object, **kwargs: object) -> str:
            result = method(self, *args, **kwargs)
            if not result.startswith("error:"):
                self.seen.clear()
            given = sum(len(str(a)) for a in (*args, *kwargs.values()))
            self.calls.append(ToolCall(tool=method.__name__, input_chars=given, output_chars=len(result)))
            return result + self.left()

        return traced


class WorktreeToolbox:
    """The executor's tools, confined to one worktree. Checks run in the project's sandbox.

    Each public method is a tool: its signature and docstring are what the model sees. Errors are
    returned as text so the model can correct itself, never raised into the agent loop. Designed after
    SWE-agent's agent-computer interface: files are read in parts and edited by replacement, so neither a
    reply nor the conversation carries a whole file again for every change, and outputs stay short.
    """

    READ_LINES = 300  # lines returned by one read_file without an explicit range
    SEARCH_HITS = 50
    TEST_FLAGS: ClassVar[tuple[str, ...]] = ("--tb=short", "--maxfail=5", "-q")  # pytest: the first failures
    TOOLS: ClassVar[tuple[str, ...]] = (
        "list_files",
        "read_file",
        "search",
        "edit_file",
        "write_file",
        "delete_file",
        "run_checks",
    )

    def __init__(
        self,
        worktree: Worktree,
        checks: Sequence[HardCheck],
        tests: CommandCheck | None = None,  # runs one test file with full output: the `run_tests` tool
        budget: int | None = None,  # the model's calls per run: each result says about how many are left
    ) -> None:
        self._worktree = worktree
        self._checks = tuple(checks)
        self._tests = tests
        self._budget = budget
        self.calls: list[ToolCall] = []  # every tool call so far, for the board (ToolsUsed)
        self.seen: set[tuple[str, str, str]] = set()  # read-only calls made since the last change
        self.repeats = 0

    FINISH_AT: ClassVar[int] = 5  # with this few calls left, the model is told to make its edits and finish

    def left(self) -> str:
        """About how many calls the model has left; a nudge to act when it is nearly out (agents that knew it
        acted sooner, instead of reading until the limit)."""
        if self._budget is None:
            return ""
        left = max(self._budget - len(self.calls), 0)
        if left <= self.FINISH_AT:
            return f"\n[about {left} calls left: make your edits now, then give your final answer]"
        return f"\n[about {left} of {self._budget} calls left]"

    def tools(self) -> Sequence[Callable[..., str]]:
        names = (*self.TOOLS, "run_tests") if self._tests is not None else self.TOOLS
        return [getattr(self, name) for name in names]

    @Traced.tool
    def list_files(self) -> str:
        """List every file in the project, one relative path per line."""
        return "\n".join(self._worktree.files()) or "(the project has no files yet)"

    @Traced.tool
    def read_file(self, path: str, start: int = 1, end: int | None = None) -> str:
        """Return lines `start` to `end` (1-based, inclusive) of the file at `path`, relative to the project
        root, numbered ("  12| ", not part of the file). Without `end`, at most 300 lines."""
        try:
            lines = self._worktree.read(path).splitlines()
        except (OSError, UnicodeDecodeError, UnsafePath) as error:
            return f"error: {error}"
        first = max(start, 1)
        last = min(end if end is not None else first + self.READ_LINES - 1, len(lines))
        shown = [f"{n:>5}| {lines[n - 1]}" for n in range(first, last + 1)]
        more = f"\n... lines {last + 1}-{len(lines)} not shown" if end is None and last < len(lines) else ""
        return ("\n".join(shown) or "(no lines in that range)") + more

    @Traced.tool
    def search(self, pattern: str, path: str = ".") -> str:
        """Find lines matching the regular expression `pattern` in the files under `path`: file:line: text."""
        try:
            regex = re.compile(pattern)
            root = self._worktree.relative(path) if path not in (".", "") else ""
        except (re.error, UnsafePath) as error:
            return f"error: {error}"
        hits: list[str] = []
        for name in self._worktree.files():
            if root and not (name == root or name.startswith(f"{root}/")):
                continue
            try:
                text = self._worktree.read(name)
            except (OSError, UnicodeDecodeError):
                continue
            hits += [
                f"{name}:{n}: {line}" for n, line in enumerate(text.splitlines(), 1) if regex.search(line)
            ]
            if len(hits) > self.SEARCH_HITS:
                return "\n".join(hits[: self.SEARCH_HITS]) + f"\n... more than {self.SEARCH_HITS} matches"
        return "\n".join(hits) or "no matches"

    @Traced.change
    def edit_file(self, path: str, old: str, new: str) -> str:
        """Replace the text `old` with `new` in the file at `path`. `old` must occur exactly once: include a
        few surrounding lines to make it unique. Prefer this to write_file for changing an existing file."""
        try:
            relative = self._worktree.relative(path)
            content = self._worktree.read(relative)
        except (OSError, UnicodeDecodeError, UnsafePath) as error:
            return f"error: {error}"
        count = content.count(old) if old else 0
        if count != 1:
            return (
                f"error: the text to replace occurs {count} times in {relative}; it must occur exactly once"
            )
        self._worktree.write(relative, content.replace(old, new, 1))
        return f"edited {relative}"

    @Traced.change
    def write_file(self, path: str, content: str) -> str:
        """Create or overwrite the file at `path`, relative to the project root, with `content`."""
        try:
            relative = self._worktree.relative(path)
            self._worktree.write(relative, content)
        except (OSError, UnsafePath) as error:
            return f"error: {error}"
        return f"wrote {relative} ({len(content)} characters)"

    @Traced.change
    def delete_file(self, path: str) -> str:
        """Delete the file at `path`, relative to the project root."""
        try:
            relative = self._worktree.relative(path)
            self._worktree.delete(relative)
        except (OSError, UnsafePath) as error:
            return f"error: {error}"
        return f"deleted {relative}"

    @Traced.tool
    def run_checks(self) -> str:
        """Run the project's checks (such as the test suite) and report which passed and why others failed."""
        return self.check_report().summary()

    @Traced.tool
    def run_tests(self, path: str) -> str:
        """Run the tests in one file or folder, relative to the project root, and show why the first fail."""
        if self._tests is None:
            return "error: no test runner"
        try:
            relative = self._worktree.relative(path)
        except UnsafePath as error:
            return f"error: {error}"
        flags = self.TEST_FLAGS if "pytest" in self._tests.command else ()
        result = self._tests.run_on(self._worktree.path, [*flags, relative])
        return f"{result.status}:\n{result.output}"

    def check_report(self) -> CheckReport:
        return CheckReport(results=tuple(check.run(self._worktree.path) for check in self._checks))


class ExecutorRequest(Frozen):
    """The payload an executor's model receives."""

    north_star: str
    instruction: Instruction
    files: tuple[str, ...]
    diff_summary: str  # what the branch already changes relative to the level it forked from
    repo_map: str = ""  # each file's size, and its functions and classes with their lines: where to look


class ExecutorReport(Frozen):
    """What the model returns when it has finished working."""

    summary: str  # one line: what was changed; used as the commit message
    complete: bool  # whether the model believes every part of the task is done


class Executor(ABC):
    """Shared by all executors: acts when an artifact has no commit yet, or its latest commit was rejected.

    In a project with a decomposition team (`requires_sizing`), it also waits until the artifact has been
    sized as fitting one branch; fix artifacts are small by construction and need no sizing.
    """

    def __init__(
        self, watches: Level, name: TeamName, requires_sizing: bool = False, requires_acceptance: bool = False
    ) -> None:
        self._watches = watches
        self._name = name
        self._requires_sizing = requires_sizing
        self._requires_acceptance = requires_acceptance

    @property
    def name(self) -> TeamName:
        return self._name

    @property
    def watches(self) -> Level:
        return self._watches

    @property
    def pausable(self) -> bool:
        return True

    def relevant(self, ctx: ArtifactContext) -> Relevance:
        sized = [e for e in ctx.history if isinstance(e, Sized)]
        if any(not s.fits for s in sized):
            return Relevance.NO  # an umbrella: its children do the work
        if ctx.waits_for is not None:
            return Relevance.NO  # the children of a split go in order, each on what the earlier ones merged
        if self._requires_sizing and ctx.artifact.fixes is None and not sized:
            return Relevance.NO  # wait for the decomposition team
        accepted = any(isinstance(e, AcceptanceWritten) for e in ctx.history)
        if self._requires_acceptance and ctx.artifact.fixes is None and not accepted:
            return Relevance.NO  # wait for the acceptance team
        milestones = [
            e
            for e in ctx.history
            if isinstance(e, CommitPushed | Rejected | Promoted | Escalated | EscalationResolved)
        ]
        if not milestones or isinstance(milestones[-1], Rejected):
            return Relevance.YES
        last = milestones[-1]
        if isinstance(last, EscalationResolved) and last.resolution is Resolution.RETRY:
            return Relevance.YES
        return Relevance.NO

    @abstractmethod
    async def act(self, ctx: ArtifactContext, worktree: Worktree) -> list[Event]: ...


class FakeExecutor(Executor):
    """An executor without a model: writes the task (and the findings it was asked to fix) to a file."""

    def __init__(
        self, watches: Level, name: TeamName = TeamName("executor"), requires_sizing: bool = False
    ) -> None:
        super().__init__(watches, name, requires_sizing)

    async def act(self, ctx: ArtifactContext, worktree: Worktree) -> list[Event]:
        artifact = ctx.artifact
        lines = [f"# {artifact.task}", ""] + [f"- fixed: {f.detail}" for f in ctx.open_findings]
        worktree.write(f"{artifact.id}.md", "\n".join(lines) + "\n")
        sha = worktree.commit(f"{artifact.id}: {artifact.task}")
        return [CommitPushed(artifact=artifact.id, commit=sha)]


class ExecutorTeam(Executor):
    """An executor backed by a model that edits the worktree with tools and runs the checks itself.

    After the model finishes, the executor runs the checks once more, deterministically. Work that passes
    is committed and announced for the gate. Work that still fails, or that the model calls incomplete,
    is committed but escalated, so it is kept for a human and the gate doesn't judge it.
    """

    INSTRUCTIONS = """\
You are the executor in a software team. Implement the task in `instruction.goal` in the project.

- Use list_files, search and read_file (a range of lines) to understand the project before changing it.
- Change existing files with edit_file, replacing just the lines that change; write_file is for new files.
- Make the smallest change that fully accomplishes the task. Do not change unrelated code.
- Fix every finding in `instruction.must_fix`; they explain why the previous attempt was rejected.
- Follow every rule in `instruction.constraints`.
- Add or update tests for the behaviour you change.
- Run run_checks after your changes and keep fixing until every check passes.
- Tests under tests/acceptance/ were written by someone else to check your work: make them pass. You
  can't change them; any change to them is undone before your work is committed.
- When you are done, give your final answer in the required structured format: a one-line summary
  of the change, and whether the task is complete. Do not finish with a plain-text reply."""

    def __init__(
        self,
        workers: Worker | Sequence[Tier],  # one worker, or tiers from cheap to strong (§13.3)
        checks: Sequence[HardCheck],
        watches: Level,
        constraints: Sequence[str] = (),
        name: TeamName = TeamName("executor"),
        requires_sizing: bool = False,
        router: Router | None = None,  # chooses the starting tier by the task's difficulty (Phase 8)
        attempts: int = 1,  # independent attempts per action; the first that passes its checks is kept
        climb: bool = False,  # each failed attempt moves one tier up instead of retrying the same model
        requires_acceptance: bool = False,  # wait for the acceptance team's tests before the first attempt
        sync_from: Level | None = None,  # the level merged into the branch before each action (the base)
        tests: CommandCheck | None = None,  # the model may run single test files with it (`run_tests`)
        repairs: int = 0,  # when the checks fail, the work is kept and the model is told what failed
        partial: bool = False,  # passing work is committed even if the model calls it incomplete
        step_budget: int
        | None = None,  # the model's calls per run (max_model_calls), counted down in results
    ) -> None:
        self._step_budget = step_budget
        self._tests = tests
        self._repairs = repairs
        self._partial = partial
        self._sync_from = sync_from
        if attempts < 1:
            raise ValueError("an executor needs at least one attempt")
        self._attempts = attempts
        self._climb = climb
        super().__init__(watches, name, requires_sizing, requires_acceptance)
        self._tiers: tuple[Tier, ...] | None = tuple(workers) if isinstance(workers, Sequence) else None
        self._single = workers if not isinstance(workers, Sequence) else None
        if self._tiers is not None and not self._tiers:
            raise ValueError("an executor needs at least one model tier")
        self._routing = RoutedTiers(name, self._tiers, router) if self._tiers is not None else None
        self._checks = tuple(checks)
        self._constraints = tuple(constraints)

    async def act(self, ctx: ArtifactContext, worktree: Worktree) -> list[Event]:
        # The route is chosen before the first attempt (a Routed event, when routing is on); later actions
        # on the artifact start from it and step up a tier per rejection.
        start, routed = self._routing.start(ctx, worktree.head()) if self._routing is not None else (0, [])
        synced = self.sync(ctx, worktree)
        if synced is not None:
            return [*routed, synced]
        climbing = self._climb and self._tiers is not None
        failed = self.failed_models(ctx) if climbing else set[str]()
        model, worker = self.next_tier(ctx, failed) if failed else self.tier_for(ctx, start)
        events: list[Event] = list(routed)
        number, nudged = 1, False
        while number <= self._attempts:
            last = number == self._attempts
            toolbox = WorktreeToolbox(worktree, self._checks, self._tests, self._step_budget)
            kept = await self._attempt(ctx, worktree, model, worker, last, nudged, toolbox)
            if toolbox.calls:  # where the model's tokens went, for the board
                used = ToolsUsed(
                    artifact=ctx.artifact.id, team=self.name, model=model, calls=tuple(toolbox.calls)
                )
                events.append(used)
            if not isinstance(kept, AttemptFailed):
                events.extend(kept)
                break
            events.append(kept)  # thrown away: a failure of this model, for the outcome recorder
            if kept.changed_nothing and not nudged:
                nudged = True  # the same attempt once more, told that nothing changed
            else:
                number += 1
                if climbing and model is not None:  # the cascade: the next model not yet failed
                    failed.add(model)
                    model, worker = self.next_tier(ctx, failed, (model, worker))
                    nudged = False
        return events

    def sync(self, ctx: ArtifactContext, worktree: Worktree) -> Event | None:
        """Merge the base level into the branch, so the work starts from what has been merged since it forked.

        Plain code does what a conflict finding asks: a rejection for conflicts alone is settled by the merge,
        and the merged branch goes back to the gate without a model. A merge that conflicts is escalated:
        the builder has no tool to resolve it. None: carry on with the model.
        """
        if self._sync_from is None:
            return None
        before = worktree.head()
        if not worktree.merge(self._sync_from, f"{ctx.artifact.id}: merge {self._sync_from}"):
            return Escalated(
                artifact=ctx.artifact.id,
                reason=f"conflicts with {self._sync_from}, which a person must resolve",
            )
        merged = worktree.head() != before
        conflicts = [f for f in ctx.open_findings if f.kind is FindingKind.CONFLICT]
        if merged and conflicts and len(conflicts) == len(ctx.open_findings):
            return CommitPushed(artifact=ctx.artifact.id, commit=worktree.head())
        return None

    async def _attempt(
        self,
        ctx: ArtifactContext,
        worktree: Worktree,
        model: str | None,
        worker: Worker,
        last: bool,
        nudged: bool = False,  # the model said it was done without changing anything, and is told so
        toolbox: "WorktreeToolbox | None" = None,  # the attempt's tools; one is made when not given
    ) -> list[Event] | AttemptFailed:
        """One attempt's events, or why an attempt before the last failed and was thrown away."""
        artifact = ctx.artifact
        toolbox = toolbox or WorktreeToolbox(worktree, self._checks, self._tests, self._step_budget)
        request = self.request(ctx, worktree, nudged)
        try:
            report = await self.work(worker, request, toolbox)
        except ProviderUnavailable as error:
            # Not the model's failure and not the task's: new work waits until a person resumes the project.
            worktree.discard_changes()
            return [Paused(reason=f"the model provider is unavailable ({model or 'model'}): {error}")]
        except WorkerError as error:
            if not last:
                worktree.discard_changes()
                return self._failed(ctx, model, f"executor failed: {error}")
            worktree.restore(AcceptanceTester.FOLDER)
            failed = self._failed(ctx, model, f"executor failed: {error}")
            return [failed, *self._escalate(ctx, worktree, failed.reason)]
        restored = worktree.restore(AcceptanceTester.FOLDER)  # the producer can't change its acceptance tests
        protection: list[Event] = (
            [AcceptanceRestored(artifact=artifact.id, paths=tuple(restored))] if restored else []
        )
        if not worktree.has_changes():
            failed = self._failed(
                ctx, model, f"executor made no changes: {report.summary}", changed_nothing=True
            )
            if not nudged or not last:
                return failed
            return [*protection, failed, Escalated(artifact=artifact.id, reason=failed.reason)]
        checks = toolbox.check_report()
        for _ in range(self._repairs if not checks.passed else 0):
            # Repair, don't restart: the work so far stays, and the model is told exactly what failed.
            try:
                report = await self.work(worker, self.request(ctx, worktree, failing=checks), toolbox)
            except WorkerError:
                break
            restored = [*restored, *worktree.restore(AcceptanceTester.FOLDER)]
            checks = toolbox.check_report()
            if checks.passed:
                break
        protection = [AcceptanceRestored(artifact=artifact.id, paths=tuple(restored))] if restored else []
        complete = report.complete or (self._partial and checks.passed)
        if not (checks.passed and complete) and not last:
            worktree.discard_changes()
            why = "checks fail" if not checks.passed else f"reports incomplete: {report.summary}"
            return self._failed(ctx, model, why)
        sha = worktree.commit(f"{artifact.id}: {report.summary}")
        events: list[Event] = [*protection, CommitPushed(artifact=artifact.id, commit=sha, model=model)]
        if not checks.passed:
            events.append(Escalated(artifact=artifact.id, reason=f"checks still fail:\n{checks.summary()}"))
        elif not complete:
            events.append(
                Escalated(artifact=artifact.id, reason=f"executor reports incomplete: {report.summary}")
            )
        return events

    async def work(
        self, worker: Worker, request: ExecutorRequest, toolbox: WorktreeToolbox
    ) -> ExecutorReport:
        """The model's part of an attempt: here an agent that uses the tools itself, in a loop.

        May raise WorkerError. Subclasses can do it differently (`stateless.StatelessBuilder`).
        """
        return await worker.run(self.INSTRUCTIONS, request, ExecutorReport, toolbox)

    def _failed(
        self, ctx: ArtifactContext, model: str | None, reason: str, changed_nothing: bool = False
    ) -> AttemptFailed:
        return AttemptFailed(
            artifact=ctx.artifact.id,
            team=self.name,
            model=model,
            reason=reason,
            changed_nothing=changed_nothing,
        )

    def tier_index(self, ctx: ArtifactContext, start: int = 0) -> int:
        """An attempt's tier: from the starting tier, one up per rejection, staying on the strongest."""
        if self._tiers is None:
            return 0
        rejections = sum(isinstance(e, Rejected) for e in ctx.history)
        return min(start + rejections, len(self._tiers) - 1)

    def failed_models(self, ctx: ArtifactContext) -> set[str]:
        """The models that have failed this step: a discarded attempt, or a commit the gate rejected. A
        reopened step's cascade goes on from there (CommonMark, 2026-10-09: DeepSeek spent $2.53 on an
        inline parser it had failed twice before)."""
        failed: set[str] = set()
        committed: str | None = None
        for event in ctx.history:
            match event:
                case AttemptFailed(team=team, model=str() as model) if team == self.name:
                    failed.add(model)
                case CommitPushed(model=str() as model):
                    committed = model
                case Rejected() if committed is not None and not all(
                    f.kind is FindingKind.CONFLICT for f in event.findings
                ):
                    failed.add(committed)
                case _:
                    pass
        return failed

    def next_tier(
        self, ctx: ArtifactContext, failed: set[str], fallback: tuple[str | None, Worker] | None = None
    ) -> tuple[str | None, Worker]:
        """The cascade's next model: the router's best among those that haven't failed this step. When all
        have, the fallback (the model just tried) or the strongest."""
        assert self._tiers is not None and self._routing is not None
        index = self._routing.next(ctx, failed)
        if index is None:
            if fallback is not None:
                return fallback
            index = len(self._tiers) - 1
        return self._tiers[index].model, self._tiers[index].worker

    def tier_for(self, ctx: ArtifactContext, start: int = 0) -> tuple[str | None, Worker]:
        """From the starting tier, one tier up per rejection so far, staying on the strongest."""
        if self._tiers is None:
            assert self._single is not None
            return None, self._single
        tier = self._tiers[self.tier_index(ctx, start)]
        return tier.model, tier.worker

    @staticmethod
    def _escalate(ctx: ArtifactContext, worktree: Worktree, reason: str) -> list[Event]:
        """Escalate, first committing any work in progress so a human can see it."""
        escalated = Escalated(artifact=ctx.artifact.id, reason=reason)
        if not worktree.has_changes():
            return [escalated]
        sha = worktree.commit(f"{ctx.artifact.id}: work in progress (escalated)")
        return [CommitPushed(artifact=ctx.artifact.id, commit=sha), escalated]

    STILL_FAILING = (
        "Your changes so far are kept in the project, but the checks still fail. Fix what they report, "
        "without starting over; tests that passed before must pass again."
    )
    NOTHING_CHANGED = (
        "Your last answer said the task was done, but no file in the project had changed. Make the changes "
        "with write_file, run run_checks, and answer only when they are made."
    )

    def request(
        self,
        ctx: ArtifactContext,
        worktree: Worktree,
        nudged: bool = False,
        failing: CheckReport | None = None,  # a repair: the checks the work so far still fails
    ) -> ExecutorRequest:
        must_fix = ctx.open_findings
        if failing is not None:
            still = Finding(
                artifact=ctx.artifact.id,
                level=self.watches,
                team=self.name,
                kind=FindingKind.TEST_FAILURE,
                severity=Severity.MAJOR,
                detail=f"{self.STILL_FAILING}\n{failing.summary()}",
            )
            must_fix = (*must_fix, still)
        if nudged:
            nothing = Finding(
                artifact=ctx.artifact.id,
                level=self.watches,
                team=self.name,
                kind=FindingKind.INCOMPLETE,
                severity=Severity.MAJOR,
                detail=self.NOTHING_CHANGED,
            )
            must_fix = (*must_fix, nothing)
        return ExecutorRequest(
            north_star=ctx.north_star,
            instruction=Instruction(goal=ctx.artifact.task, must_fix=must_fix, constraints=self._constraints),
            files=tuple(worktree.files()),
            diff_summary=ctx.diff_summary,
            repo_map=RepoMap(worktree).render(),
        )
