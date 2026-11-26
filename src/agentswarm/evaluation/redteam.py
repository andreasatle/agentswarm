"""The document's red team (docs/design/outer-loop.md, step 1): an agent of another model family attacks a
finished document as a whole, where the reviewers each look at one section from one perspective, and reports
its flaws. Red teaming in the user's sense (2026-11-22): an agent attacking the product to find its flaws.

Like every red team (goal 12) it must show each flaw, not argue it: every finding quotes the document word for
word, and plain code drops a finding whose quote isn't there. Measured before it is trusted: on finished runs
with flaws found by hand, how many it finds and how many it invents.
"""

import re
from enum import StrEnum
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field

from agentswarm.evaluation.citations import CitationAudit, Support
from agentswarm.llm.workers import Worker


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class FlawKind(StrEnum):
    REVISE = "revise"  # the writers can fix it in this run's text
    FRAMEWORK = "framework"  # it comes from how the document is made, and recurs: an issue for the code
    NOTE = "note"  # for the user; no fix needed


class DocumentAttack(Frozen):
    """The payload the red team receives: what the document was asked to be, and the document."""

    today: str  # the date (YYYY-MM-DD): a model that doesn't know it calls this year's sources "future"
    reader: str  # who it is for and what they know
    brief: str  # what it was asked to do and cover
    words: int | None  # its length cap; None: as the brief says
    sources: tuple[str, ...]  # the sources it was written from, one line each
    document: str  # the whole document, its references included


class Finding(Frozen):
    """One flaw, shown by a quote from the document."""

    where: str = Field(description="the section's title, or 'References'")
    evidence: str = Field(
        description="the flaw's evidence copied word for word from the document: a sentence, a table row "
        "or a reference line, short enough to be found by search"
    )
    flaw: str = Field(description="what is wrong, in one sentence")
    fix: str = Field(description="how to repair it, in one sentence")
    kind: FlawKind = Field(
        description="revise (the writers can fix it in the text), framework (it comes from how the document "
        "was assembled, such as the reference list or the layout, and would recur in every document), or note"
    )
    fact: bool = Field(
        default=False,
        description="true when the flaw is that the quoted statement is false or isn't what its source says; "
        "such a finding is checked against the source the statement cites before anyone sees it",
    )


class Findings(Frozen):
    """Your report: the flaws you can show. An empty list is a valid answer."""

    findings: list[Finding]


class Refuted(Frozen):
    """A finding that a statement is false, which the statement's own source contradicts."""

    finding: Finding
    reason: str  # the referee's reading of the source, quoting it


class RedTeamReport(Frozen):
    shown: tuple[Finding, ...]  # quoted from the document
    unshown: tuple[Finding, ...]  # their quote isn't in the document: dropped, kept here to be counted
    refuted: tuple[Refuted, ...] = ()  # the cited source supports the statement: dropped, kept to be counted

    def render(self) -> str:
        lines = [
            f"{len(self.shown)} flaws shown, {len(self.unshown)} dropped (their quote isn't in the document)"
            + (f", {len(self.refuted)} refuted by the cited source" if self.refuted else "")
        ]
        for n, f in enumerate(self.shown, 1):
            lines += [
                f"{n}. [{f.kind}] {f.where}: {f.flaw}",
                f"   evidence: {f.evidence}",
                f"   fix: {f.fix}",
            ]
        return "\n".join(lines)


class DocumentRedTeam:
    INSTRUCTIONS = """\
You are the red team for a document that other models wrote from the sources listed, for the reader
described. Attack it as a whole: find the flaws that would let that reader down or embarrass the document,
especially those no single section shows. Look at its structure (a reference to a part of the document that
doesn't exist or is never introduced; something promised and not delivered; a table or list that is malformed
or incomplete; the same point, example or sentence repeated across sections), at the brief (a part of it the
document leaves out; a length far over the limit), at the references (a malformed entry: an author, date,
title or venue that is wrong, missing or a leftover of a web page or file; a source listed twice), and at its
claims (two statements that contradict each other; a number used inconsistently; the document going beyond
its sources without saying so). A date up to today's, given in the data, is not in the future. The reference
list's layout is the framework's on purpose (authors (year). title, link, site and kind, and for a paper the
pages it is cited at): report a wrong entry in it, not the layout.

Show every flaw: copy its evidence word for word from the document (a sentence, a table row or a reference
line), so that it can be found by search; a flaw you cannot quote, don't report. Say in one sentence what is
wrong and how to fix it, and whether the writers can fix it in the text (revise), it comes from how the
document was assembled and would recur in every document (framework), or it needs no fix (note). Report only
real flaws, not preferences of style; when you find none, say so with an empty list."""

    def __init__(self, worker: Worker) -> None:
        self._worker = worker

    async def attack(self, request: DocumentAttack) -> RedTeamReport:
        found = await self._worker.run(self.INSTRUCTIONS, request, Findings)
        shown = tuple(f for f in found.findings if self.quoted(f.evidence, request.document))
        unshown = tuple(f for f in found.findings if f not in shown)
        return RedTeamReport(shown=shown, unshown=unshown)

    @staticmethod
    def flat(text: str) -> str:
        """Whitespace collapsed and Markdown emphasis dropped: a quote needn't keep line breaks or stars."""
        return " ".join(re.sub(r"[*_`]", "", text).split())

    # a citation as the finished document shows it ([^9]) or as a section holds it ([@2402-01528v4-p001])
    MARKERS: ClassVar[re.Pattern[str]] = re.compile(r" ?\[\^\d+\]| ?\[@[^\]]*\]")

    @classmethod
    def quoted(cls, evidence: str, document: str) -> bool:
        """Whether the evidence is in the document: whole, or each part an ellipsis in the quote joins. Its
        citations are left out on both sides: the red team quotes the finished document, a revision looks the
        quote up in the sections, and every quote with a citation in it went unplaced (speculative-decoding,
        2026-11-26: "not made, no section holds it" for both findings the user kept)."""
        flat = cls.flat(cls.MARKERS.sub("", document))
        parts = [cls.flat(p) for p in re.split(r"\s*(?:\.\.\.|…)\s*", cls.MARKERS.sub("", evidence))]
        parts = [p.strip("\"' ") for p in parts if len(p.strip("\"' ")) >= 12]
        return bool(parts) and all(p in flat for p in parts)


class SourceCheck:
    """A finding that a statement is false is checked against the source the statement cites, by the citation
    audit's referee, and dropped when the source supports the statement. The red team sees the document, not
    its sources, and every such finding checked by hand in two runs was wrong: the source said what the
    document said (2026-11-25: an agreement of 85%, a monotonicity measured in training, a model's name)."""

    MARKERS: ClassVar[re.Pattern[str]] = DocumentRedTeam.MARKERS
    SHORTEST: ClassVar[int] = 30  # characters of a quote needed to find its statement

    def __init__(self, audit: CitationAudit) -> None:
        self._audit = audit

    @classmethod
    def plain(cls, text: str) -> str:
        return DocumentRedTeam.flat(cls.MARKERS.sub("", text))

    def statement(self, evidence: str) -> tuple[str, str] | None:
        """The cited statement (its section's file, its line) a quote of the document comes from."""
        parts = [self.plain(p) for p in re.split(r"\s*(?:\.\.\.|…)\s*", evidence)]
        part = max(parts, key=len, default="").strip(".\"' ")
        if len(part) < self.SHORTEST:
            return None
        for file, line in self._audit.statements():
            if part in self.plain(line):
                return file, line
        return None

    async def check(self, report: RedTeamReport) -> RedTeamReport:
        shown: list[Finding] = []
        refuted: list[Refuted] = []
        for finding in report.shown:
            found = self.statement(finding.evidence) if finding.fact else None
            if found is None:  # not a fact, or no cited statement to check it by: shown as it is
                shown.append(finding)
                continue
            (ruling,) = await self._audit.audit([found])
            if ruling.support is Support.SUPPORTED:
                refuted.append(Refuted(finding=finding, reason=ruling.reason))
            else:
                shown.append(finding)
        return report.model_copy(update={"shown": tuple(shown), "refuted": (*report.refuted, *refuted)})


class ReferenceCheck:
    """The reference list's mechanical flaws, found by plain code rather than a model: an author that is a
    site's address, no date, a title with a page's leftovers or cut short. The red team's first measurement
    (2026-11-23) missed most of these; code finds every one, for nothing."""

    ENTRY: ClassVar[re.Pattern[str]] = re.compile(r"^\[\^(\d+)\]: (.+?) \(([^)]*)\)\. \[\*(.+?)\*\]", re.M)
    ADDRESS: ClassVar[re.Pattern[str]] = re.compile(r"^[\w-]+(?:\.[\w-]+)+$")  # plato.stanford.edu
    LEFTOVER: ClassVar[re.Pattern[str]] = re.compile(r" > | \| |^GitHub - |(?:\.\.\.|…)$")

    @classmethod
    def issues(cls, document: str) -> tuple[str, ...]:
        found: list[str] = []
        for number, authors, year, title in cls.ENTRY.findall(document):
            wrong: list[str] = []
            if cls.ADDRESS.match(authors):
                wrong.append(f"its author is the site {authors}")
            if year == "n.d.":
                wrong.append("no date")
            if cls.LEFTOVER.search(title):
                wrong.append("its title has a page's leftovers or is cut short")
            if wrong:
                found.append(f"[^{number}] {title[:60]}: " + "; ".join(wrong))
        return tuple(found)
