"""Hardening: red teams attack the merged product until they find nothing new (docs/design/hardening.md).

An outer loop around the building. When no work is left at the lowest level, the campaign attacks the product
on the base level, one spec section and one focus at a time. A red team writes and runs tests in a scratch
folder; a report needs a test that fails on the product and the spec passage it rests on, quoted word for
word. Plain code checks both; the referee, of another model family, rules whether the spec backs the test.
A confirmed report becomes a fix task at the lowest level whose acceptance test is the red team's test, so
the set of protected checks grows: that is the hardening. Rounds repeat over every section and focus until a
whole round confirms nothing new. No red team is switched off for finding little: rare finds are the point of
diverse attackers (the user, 2026-10-15).
"""

import hashlib
import random
import re
from collections.abc import Sequence
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field

from agentswarm.board.artifacts import ArtifactFactory
from agentswarm.board.blackboard import Blackboard, BoardViews
from agentswarm.control.budget import Budget
from agentswarm.control.escalations import Escalations
from agentswarm.core.config import HardeningConfig, LevelLadder
from agentswarm.core.domain import (
    AcceptanceFile,
    AcceptanceWritten,
    Artifact,
    ArtifactId,
    Attacked,
    AttackFinding,
    AttackFocus,
    Event,
    Focus,
    Reconsidered,
    RejectedReport,
    Sized,
    TeamName,
)
from agentswarm.evaluation.mutation import ChangedLines, MutantGenerator
from agentswarm.evaluation.score import Measure
from agentswarm.gating.checks import CheckStatus, CommandCheck, RatchetCheck
from agentswarm.gating.ratchet import Progress as TestProgress
from agentswarm.llm.workers import Worker, WorkerError
from agentswarm.teams.acceptance import AcceptanceTester
from agentswarm.teams.analyzer import AnalysisToolbox
from agentswarm.teams.campaign import Campaign, Part
from agentswarm.teams.referee import Referee
from agentswarm.workspace.git import Repository, Worktree


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class SpecSection(Part):
    """A section of a spec: what one attack is about."""


class SpecSections:
    """A spec split into sections: a line starting with the heading opens one. A section longer than `size`
    is cut at its next deeper heading into parts of about `size`, so each attack has a focused piece."""

    @classmethod
    def split(cls, text: str, heading: str, size: int | None = None) -> list[SpecSection]:
        sections = cls.at(text, heading)
        if size is None:
            return sections
        found: list[SpecSection] = []
        for section in sections:
            if len(section.text) <= size:
                found.append(section)
                continue
            first, *rest = section.text.split("\n", 1)
            parts = cls.at("\n".join(rest), "#" + heading)
            groups: list[list[SpecSection]] = [[]]
            for part in parts:
                if groups[-1] and sum(len(p.text) for p in groups[-1]) + len(part.text) > size:
                    groups.append([])
                groups[-1].append(part)
            if len(groups) <= 1:
                found.append(section)
                continue
            for n, group in enumerate(groups, 1):
                body = "\n\n".join(p.text for p in group)
                found.append(
                    SpecSection(title=f"{section.title} ({n}/{len(groups)})", text=f"{first}\n\n{body}")
                )
        return found

    @staticmethod
    def at(text: str, heading: str) -> list[SpecSection]:
        found: list[SpecSection] = []
        title: str | None = None
        lines: list[str] = []
        fenced = False  # inside a code block a line may start like a heading: a YAML or shell comment
        for line in text.splitlines():
            if line.lstrip().startswith(("```", "~~~")):
                fenced = not fenced
            if line.startswith(heading) and not fenced:
                if title is not None:
                    found.append(SpecSection(title=title, text="\n".join(lines).strip()))
                title, lines = line[len(heading) :].strip(), [line]
            elif title is not None:
                lines.append(line)
        if title is not None:
            found.append(SpecSection(title=title, text="\n".join(lines).strip()))
        return found


class AttackRequest(Frozen):
    """The payload a red team receives."""

    north_star: str
    section: str  # the spec section to attack, its title
    passage: str  # that section's text
    focus: str  # what to attack in it
    leads: tuple[str, ...] = ()  # planted bugs no check noticed: behaviours nothing tests yet
    withdrawn: tuple[str, ...] = ()  # reports confirmed once and withdrawn since: not problems, don't repeat
    rejected: tuple[str, ...] = ()  # reports the referee ruled against: again only with new evidence


class AttackProblem(Frozen):
    title: str = Field(description="one line")
    claim: str = Field(description="what the product gets wrong, for which input, and what the spec requires")
    test: str = Field(description="the name of the run_test file that fails because of this problem")
    quotes: tuple[str, ...] = Field(
        min_length=1,
        max_length=3,
        description="the spec's exact words the test rests on, copied from the spec: one passage, or up to "
        "three when the problem combines rules from several places",
    )


class AttackReport(Frozen):
    """What the red team found: only problems backed by a failing test and the spec's own words."""

    summary: str = Field(description="one line: what was attacked")
    problems: tuple[AttackProblem, ...]


class AttackToolbox(AnalysisToolbox):
    """Reading the product and running scratch tests; there is no single change to show."""

    TOOLS: ClassVar[tuple[str, ...]] = ("list_files", "read_file", "run_test")


class Leads:
    """Mutation-guided leads (MuTAP): plant small bugs in the product; each one no check notices is a
    behaviour nothing tests yet. Plain code, in the attack's scratch checkout, restored after each mutant."""

    SHOWN = 8  # leads given to the red team

    def __init__(self, check: CommandCheck, count: int) -> None:
        self._check = check
        self._count = count
        self._generator = MutantGenerator()

    def find(self, worktree: Worktree, seed: str) -> tuple[str, ...]:
        mutants = [
            mutant
            for path in sorted(worktree.files())
            if path.endswith(".py") and not ChangedLines.is_test(path) and not path.startswith("_")
            for mutant in self._generator.mutants(
                path, worktree.read(path), set(range(1, worktree.read(path).count("\n") + 2))
            )
        ]
        random.Random(seed).shuffle(mutants)
        leads: list[str] = []
        for mutant in mutants[: self._count]:
            original = worktree.read(mutant.path)
            worktree.write(mutant.path, mutant.source)
            try:
                result = self._check.run_on(worktree.path, [])
            finally:
                worktree.write(mutant.path, original)
            if result.status is CheckStatus.PASSED:  # survived: no check noticed the planted bug
                line = original.splitlines()[mutant.line - 1].strip() if mutant.line else ""
                leads.append(f"{mutant.path}:{mutant.line}: {mutant.description} in `{line}`")
            if len(leads) >= self.SHOWN:
                break
        return tuple(leads)


class Hardener(Campaign):
    """The hardening campaign: red teams attack the product by spec section and focus; plain code checks
    their reports and makes fix tasks."""

    NAME = TeamName("red_team")
    TEXT_LIMIT = 30_000  # characters of a spec section given to the red team
    REJECTED_SHOWN = 20  # rejected reports shown to a red team, the latest
    INSTRUCTIONS = """\
You are a red team. The product in the project is finished as far as its builders know; its specification is
in the project too. Find where the product breaks the specification, within the section and focus in the data.

- Read the section in the data, then the product's code (list_files, read_file) to see how it handles it.
- Write pytest files with run_test that check what the specification requires, through the product's public
  interface. A test must fail on the product to count, and must assert only what the specification says.
- Report a problem only with a test you ran that fails, and the specification's exact words it rests on,
  copied from the section. A rule the product already meets is not a problem. If nothing breaks, report no
  problems: that is a good result.
- Report the most important problems first. Don't report style, naming, or anything the specification doesn't
  require.
- Reports listed as withdrawn in the data were found wrong after all (the project's existing tests show what
  the specification means there): don't report them again.
- Reports listed as rejected in the data were ruled not backed by the specification, for the reason given.
  Report one again only with new evidence that answers that reason: another rule quoted, or a test that
  avoids the objection."""
    FOCUS: ClassVar[dict[AttackFocus, str]] = {
        AttackFocus.SECTION: (
            "Every rule and stated behaviour of this section, one test each, including the ones no test of "
            "the project covers yet."
        ),
        AttackFocus.BOUNDARIES: (
            "Edge values the section names or implies: empty input, zero, limits and the values just inside "
            "and just outside them, the largest and smallest allowed."
        ),
        AttackFocus.REJECTION: (
            "Inputs this section says are invalid or must be rejected: check that each is rejected the way "
            "the specification says, and that nothing invalid is accepted."
        ),
        AttackFocus.COMBINATION: (
            "Valid inputs that combine this section's constructs with each other and with constructs from "
            "other parts of the specification (read them in the project as needed): nested, in every style "
            "the specification allows, with every property or modifier that may attach. Write many such "
            "inputs, from simple to intricate. Make sure from the specification's rules that each is valid; "
            "each must then be accepted, and give the result the specification defines where it is clear. "
            "Quote the rule that makes the failing input valid."
        ),
        AttackFocus.MUTATION: (
            "The leads in the data: bugs planted in the product that no test noticed, so the behaviour at "
            "each place is untested. For each, find what this section requires there, and test it."
        ),
    }

    def __init__(
        self,
        config: HardeningConfig,
        north_star: str,
        board: Blackboard,
        repository: Repository,
        ladder: LevelLadder,
        artifacts: ArtifactFactory,
        worker: Worker,
        referee: Referee,
        check: CommandCheck,
        budget: Budget | None = None,
        progress: Measure | None = None,
        review: RatchetCheck | None = None,
    ) -> None:
        super().__init__(board, repository, ladder, config.quiet_rounds, budget, progress)
        self._config = config
        self._north_star = north_star
        self._artifacts = artifacts
        self._worker = worker
        self._referee = referee
        self._check = check
        self._leads = Leads(check, config.mutants)
        # The test suite, test by test, for looking again at a report whose fix every builder failed; None:
        # confirmed reports are never reconsidered
        self._review = review

    @property
    def focuses(self) -> tuple[Focus, ...]:
        return self._config.focuses

    async def prepare(self, views: BoardViews) -> int:
        """Before the planner splits it: a fix no builder could make is looked at again."""
        for finding in self.unreviewed(views):
            reconsidered = await self.reconsider(views.artifact(finding.task), finding)
            self._board.append([reconsidered])
            if reconsidered.withdrawn:
                Escalations(self._board, None).drop(finding.task, f"withdrawn: {reconsidered.reason}")
            return 1
        return 0

    def unreviewed(self, views: BoardViews) -> list[AttackFinding]:
        """Confirmed reports whose fix every builder model failed, not yet looked at again."""
        if self._review is None:
            return []
        return [
            finding
            for attack in views.attacks()
            for finding in attack.findings
            if views.active(finding.task)
            and Escalations.awaiting_split(views.history(finding.task))
            and not any(isinstance(e, Reconsidered) for e in views.history(finding.task))
        ]

    async def reconsider(self, task: Artifact, finding: AttackFinding) -> Reconsidered:
        """Plain code first: on the task's branch, the last attempt's change must make the red team's test
        pass and break protected tests the product passed; only then does the referee look again."""
        assert self._review is not None

        def settled(
            withdrawn: bool, reason: str, broken: Sequence[str] = (), referee: str | None = None
        ) -> Reconsidered:
            return Reconsidered(
                artifact=task.id,
                claim=finding.claim,
                broken=tuple(broken),
                referee=referee,
                withdrawn=withdrawn,
                reason=reason,
            )

        own = AcceptanceTester.path_for(task.id)
        worktree = self._repository.checkout_artifact(task)
        try:
            base = worktree.merge_base(self._ladder.base)
            before = self._review.outcomes_at(worktree.path, base)
            now = self._review.outcomes(worktree.path, base)
            if before is None or now is None:
                return settled(False, "the tests didn't run; the report stands")
            mine = [t for t in now.passed | now.failed if t.startswith(own)]
            if not mine or any(t in now.failed for t in mine):
                return settled(False, "no change made the red team's test pass; the report stands")
            progress = TestProgress(base=before, now=now, protected=self._review.protected)
            broken = sorted(t for t in progress.broken if not t.startswith(own))
            if not broken:
                return settled(False, "the change broke no protected test; the report stands")
            try:
                test_file = worktree.read(own)
            except (OSError, UnicodeDecodeError, ValueError):
                return settled(False, "the red team's test can't be read; the report stands", broken)
            ruling = await self._referee.reconsider(
                worktree, finding.claim, test_file, finding.quotes, broken
            )
        finally:
            worktree.remove()
        if ruling is None:
            return settled(False, "no ruling; the report stands", broken)
        withdrawn, reason = ruling
        return settled(withdrawn, reason, broken, self._referee.model)

    @staticmethod
    def withdrawn(views: BoardViews) -> tuple[str, ...]:
        """Reports withdrawn so far, for the red teams: their claims and why."""
        return tuple(
            f"{e.claim} (withdrawn: {e.reason})"
            for attack in views.attacks()
            for finding in attack.findings
            for e in views.history(finding.task)
            if isinstance(e, Reconsidered) and e.withdrawn
        )

    @classmethod
    def rejected(cls, views: BoardViews) -> tuple[str, ...]:
        """The reports the referee ruled against, latest last, for the red teams: their claims and why
        (2026-10-16: two rejected claims came back 30 times from different YAML chapters)."""
        found = [f"{r.claim} (rejected: {r.reason})" for attack in views.attacks() for r in attack.rejected]
        return tuple(dict.fromkeys(found))[-cls.REJECTED_SHOWN :]

    def parts(self, worktree: Worktree) -> list[Part]:
        return list(self.sections(worktree))

    def sections(self, worktree: Worktree) -> list[SpecSection]:
        try:
            text = worktree.read(self._config.spec)
        except (OSError, UnicodeDecodeError, ValueError):
            return []
        found = SpecSections.split(text, self._config.heading, self._config.section_size)
        if not self._config.sections:
            return found
        return [
            s for t in self._config.sections for s in found if s.title == t or s.title.startswith(f"{t} (")
        ]

    async def attack(
        self, worktree: Worktree, tip: str, round: int, part: Part, focus: Focus, views: BoardViews
    ) -> list[Event]:
        assert isinstance(focus, AttackFocus)  # the hardener's focuses (HardeningConfig)
        section = part
        leads = (
            self._leads.find(worktree, f"{round}:{section.title}") if focus is AttackFocus.MUTATION else ()
        )
        request = AttackRequest(
            north_star=self._north_star,
            section=section.title,
            passage=section.text[: self.TEXT_LIMIT],
            focus=self.FOCUS[focus],
            leads=leads,
            withdrawn=self.withdrawn(views),
            rejected=self.rejected(views),
        )
        toolbox = AttackToolbox(worktree, "", self._check)

        def attacked(
            reported: int,
            confirmed: int,
            tasks: Sequence[ArtifactId],
            dropped: Sequence[str],
            findings: Sequence[AttackFinding] = (),
            rejections: Sequence[RejectedReport] = (),
            failed: bool = False,
        ) -> Attacked:
            return Attacked(
                round=round,
                section=section.title,
                focus=focus,
                commit=tip,
                model=self._config.model,
                reported=reported,
                confirmed=confirmed,
                tasks=tuple(tasks),
                dropped=tuple(dropped),
                findings=tuple(findings),
                rejected=tuple(rejections),
                failed=failed,
            )

        if focus is AttackFocus.MUTATION and not leads:
            return [attacked(0, 0, (), ("no lead: every planted bug was noticed by a check",))]
        try:
            report = await self._worker.run(self.INSTRUCTIONS, request, AttackReport, toolbox)
        except WorkerError as error:
            return [attacked(0, 0, (), (f"the red team failed: {error}",), failed=True)]
        problems = report.problems[: self._config.max_problems]
        tasks: list[ArtifactId] = []
        findings: list[AttackFinding] = []
        rejections: list[RejectedReport] = []
        events: list[Event] = []
        dropped: list[str] = []
        for problem in problems:
            if not toolbox.failed(problem.test):
                dropped.append(f"{problem.title}: its test passed, or never ran")
                continue
            source = toolbox.test_source(problem.test)
            ruling = await self._referee.confirm(worktree, problem.claim, source, problem.quotes)
            if ruling is None:
                dropped.append(f"{problem.title}: no ruling")
                continue
            confirmed, reason = ruling
            if not confirmed:
                dropped.append(f"{problem.title}: not confirmed: {reason}")
                if self._referee.passages(worktree, problem.quotes) is not None:  # not a quote failure
                    rejections.append(RejectedReport(claim=problem.claim, reason=reason))  # the referee's
                continue
            task, written = self.fix_task(problem, source)
            tasks.append(task)
            findings.append(AttackFinding(task=task, claim=problem.claim, quotes=problem.quotes))
            # One failing test fits one branch: the builders try it first, and it is split only if every model
            # fails it (ADaPT). Sized up front, YAML's fixes were split into 8 steps for 1 failing test, each
            # step with checks of its own: $3.30 of a $5 round, 1 fix merged (2026-10-16).
            events += [written, Sized(artifact=task, fits=True)]
        return [*events, attacked(len(problems), len(tasks), tasks, dropped, findings, rejections)]

    def fix_task(self, problem: AttackProblem, source: str) -> tuple[ArtifactId, AcceptanceWritten]:
        """A task at the lowest level whose acceptance test is the red team's failing test, protected."""
        quoted = " ".join(f'"{quote}"' for quote in problem.quotes)
        task = (
            f"Fix what a red team found against the specification: {problem.title}. {problem.claim} "
            f"The specification says: {quoted}. The red team's test, which fails now, is the acceptance "
            "test of this task; make it pass without breaking any other test, the way the specification "
            "states it."
        )
        artifact = self._artifacts.create(task, self.NAME)
        path = AcceptanceTester.path_for(artifact.id)
        worktree = self._repository.checkout_artifact(artifact)
        try:
            worktree.write(path, source)
            commit = worktree.commit(f"{artifact.id}: the red team's failing test ({problem.title})")
        finally:
            worktree.remove()
        digest = hashlib.sha256(source.encode()).hexdigest()
        written = AcceptanceWritten(
            artifact=artifact.id,
            team=self.NAME,
            commit=commit,
            files=(AcceptanceFile(path=path, sha256=digest),),
        )
        return artifact.id, written

    @staticmethod
    def slug(title: str) -> str:
        return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
