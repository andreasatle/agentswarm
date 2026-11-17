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

import re
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
    SectionAssigned,
    Sized,
    TeamName,
)
from agentswarm.evaluation.citations import CitationRequest, Support
from agentswarm.evaluation.score import Measure
from agentswarm.llm.workers import Worker, WorkerError
from agentswarm.teams.analyzer import Quotes
from agentswarm.teams.campaign import Campaign, Part
from agentswarm.teams.claims import CheckedClaim, CitedStatement, ClaimChecker, WholeParts
from agentswarm.teams.referee import FindingRequest, QuotedPassage, Referee, ReportedFinding
from agentswarm.workspace.git import Repository, Worktree


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ReviewSource(Frozen):
    name: str  # the source's name, as cited: [@name]
    text: str


class ReviewRequest(Frozen):
    """The payload a reviewer receives."""

    north_star: str
    brief: str  # what the document is for, who reads it, its sources and rules
    section: str  # the section file to review
    text: str  # that section as it is
    perspective: str  # what to look for
    sources: tuple[str, ...]  # every source file's name
    rejected: tuple[str, ...] = ()  # findings the referee ruled against: again only with new evidence
    # The text a review needs, read by plain code: the source parts the section cites or was assigned, and
    # for consistency the rest of the document. With it, the reviewer answers in one call (2026-10-18: a
    # reviewer reading through tools cost $0.25-0.30 a section)
    material: tuple[ReviewSource, ...] = ()
    document: str = ""


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


class ReviewCampaign(Campaign):
    NAME = TeamName("reviewer")
    CONTEXT = 600  # characters of a file on either side of a quote, for the referee
    REJECTED_SHOWN = 20
    INSTRUCTIONS = """\
You review one section of a document from one perspective, given in the data with the document's brief. The
document is finished as far as its writer knows. Find what is wrong with this section from your perspective.

- Read the section in the data, and the source text and document given with it: everything the review
  needs is there. Evidence quotes must come from those texts, naming their file (sources/<name>.md, or the
  section's file in sections/).
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
            "The reader. Read this section as the brief's intended reader, new to the subject, with the "
            "whole document given for context. Find what gets in their way: a term used before it is "
            "explained, a "
            "missing step or a jump in the argument, a reference to something the document doesn't contain, "
            "a point the document already made, repeated without adding anything, or a citation repeated or "
            "placed so it clutters the sentence. Quote the passage from this section; for a repetition, also "
            "the earlier one."
        ),
        Perspective.CONSISTENCY: (
            "Consistency. Find places where this section and another part of the document disagree: a "
            "number, a name, a term used two ways, a claim and its contradiction. Quote both passages."
        ),
        Perspective.GENERAL: (
            "Anything. Find whatever most needs fixing in this section for the brief and its reader. Quote "
            "the passage it is about."
        ),
        Perspective.CLAIMS: (
            "Claims. A cited statement that its source doesn't support, or supports only in part (it says "
            "less, or something narrower). The statement is quoted from the section and, where one settles "
            "it, the source passage."
        ),
    }
    # Quotes the evidence must hold, at least: (from the document's sections, from the sources)
    NEEDS: ClassVar[dict[Perspective, tuple[int, int]]] = {
        Perspective.FACTS: (1, 0),
        Perspective.COVERAGE: (0, 1),
        Perspective.READER: (1, 0),
        Perspective.CONSISTENCY: (2, 0),
        Perspective.GENERAL: (1, 0),
        Perspective.CLAIMS: (1, 0),
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
        claims: ClaimChecker | None = None,  # checks each cited statement, for the `claims` perspective
        finding_referee: Referee
        | None = None,  # rules on findings that aren't cited statements; None: referee
    ) -> None:
        super().__init__(board, repository, ladder, config.quiet_rounds, budget, progress, config.max_rounds)
        self._claims = claims
        self._config = config
        self._north_star = north_star
        self._artifacts = artifacts
        self._worker = worker
        self._referee = referee
        self._finding_referee = finding_referee or referee

    @property
    def focuses(self) -> tuple[Focus, ...]:
        return self._config.perspectives

    def building(self, views: BoardViews) -> bool:
        """Every section is written first: a step waiting for a person counts too, since reviewing a document
        with sections missing finds only that they are missing (2026-10-17: reviews ran on a report of one
        section while six waited for a person)."""
        return any(
            a.level == self._ladder.lowest
            and not views.dropped(a.id)
            and not views.children(a.id)
            and views.merge_commit(a.id) is None
            for a in views.artifacts()
        )

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
            material=self.material(worktree, part, views),
            document=self.document(worktree)
            if focus in (Perspective.CONSISTENCY, Perspective.READER)
            else "",
        )

        def attacked(
            reported: int,
            tasks: Sequence[ArtifactId],
            dropped: Sequence[str],
            findings: Sequence[AttackFinding] = (),
            rejections: Sequence[RejectedReport] = (),
            failed: bool = False,
        ) -> Attacked:
            return Attacked(
                round=round,
                section=part.title,
                focus=focus,
                commit=tip,
                model=self._config.model,
                reported=reported,
                confirmed=len(findings),
                tasks=tuple(tasks),
                dropped=tuple(dropped),
                findings=tuple(findings),
                rejected=tuple(rejections),
                failed=failed,
            )

        cited: dict[str, CitationRequest] = {}  # for `claims`: each flagged line with the text it cites
        try:
            if focus is Perspective.CLAIMS:
                report, cited = await self.claim_findings(worktree, part, views)
            else:
                report = await self._worker.run(self.INSTRUCTIONS, request, ReviewFindings)
        except WorkerError as error:
            return [attacked(0, (), (f"the reviewer failed: {error}",), failed=True)]
        # A claims review takes every flagged line: each is cheap to check and to rule on, and one revision
        # fixes them all (the claims trial, 2026-11-09: three a round left 15 of 23 problems after a round)
        found = (
            report.findings if focus is Perspective.CLAIMS else report.findings[: self._config.max_findings]
        )
        upheld: list[ReviewFinding] = []
        tasks: list[ArtifactId] = []
        findings: list[AttackFinding] = []
        rejections: list[RejectedReport] = []
        dropped: list[str] = []
        events: list[Event] = []
        backed: list[tuple[int, ReviewFinding, CitationRequest | None]] = []
        reported: list[ReportedFinding] = []
        for number, finding in enumerate(found, 1):
            evidence = self.evidence(worktree, finding)
            unbacked = self.unbacked(evidence, focus)
            if unbacked is not None:
                dropped.append(f"{finding.title}: {unbacked}")
                continue
            claimed = cited.get(finding.evidence[0].quote) if finding.evidence else None
            backed.append((number, finding, claimed))
            if claimed is None:
                reported.append(
                    ReportedFinding(
                        number=number,
                        title=finding.title,
                        problem=finding.problem,
                        evidence=tuple(e for e in evidence if e is not None),
                    )
                )
        ruled = (  # the review's findings ruled together: the document is sent once, not once a finding
            await self._finding_referee.rule_findings(
                FindingRequest(
                    perspective=self.PERSPECTIVE[focus],
                    brief=request.brief,
                    document=self.document(worktree),
                    findings=tuple(reported),
                )
            )
            if reported
            else {}
        )
        for number, finding, claimed in backed:
            ruling = await self._referee.rule_claim(claimed) if claimed is not None else ruled.get(number)
            if ruling is None:
                dropped.append(f"{finding.title}: no ruling")
                continue
            confirmed, reason = ruling
            if not confirmed:
                dropped.append(f"{finding.title}: not confirmed: {reason}")
                rejections.append(
                    RejectedReport(
                        claim=f"{finding.title}: {finding.problem}",
                        reason=reason,
                        statement=finding.evidence[0].quote if focus is Perspective.CLAIMS else "",
                    )
                )
                continue
            upheld.append(finding)
        # One revision for the section's confirmed findings: the typed writer rewrites a section whole, so two
        # revisions of it at once conflict (benchmark r2: 11 of 11 conflicts were a reviewer's second finding
        # on the same section, each redone, often by the strong model)
        batches = []
        if upheld:
            fixing = (
                self.fix_claims(part.title, upheld)
                if focus is Perspective.CLAIMS
                else self.fix_findings(part.title, upheld, focus)
            )
            batches = [(fixing, upheld)]
        assigned = self.assignment(part.title, views)
        for task, fixed in batches:
            tasks.append(task)
            if assigned is not None:  # the writer revises it in one typed answer, as it wrote it
                events.append(SectionAssigned(artifact=task, path=part.title, section=assigned.section))
            findings.extend(
                AttackFinding(task=task, claim=f.problem, quotes=tuple(e.quote for e in f.evidence))
                for f in fixed
            )
            events.append(
                Sized(artifact=task, fits=True)
            )  # one section's revision: the writer tries it whole
        return [*events, attacked(len(found), tasks, dropped, findings, rejections)]

    CLAIM_CHECKER = "claim-checker"  # as the checker's spend is recorded (Project.claim_checker)

    async def claim_findings(
        self, worktree: Worktree, part: Part, views: BoardViews
    ) -> tuple[ReviewFindings, dict[str, CitationRequest]]:
        """Every cited statement of the section checked on its own; the unsupported first, then the partly. A
        statement the referee ruled against flagging is left out while its line is unchanged: raised again
        each round, it took one of the round's few findings from a real problem."""
        if self._claims is None:
            raise WorkerError("the `claims` perspective has no claim checker")
        folder = f"{self._config.sources}/"
        sources: dict[str, str] = {}
        for file in worktree.files():
            if file.startswith(folder) and file.endswith(".md"):
                try:
                    sources[file.removeprefix(folder).removesuffix(".md")] = worktree.read(file)
                except (OSError, UnicodeDecodeError, ValueError):
                    continue
        ruled = {r.statement for attack in views.attacks() for r in attack.rejected if r.statement}
        statements = [s for s in CitedStatement.all_in(part.title, part.text) if s.text not in ruled]
        checked = await self._claims.check(statements, sources)
        flagged = [c for c in checked if c.support is Support.UNSUPPORTED] + [
            c for c in checked if c.support is Support.PARTLY
        ]
        report = ReviewFindings(
            summary=f"{len(checked)} cited statements checked, {len(flagged)} not fully supported",
            findings=tuple(self.claim_finding(c) for c in flagged),
        )
        # The referee reads each flagged line against its cited part whole, whatever the checker was given
        whole = WholeParts()
        cited = {
            c.statement.text: CitationRequest(
                statement=c.statement.text, sources=await whole.cited(c.statement, sources)
            )
            for c in flagged
        }
        return report, cited

    def claim_finding(self, checked: CheckedClaim) -> ReviewFinding:
        statement = checked.statement
        evidence = [Evidence(file=statement.file, quote=statement.text)]
        if checked.source is not None and checked.source_quote:
            evidence.append(
                Evidence(file=f"{self._config.sources}/{checked.source}.md", quote=checked.source_quote)
            )
        cited = ", ".join(c.name for c in statement.citations)
        verb = "does not support" if checked.support is Support.UNSUPPORTED else "supports only part of"
        return ReviewFinding(
            title=f"{checked.support}: {statement.claim()[:80]}",
            problem=f"The cited source ({cited}) {verb} this statement: {checked.reason}",
            fix="State only what the source says, or cite the source that says it.",
            evidence=tuple(evidence),
        )

    MATERIAL_LIMIT = 200_000  # characters of source text given with a review

    @staticmethod
    def assignment(path: str, views: BoardViews) -> SectionAssigned | None:
        """The section of the outline a file was written for: a revision of it is written from the same
        sources, by the writer's typed answer (until 2026-11-14 revisions went to a tool-using agent that
        edited the Markdown freely, past the writer's clean-up)."""
        return next(
            (
                e
                for artifact in views.artifacts()
                for e in views.history(artifact.id)
                if isinstance(e, SectionAssigned) and e.path == path
            ),
            None,
        )

    def material(self, worktree: Worktree, part: Part, views: BoardViews) -> tuple[ReviewSource, ...]:
        """The parts the section cites, then those it was assigned (its writer's sources), within a limit."""
        cited = list(dict.fromkeys(re.findall(r"\[@([^\],\s]+)", part.text)))
        assigned = [
            name
            for artifact in views.artifacts()
            for e in views.history(artifact.id)
            if isinstance(e, SectionAssigned) and e.path == part.title
            for name in e.section.sources
        ]
        found: list[ReviewSource] = []
        size = 0
        for name in dict.fromkeys([*cited, *assigned]):
            try:
                text = worktree.read(f"{self._config.sources}/{name}.md")
            except (OSError, UnicodeDecodeError, ValueError):
                continue
            if size + len(text) > self.MATERIAL_LIMIT:
                break
            found.append(ReviewSource(name=name, text=text))
            size += len(text)
        return tuple(found)

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

    def fix_findings(
        self, section: str, confirmed: Sequence[ReviewFinding], perspective: Perspective
    ) -> ArtifactId:
        """One revision for every confirmed finding a reviewer made on a section."""
        listed = " ".join(
            f"({n}) {f.title}. {f.problem} Evidence: "
            + "; ".join(f'{e.file}: "{e.quote}"' for e in f.evidence)
            + f". Suggested: {f.fix}"
            for n, f in enumerate(confirmed, 1)
        )
        task = (
            f"Revise {section} for what a reviewer ({perspective}) found: {listed} Keep every factual "
            f"statement cited to its source, follow {self._config.brief} (its length included), and change "
            "only what these findings need."
        )
        return self._artifacts.create(task, self.NAME).id

    def fix_claims(self, section: str, confirmed: Sequence[ReviewFinding]) -> ArtifactId:
        """One revision for every confirmed claim of a section: the writer reads the section and its sources
        once, not once a claim (the claims trial: $0.02 a fix, mostly rereading)."""
        listed = " ".join(
            f'({n}) {f.problem} The line: "{f.evidence[0].quote}"'
            + (f' The source says: "{f.evidence[1].quote}"' if len(f.evidence) > 1 else "")
            for n, f in enumerate(confirmed, 1)
        )
        # Fixes cut rather than hedge: the claims trial (2026-11-10) stalled when a revision that added hedges
        # took the guide 52 words over its cap, the writer told to change nothing else
        task = (
            f"Revise {section}: {len(confirmed)} cited statements its sources don't fully support (checked "
            f"claim by claim, each confirmed by a referee). {listed} For each, cut what the cited source "
            "doesn't say, or cite the source that says it; don't add hedges or explanations, since a longer "
            "document can break its length cap. Keep every factual statement cited to its source, follow "
            f"{self._config.brief}, and change only these statements, except to shorten others in the "
            "section when the length cap requires it."
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
