"""Reviewing a document: reviewers attack the finished document, section by section and perspective by
perspective, until they find nothing new (docs/design/documents.md).

The document's counterpart of hardening. A reviewer looks at one section from one perspective (facts against
their sources, coverage of the brief, the newcomer reader, consistency, or one broad review as the baseline)
and reports findings, each backed by quotes from the document or its sources. Plain code checks every quote
is in its file word for word and that the evidence fits the perspective (a coverage finding quotes a source,
a consistency finding two passages); the referee, of another model family, rules on the rest. A confirmed
finding becomes a writer's task to revise that section. Whether it was fixed shows in the next round: the
same reviewer meets the section again, and the referee's rejections are shown so a claim isn't argued twice.
"""

from collections.abc import Sequence
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field

from agentswarm.board.artifacts import ArtifactFactory
from agentswarm.board.blackboard import Blackboard, BoardViews
from agentswarm.control.budget import Budget
from agentswarm.core.config import LevelLadder, ReviewConfig
from agentswarm.core.domain import (
    ArtifactId,
    Attacked,
    AttackFinding,
    Event,
    Focus,
    Perspective,
    RejectedReport,
    Sized,
    TeamName,
)
from agentswarm.evaluation.score import Measure
from agentswarm.llm.workers import Worker, WorkerError
from agentswarm.teams.analyzer import Quotes, ReadingToolbox
from agentswarm.teams.campaign import Campaign, Part
from agentswarm.teams.referee import FindingRequest, QuotedPassage, Referee
from agentswarm.workspace.git import Repository, Worktree


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ReviewRequest(Frozen):
    """The payload a reviewer receives."""

    north_star: str
    brief: str  # what the document is for, who reads it, its sources and rules
    section: str  # the section file to review
    text: str  # that section as it is
    perspective: str  # what to look for
    sources: tuple[str, ...]  # the source files, to read as needed
    rejected: tuple[str, ...] = ()  # findings the referee ruled against: again only with new evidence


class Evidence(Frozen):
    file: str = Field(description="the file the quote is from, relative to the project root")
    quote: str = Field(description="the exact words, copied from that file (a phrase or a sentence or two)")


class ReviewFinding(Frozen):
    title: str = Field(description="one line")
    problem: str = Field(description="what is wrong, and why it matters for the brief and its reader")
    fix: str = Field(description="what the writer should change, briefly")
    evidence: tuple[Evidence, ...] = Field(
        min_length=1, max_length=3, description="quotes that show the problem, as the perspective asks"
    )


class ReviewFindings(Frozen):
    """What the reviewer found: only problems its quotes show."""

    summary: str = Field(description="one line: what was reviewed")
    findings: tuple[ReviewFinding, ...]


class ReviewToolbox(ReadingToolbox):
    """Reading the document and its sources; there is no single change to show."""

    TOOLS: ClassVar[tuple[str, ...]] = ("list_files", "read_file")

    def __init__(self, worktree: Worktree) -> None:
        super().__init__(worktree, "")


class ReviewCampaign(Campaign):
    NAME = TeamName("reviewer")
    CONTEXT = 600  # characters of a file on either side of a quote, for the referee
    REJECTED_SHOWN = 20
    INSTRUCTIONS = """\
You review one section of a document from one perspective, given in the data with the document's brief. The
document is finished as far as its writer knows. Find what is wrong with this section from your perspective.

- Read the section in the data; read the sources and other sections (list_files, read_file) as you need.
- Report a problem only with evidence: the exact words from the files that show it, copied verbatim, with
  the file each is from. What the perspective asks you to quote is in the data.
- Report the most important problems first, and only real ones: a problem must matter for the brief and its
  reader. If the section is fine from your perspective, report no findings: that is a good result.
- Suggest a fix that keeps within the brief (its length cap included); don't ask for more text where less
  would do.
- Findings listed as rejected in the data were ruled not shown by their evidence, for the reason given.
  Report one again only with new evidence that answers that reason."""
    PERSPECTIVE: ClassVar[dict[Perspective, str]] = {
        Perspective.FACTS: (
            "Facts. Find statements in this section that the source they cite doesn't support: wrong "
            "numbers, names or results, claims the paper doesn't make, or a factual statement citing no "
            "source. "
            "Quote the statement from the section and, when it cites one, the source passage it misstates "
            "(or the passage that says otherwise)."
        ),
        Perspective.COVERAGE: (
            "Coverage. Find what the brief asks for that the sources hold and this section should cover "
            "but doesn't: a paper's aim, method, data, evaluation or findings left out or too vague to use. "
            "Quote the source passage with the missing content."
        ),
        Perspective.READER: (
            "The reader. Read as the brief's intended reader, new to the subject. Find passages they "
            "couldn't follow: undefined terms, missing steps, an order that confuses. Quote the passage."
        ),
        Perspective.CONSISTENCY: (
            "Consistency. Find places where this section and another part of the document disagree: a "
            "number, a name, a term used two ways, a claim and its contradiction. Quote both passages."
        ),
        Perspective.GENERAL: (
            "Anything. Find whatever most needs fixing in this section for the brief and its reader. Quote "
            "the passage it is about."
        ),
    }
    # Quotes the evidence must hold, at least: (from the document's sections, from the sources)
    NEEDS: ClassVar[dict[Perspective, tuple[int, int]]] = {
        Perspective.FACTS: (1, 0),
        Perspective.COVERAGE: (0, 1),
        Perspective.READER: (1, 0),
        Perspective.CONSISTENCY: (2, 0),
        Perspective.GENERAL: (1, 0),
    }

    def __init__(
        self,
        config: ReviewConfig,
        north_star: str,
        board: Blackboard,
        repository: Repository,
        ladder: LevelLadder,
        artifacts: ArtifactFactory,
        worker: Worker,
        referee: Referee,
        budget: Budget | None = None,
        progress: Measure | None = None,
    ) -> None:
        super().__init__(board, repository, ladder, config.quiet_rounds, budget, progress)
        self._config = config
        self._north_star = north_star
        self._artifacts = artifacts
        self._worker = worker
        self._referee = referee

    @property
    def focuses(self) -> tuple[Focus, ...]:
        return self._config.perspectives

    def parts(self, worktree: Worktree) -> list[Part]:
        """The document's section files, in reading order."""
        folder = f"{self._config.sections}/"
        files = sorted(
            f for f in worktree.files() if f.startswith(folder) and f.endswith(".md") and "/." not in f
        )
        return [Part(title=f, text=worktree.read(f)) for f in files]

    async def attack(
        self, worktree: Worktree, tip: str, round: int, part: Part, focus: Focus, views: BoardViews
    ) -> list[Event]:
        assert isinstance(focus, Perspective)  # the reviewer's focuses (ReviewConfig)
        sources = tuple(f for f in worktree.files() if f.startswith(f"{self._config.sources}/"))
        request = ReviewRequest(
            north_star=self._north_star,
            brief=self.brief(worktree),
            section=part.title,
            text=part.text,
            perspective=self.PERSPECTIVE[focus],
            sources=sources,
            rejected=self.rejected(views),
        )

        def attacked(
            reported: int,
            tasks: Sequence[ArtifactId],
            dropped: Sequence[str],
            findings: Sequence[AttackFinding] = (),
            rejections: Sequence[RejectedReport] = (),
        ) -> Attacked:
            return Attacked(
                round=round,
                section=part.title,
                focus=focus,
                commit=tip,
                model=self._config.model,
                reported=reported,
                confirmed=len(tasks),
                tasks=tuple(tasks),
                dropped=tuple(dropped),
                findings=tuple(findings),
                rejected=tuple(rejections),
            )

        try:
            report = await self._worker.run(
                self.INSTRUCTIONS, request, ReviewFindings, ReviewToolbox(worktree)
            )
        except WorkerError as error:
            return [attacked(0, (), (f"the reviewer failed: {error}",))]
        found = report.findings[: self._config.max_findings]
        tasks: list[ArtifactId] = []
        findings: list[AttackFinding] = []
        rejections: list[RejectedReport] = []
        dropped: list[str] = []
        events: list[Event] = []
        for finding in found:
            evidence = self.evidence(worktree, finding)
            unbacked = self.unbacked(evidence, focus)
            if unbacked is not None:
                dropped.append(f"{finding.title}: {unbacked}")
                continue
            ruling = await self._referee.rule_finding(
                FindingRequest(
                    perspective=self.PERSPECTIVE[focus],
                    brief=request.brief,
                    document=self.document(worktree),
                    title=finding.title,
                    problem=finding.problem,
                    evidence=tuple(e for e in evidence if e is not None),
                )
            )
            if ruling is None:
                dropped.append(f"{finding.title}: no ruling")
                continue
            confirmed, reason = ruling
            if not confirmed:
                dropped.append(f"{finding.title}: not confirmed: {reason}")
                rejections.append(RejectedReport(claim=f"{finding.title}: {finding.problem}", reason=reason))
                continue
            task = self.fix_task(part.title, focus, finding)
            tasks.append(task)
            findings.append(
                AttackFinding(
                    task=task, claim=finding.problem, quotes=tuple(e.quote for e in finding.evidence)
                )
            )
            events.append(
                Sized(artifact=task, fits=True)
            )  # one section's revision: the writer tries it whole
        return [*events, attacked(len(found), tasks, dropped, findings, rejections)]

    def document(self, worktree: Worktree) -> str:
        """Every section, in reading order, each under its file name."""
        return "\n\n".join(f"<{p.title}>\n{p.text}" for p in self.parts(worktree))

    def brief(self, worktree: Worktree) -> str:
        try:
            return worktree.read(self._config.brief)
        except (OSError, UnicodeDecodeError, ValueError):
            return ""

    def evidence(self, worktree: Worktree, finding: ReviewFinding) -> list[QuotedPassage | None]:
        """Each quote with the text around it, or None where it isn't in its file word for word (or the file
        is neither the document, its sources nor its brief, or the quote is too short to mean anything)."""
        allowed = (f"{self._config.sections}/", f"{self._config.sources}/")
        passages: list[QuotedPassage | None] = []
        for item in finding.evidence:
            if not (item.file.startswith(allowed) or item.file == self._config.brief):
                passages.append(None)
                continue
            if len(Quotes.normal(item.quote)) < Quotes.MIN_LENGTH:
                passages.append(None)
                continue
            try:
                text = worktree.read(item.file)
            except (OSError, UnicodeDecodeError, ValueError):
                passages.append(None)
                continue
            context = Referee.around(text, item.quote, self.CONTEXT)
            passages.append(
                QuotedPassage(file=item.file, quote=item.quote, context=context)
                if context is not None
                else None
            )
        return passages

    def unbacked(self, evidence: Sequence[QuotedPassage | None], perspective: Perspective) -> str | None:
        """Why plain code drops a finding before any ruling; None when its evidence is in order."""
        if any(e is None for e in evidence):
            return "a quote is not in the file it names, word for word"
        quoted = [e for e in evidence if e is not None]
        document = sum(e.file.startswith(f"{self._config.sections}/") for e in quoted)
        sources = sum(e.file.startswith(f"{self._config.sources}/") for e in quoted)
        needs_document, needs_sources = self.NEEDS[perspective]
        if document < needs_document or sources < needs_sources:
            return (
                f"a {perspective} finding quotes at least {needs_document} passage(s) of the document and "
                f"{needs_sources} of the sources"
            )
        return None

    def fix_task(self, section: str, perspective: Perspective, finding: ReviewFinding) -> ArtifactId:
        quoted = "; ".join(f'{e.file}: "{e.quote}"' for e in finding.evidence)
        task = (
            f"Revise {section} for what a reviewer ({perspective}) found: {finding.title}. {finding.problem} "
            f"Evidence: {quoted}. Suggested: {finding.fix} Keep every factual statement cited to its "
            f"source, follow {self._config.brief} (its length cap included), and change only what this "
            "finding needs."
        )
        return self._artifacts.create(task, self.NAME).id

    @classmethod
    def rejected(cls, views: BoardViews) -> tuple[str, ...]:
        found = [
            f"{r.claim} (rejected: {r.reason})"
            for attack in views.attacks()
            if isinstance(attack.focus, Perspective)
            for r in attack.rejected
        ]
        return tuple(dict.fromkeys(found))[-cls.REJECTED_SHOWN :]
