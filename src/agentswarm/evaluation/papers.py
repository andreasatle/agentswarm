"""Papers for a topic, found on arXiv, for `agentswarm learn` (the user, 2026-10-18: "ask for a topic and you
come up with documents").

A model turns the topic into a few search queries; plain code runs them against arXiv's API and gathers the
candidates (title, authors, year, abstract); the model picks the few that together teach the topic, each with
a line on why; plain code downloads their PDFs into a folder with a reading list. The papers are real because
they come from the search, never from the model's memory (which invents some). The user reads the list and
adds, removes or swaps PDFs before paying for the tutorial. arXiv misses paywalled journals and old classics;
those the user adds by hand.
"""

import asyncio
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ElementTree
from collections.abc import Sequence
from pathlib import Path
from typing import ClassVar, Protocol

from pydantic import BaseModel, ConfigDict, Field

from agentswarm.llm.workers import Worker


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Candidate(Frozen):
    id: str  # arXiv's, without the version: 2305.20050
    title: str
    authors: tuple[str, ...]
    year: int
    abstract: str


class PaperSearch(Protocol):
    def search(self, query: str, limit: int) -> list[Candidate]: ...

    def titled(self, title: str) -> Candidate | None:
        """The paper with this title, if arXiv has it."""
        ...

    def download(self, paper: str, path: Path) -> None: ...


class ArxivSearch:
    """arXiv's public API (export.arxiv.org): no key; it asks for a pause of 3 seconds between requests."""

    API = "https://export.arxiv.org/api/query"
    PDF = "https://arxiv.org/pdf/{id}"
    ATOM: ClassVar[dict[str, str]] = {"a": "http://www.w3.org/2005/Atom"}
    PAUSE_S = 3.0
    TIMEOUT_S = 60
    AGENT = "agentswarm-learn/0.1 (paper search for a tutorial)"

    def search(self, query: str, limit: int) -> list[Candidate]:
        terms = " AND ".join(f"all:{word}" for word in re.findall(r"[\w-]+", query))
        url = (
            self.API
            + "?"
            + urllib.parse.urlencode(
                {"search_query": terms, "start": 0, "max_results": limit, "sortBy": "relevance"}
            )
        )
        return self.parse(self.fetch(url))

    def titled(self, title: str) -> Candidate | None:
        terms = 'ti:"{}"'.format(
            title.replace('"', " ")
        )  # word by word, arXiv drops short words and finds none
        url = self.API + "?" + urllib.parse.urlencode({"search_query": terms, "start": 0, "max_results": 5})
        wanted = self.words(title)
        return next((c for c in self.parse(self.fetch(url)) if self.words(c.title) == wanted), None)

    @staticmethod
    def words(title: str) -> list[str]:
        return re.findall(r"[a-z0-9]+", title.lower())

    def download(self, paper: str, path: Path) -> None:
        path.write_bytes(self.fetch(self.PDF.format(id=paper)))

    def fetch(self, url: str) -> bytes:
        request = urllib.request.Request(url, headers={"User-Agent": self.AGENT})
        with urllib.request.urlopen(request, timeout=self.TIMEOUT_S) as response:
            data: bytes = response.read()
        time.sleep(self.PAUSE_S)
        return data

    @classmethod
    def parse(cls, feed: bytes) -> list[Candidate]:
        found: list[Candidate] = []
        for entry in ElementTree.fromstring(feed).findall("a:entry", cls.ATOM):
            link = entry.findtext("a:id", "", cls.ATOM)
            match = re.search(r"abs/([^v]+?)(?:v\d+)?$", link)
            if match is None:
                continue
            found.append(
                Candidate(
                    id=match.group(1),
                    title=" ".join(entry.findtext("a:title", "", cls.ATOM).split()),
                    authors=tuple(
                        " ".join(a.findtext("a:name", "", cls.ATOM).split())
                        for a in entry.findall("a:author", cls.ATOM)
                    ),
                    year=int(entry.findtext("a:published", "0000", cls.ATOM)[:4]),
                    abstract=" ".join(entry.findtext("a:summary", "", cls.ATOM).split()),
                )
            )
        return found


class QueryRequest(Frozen):
    """The payload a searcher receives: the topic and the reader."""

    topic: str
    reader_knows: str
    queries: int  # how many to write


class SearchQueries(Frozen):
    """Your queries for arXiv: a few words each, covering the topic's main threads."""

    queries: list[str] = Field(description="short keyword queries, e.g. 'process reward model verification'")
    known: list[str] = Field(
        default_factory=list,
        description="exact titles of well-known papers on the topic you are sure exist (checked on arXiv)",
    )


class PickRequest(Frozen):
    """The payload a picker receives: the topic, the reader and the candidates found."""

    topic: str
    reader_knows: str
    papers: int  # how many to pick
    candidates: tuple[Candidate, ...]


class PickedPaper(Frozen):
    id: str = Field(description="the candidate's id, exactly as given")
    why: str = Field(description="one sentence: what this paper gives the tutorial that the others don't")


class ReadingList(Frozen):
    """Your choice: the papers, in the order a reader should meet them."""

    papers: list[PickedPaper]


class Found(Frozen):
    paper: Candidate
    why: str
    file: Path | None  # None: the download failed


class PaperFinder:
    """The queries are run on arXiv and each known title is looked up there (kept only if found); the picked
    ids are checked against the candidates (an id not among them is dropped) and their PDFs downloaded."""

    QUERY = """\
You plan a literature search on arXiv for a tutorial on the topic given, for the reader described. Write the
number of short keyword queries asked for, each covering a different thread of the topic (its foundations, the
main competing methods, how they are evaluated), so that together they find the papers a tutorial needs.
arXiv ranks recent papers first, so also list the exact titles of the well-known papers on the topic that you
are sure of: each is looked up, and kept only if found."""

    PICK = """\
You choose the papers a tutorial on the topic will be written from, for the reader described, from the
candidates found on arXiv. Pick the number asked for that together teach the topic: the papers that introduced
the key ideas, the main competing approaches, and the evidence comparing them. Prefer papers that build on one
another, and influential original work over surveys of it or small variations. Use only ids from the
candidates. Order them as a reader should meet them, and say in one sentence what each adds."""

    QUERIES = 5
    PER_QUERY = 8
    KNOWN = 10
    CANDIDATES = 40  # known titles first; 2026-10-18: about 80 full abstracts cost $0.15 a pick on Sonnet
    ABSTRACT = 600  # characters of each abstract the picker reads

    def __init__(self, worker: Worker, search: PaperSearch) -> None:
        self._worker = worker
        self._search = search

    async def candidates(self, topic: str, know: str) -> list[Candidate]:
        planned = await self._worker.run(
            self.QUERY, QueryRequest(topic=topic, reader_knows=know, queries=self.QUERIES), SearchQueries
        )
        found: dict[str, Candidate] = {}
        for title in planned.known[: self.KNOWN]:
            known = await asyncio.to_thread(self._search.titled, title)
            if known is not None:
                found.setdefault(known.id, known)
        for query in planned.queries[: self.QUERIES]:
            for candidate in await asyncio.to_thread(self._search.search, query, self.PER_QUERY):
                found.setdefault(candidate.id, candidate)
        return [
            c.model_copy(update={"abstract": c.abstract[: self.ABSTRACT]})
            for c in list(found.values())[: self.CANDIDATES]
        ]

    async def pick(self, topic: str, know: str, papers: int, candidates: Sequence[Candidate]) -> list[Found]:
        request = PickRequest(topic=topic, reader_knows=know, papers=papers, candidates=tuple(candidates))
        chosen = await self._worker.run(self.PICK, request, ReadingList)
        known = {c.id: c for c in candidates}
        picked: list[Found] = []
        for paper in chosen.papers:
            name = re.sub(r"v\d+$", "", paper.id.strip())
            if name in known and all(f.paper.id != name for f in picked):  # an invented id is dropped
                picked.append(Found(paper=known[name], why=paper.why, file=None))
        return picked[:papers]

    async def find(self, topic: str, know: str, papers: int, out: Path) -> list[Found]:
        """Searches, picks and downloads into `out`, and writes the reading list there (papers.md)."""
        picked = await self.pick(topic, know, papers, await self.candidates(topic, know))
        out.mkdir(parents=True, exist_ok=True)
        done: list[Found] = []
        for found in picked:
            path = out / f"{found.paper.id}.pdf"
            try:
                await asyncio.to_thread(self._search.download, found.paper.id, path)
            except OSError:
                done.append(found)
                continue
            done.append(found.model_copy(update={"file": path}))
        (out / "papers.md").write_text(self.render(topic, done))
        return done

    @staticmethod
    def render(topic: str, found: Sequence[Found]) -> str:
        lines = [f"# Papers for: {topic}", ""]
        for n, f in enumerate(found, 1):
            authors = ", ".join(f.paper.authors[:3]) + (" et al." if len(f.paper.authors) > 3 else "")
            state = f"`{f.file.name}`" if f.file is not None else "download failed"
            lines.append(
                f"{n}. **{f.paper.title}** ({authors}, {f.paper.year}), arXiv {f.paper.id}, {state}."
            )
            lines.append(f"   {f.why}")
        return "\n".join(lines) + "\n"
