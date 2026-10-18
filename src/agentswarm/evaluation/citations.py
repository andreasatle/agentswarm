"""Citation audit: does the cited source support what a document says? (the user and an outside review,
2026-10-18: "citation quality is independently measurable").

Plain code draws a seeded random sample of cited statements from a document's sections, finds each citation's
source part (and, when a page is named, that page with one on either side), and asks a referee model whether
the source supports the statement. Each statement is one line (the documents keep one sentence per line). The
result is a count anyone can check: supported, partly, not supported, and whether a named page holds it.
"""

import random
import re
from collections.abc import Sequence
from enum import StrEnum
from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field

from agentswarm.llm.workers import Worker, WorkerError


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Support(StrEnum):
    SUPPORTED = "supported"
    PARTLY = "partly"  # some of the statement is in the source, some isn't, or it overstates
    UNSUPPORTED = "unsupported"


class CitedSource(Frozen):
    name: str
    page: int | None  # the page the statement names, if any
    text: str  # the cited part, or the named page and its neighbours


class CitationRequest(Frozen):
    """The payload a referee receives: one statement and the source text it cites."""

    statement: str
    sources: tuple[CitedSource, ...]


class CitationAnswer(Frozen):
    """Your ruling on one statement: first your reasoning, then the verdict."""

    reason: str = Field(description="what the source says about the statement, quoting it briefly")
    support: Support = Field(description="supported, partly (some of it, or overstated), or unsupported")
    page_right: bool | None = Field(
        default=None, description="when a page is named: whether that page holds the support"
    )


class Audited(Frozen):
    file: str
    statement: str
    support: Support | None  # None: no ruling (the call failed)
    page_right: bool | None
    reason: str


class CitationAudit:
    INSTRUCTIONS = """\
You check one statement of a document against the source text it cites. Decide from the source alone
whether it supports the statement: supported (it says this), partly (it says some of it, or something
weaker or narrower than the statement claims), or unsupported (it doesn't say this, or says otherwise).
Where the statement names a page, also say whether that page holds the support. Quote the source briefly
in your reason."""

    CITATION: ClassVar[re.Pattern[str]] = re.compile(r"\[@([^\],\s]+)(?:,\s*page\s+(\d+))?[^\]]*\]")
    PART_LIMIT = 40_000  # characters of a cited part given whole

    def __init__(self, level: Path, worker: Worker) -> None:
        self._level = level  # a level's plain folder: sections/, sources/
        self._worker = worker

    def statements(self) -> list[tuple[str, str]]:
        """(file, line) for every line of the sections that cites a source."""
        found: list[tuple[str, str]] = []
        for path in sorted((self._level / "sections").glob("*.md")):
            for line in path.read_text().splitlines():
                if self.CITATION.search(line) and not line.startswith("#"):
                    found.append((path.name, line.strip()))
        return found

    def sample(self, size: int, seed: int) -> list[tuple[str, str]]:
        found = self.statements()
        return random.Random(seed).sample(found, min(size, len(found)))

    def sources(self, statement: str) -> tuple[CitedSource, ...]:
        cited: list[CitedSource] = []
        for match in self.CITATION.finditer(statement):
            name, page = match.group(1), int(match.group(2)) if match.group(2) else None
            path = self._level / "sources" / f"{name}.md"
            if not path.is_file():
                cited.append(CitedSource(name=name, page=page, text="(no such source)"))
                continue
            text = path.read_text()
            cited.append(CitedSource(name=name, page=page, text=self.pages(text, page)))
        return tuple(cited)

    @classmethod
    def pages(cls, text: str, page: int | None) -> str:
        """The named page and one on either side, when the part marks its pages; else the part (bounded)."""
        if page is not None:
            marked = re.split(r"(?m)^\[page (\d+)\]$", text)
            numbered = {int(marked[i]): marked[i + 1] for i in range(1, len(marked) - 1, 2)}
            near = [f"[page {p}]{numbered[p]}" for p in (page - 1, page, page + 1) if p in numbered]
            if near:
                return "\n".join(near)
        return text[: cls.PART_LIMIT]

    async def audit(self, statements: Sequence[tuple[str, str]]) -> list[Audited]:
        results: list[Audited] = []
        for file, statement in statements:
            request = CitationRequest(statement=statement, sources=self.sources(statement))
            try:
                answer = await self._worker.run(self.INSTRUCTIONS, request, CitationAnswer)
            except WorkerError as error:
                results.append(
                    Audited(file=file, statement=statement, support=None, page_right=None, reason=str(error))
                )
                continue
            results.append(
                Audited(
                    file=file,
                    statement=statement,
                    support=answer.support,
                    page_right=answer.page_right,
                    reason=answer.reason,
                )
            )
        return results

    @staticmethod
    def render(results: Sequence[Audited]) -> str:
        counts = {s: sum(r.support is s for r in results) for s in Support}
        paged = [r for r in results if r.page_right is not None]
        lines = [
            f"{len(results)} statements: {counts[Support.SUPPORTED]} supported, "
            f"{counts[Support.PARTLY]} partly, {counts[Support.UNSUPPORTED]} unsupported, "
            f"{sum(r.support is None for r in results)} not ruled"
            + (
                f"; named page right {sum(bool(r.page_right) for r in paged)} of {len(paged)}"
                if paged
                else ""
            )
        ]
        for r in results:
            mark = r.support.value if r.support is not None else "no ruling"
            lines.append(f"- [{mark}] {r.file}: {r.statement[:140]}")
            if r.support is not Support.SUPPORTED:
                lines.append(f"    {r.reason[:240]}")
        return "\n".join(lines)
