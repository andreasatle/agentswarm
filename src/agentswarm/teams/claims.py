"""Checking a document claim by claim against the sources it cites (docs/RESEARCH.md section 14: the
established cheap structure for verifying a text against sources is to split it into claims, find the
evidence for each, and let a small model judge each claim on its evidence alone).

The documents keep one sentence per line, so a claim is a line that cites a source. Its evidence is either
the cited part whole (its named page and neighbours, when it names one), as the citation audit reads it, or
the passages hybrid search finds within the cited parts. A verdict that the source doesn't support the claim,
or supports only some of it, becomes a reviewer's finding (teams/review.py, the `claims` perspective).
"""

import asyncio
import re
from collections.abc import Mapping, Sequence
from typing import ClassVar, Protocol

from pydantic import BaseModel, ConfigDict, Field

from agentswarm.evaluation.citations import CitationAudit, CitationRequest, CitedSource, Support
from agentswarm.llm.workers import Worker, WorkerError
from agentswarm.retrieval.passages import PassageIndex
from agentswarm.teams.referee import Referee


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Citation(Frozen):
    name: str  # the source part, as cited: [@name]
    page: int | None


class CitedStatement(Frozen):
    """One line of a section that cites a source: the unit a claim check works on."""

    file: str
    text: str  # the line as it is in the file
    citations: tuple[Citation, ...]

    BRACKETS: ClassVar[re.Pattern[str]] = re.compile(r"\[@[^\]]*\]")

    @classmethod
    def all_in(cls, file: str, text: str) -> list["CitedStatement"]:
        found: list[CitedStatement] = []
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("#"):
                continue
            cited = tuple(
                Citation(name=m.group(1), page=int(m.group(2)) if m.group(2) else None)
                for m in CitationAudit.CITATION.finditer(line)
            )
            if cited:
                found.append(cls(file=file, text=line, citations=cited))
        return found

    def claim(self) -> str:
        """The statement without its citations: what a search looks for."""
        return " ".join(self.BRACKETS.sub(" ", self.text).split())


class SourceText(Protocol):
    """What a claim is checked against."""

    async def cited(
        self, statement: CitedStatement, sources: Mapping[str, str]
    ) -> tuple[CitedSource, ...]: ...


class WholeParts:
    """Each cited part whole, or the page it names with one on either side (as the citation audit reads)."""

    async def cited(self, statement: CitedStatement, sources: Mapping[str, str]) -> tuple[CitedSource, ...]:
        return tuple(
            CitedSource(
                name=c.name,
                page=c.page,
                text=CitationAudit.pages(sources[c.name], c.page)
                if c.name in sources
                else "(no such source)",
            )
            for c in statement.citations
        )


class Retrieved:
    """The passages of the cited parts that hybrid search ranks highest for the claim."""

    def __init__(self, index: PassageIndex, k: int = 6) -> None:
        self._index = index
        self._k = k  # passages per cited part

    async def cited(self, statement: CitedStatement, sources: Mapping[str, str]) -> tuple[CitedSource, ...]:
        """k passages from each cited part: shared across the parts, a statement citing four papers was given
        passages of one and judged unsupported (the review A/B, 2026-11-09)."""
        await self._index.sync(sources)
        cited: list[CitedSource] = []
        for c in statement.citations:
            if c.name not in sources:
                cited.append(CitedSource(name=c.name, page=c.page, text="(no such source)"))
                continue
            passages = await self._index.search(statement.claim(), [c.name], self._k)
            text = "\n\n[...]\n\n".join(
                (f"[page {p.page}]\n" if p.page is not None else "") + p.text for p in passages
            )
            cited.append(CitedSource(name=c.name, page=c.page, text=text or "(no passage found)"))
        return tuple(cited)


class ClaimVerdict(Frozen):
    """Your ruling on one statement: first your reasoning, then the verdict."""

    reason: str = Field(description="what the source text says about the statement")
    support: Support = Field(description="supported, partly (some of it, or overstated), or unsupported")
    source_quote: str = Field(
        default="",
        description="the words of the source text that settle it (that say otherwise, or say less), copied "
        "exactly; empty when the source says nothing about it",
    )


class CheckedClaim(Frozen):
    statement: CitedStatement
    support: Support | None  # None: no ruling (the call failed)
    reason: str
    source: str | None = None  # the cited part holding `source_quote` word for word
    source_quote: str = ""


class ClaimChecker:
    INSTRUCTIONS = """\
You check one statement of a document against the source text it cites. Decide from that text alone whether
it supports the statement: supported (it says this), partly (it says some of it, or something weaker or
narrower than the statement claims), or unsupported (it doesn't say this, or says otherwise). The text may be
the passages of a source found most relevant to the statement rather than the whole source. Quote the words
that settle it exactly as they appear."""

    CONCURRENCY: ClassVar[int] = 4  # checks in flight at once

    def __init__(self, worker: Worker, source_text: SourceText) -> None:
        self._worker = worker
        self._source_text = source_text

    async def check(
        self, statements: Sequence[CitedStatement], sources: Mapping[str, str]
    ) -> list[CheckedClaim]:
        """A verdict for each statement, in order."""
        limit = asyncio.Semaphore(self.CONCURRENCY)

        async def one(statement: CitedStatement) -> CheckedClaim:
            async with limit:
                return await self._one(statement, sources)

        return list(await asyncio.gather(*(one(s) for s in statements)))

    async def _one(self, statement: CitedStatement, sources: Mapping[str, str]) -> CheckedClaim:
        cited = await self._source_text.cited(statement, sources)
        request = CitationRequest(statement=statement.text, sources=cited)
        try:
            verdict = await self._worker.run(self.INSTRUCTIONS, request, ClaimVerdict)
        except WorkerError as error:
            return CheckedClaim(statement=statement, support=None, reason=str(error))
        holder = next(
            (
                c.name
                for c in statement.citations
                if verdict.source_quote
                and c.name in sources
                and Referee.around(sources[c.name], verdict.source_quote, 0) is not None
            ),
            None,
        )
        return CheckedClaim(
            statement=statement,
            support=verdict.support,
            reason=verdict.reason,
            source=holder,
            source_quote=verdict.source_quote if holder is not None else "",
        )
