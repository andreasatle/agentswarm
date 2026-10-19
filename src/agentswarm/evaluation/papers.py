"""Papers for a topic, found on arXiv or OpenAlex, for `agentswarm learn` (the user, 2026-10-18: "ask for a
topic and you come up with documents"; then: arXiv is narrow, mainly machine learning, so OpenAlex for all).

A model turns the topic into a few search queries; plain code runs them against the search service and gathers
the candidates (title, authors, year, abstract); the model picks the few that together teach the topic, each
with a line on why; plain code downloads their PDFs into a folder with a reading list. The papers are real
because they come from the search, never from the model's memory (which invents some). The user reads the list
and adds, removes or swaps PDFs before paying for the tutorial. Paywalled papers are out of reach (OpenAlex
keeps only works with an open PDF); those the user adds by hand.
"""

import asyncio
import html
import re
import time
import unicodedata
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ElementTree
from collections.abc import Sequence
from enum import StrEnum
from pathlib import Path
from typing import ClassVar, Protocol

from pydantic import BaseModel, ConfigDict, Field

from agentswarm.core.domain import Reference
from agentswarm.llm.workers import Worker


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Candidate(Frozen):
    id: str  # the service's: arXiv's without the version (2305.20050), OpenAlex's work id (W2086162146)
    title: str
    authors: tuple[str, ...]
    year: int  # of its first version: a later version's PDF carries a later date (DeepSeek-R1 read as 2026)
    abstract: str
    venue: str = ""  # the journal or "arXiv preprint 2305.20050"
    cited: int | None = None  # times cited, where the service counts it (OpenAlex)
    pdfs: tuple[str, ...] = ()  # where its PDF may be, the likeliest to let a program download it first
    page: str = ""  # its landing page (the DOI), for a person to fetch it from
    free: str = ""  # where it is free to read, if the service knows (a PubMed Central page, say)
    access: str = ""  # how it is open: green (a repository copy), gold, hybrid, bronze (the publisher's whim)
    site: str = ""  # a web source's publisher (Wikipedia, UNESCO): its author and its in-text name
    kind: str = "pdf"  # its file's extension: pdf, txt (Wikipedia's text), html (a web page)

    LISTED: ClassVar[int] = 10  # authors named in a reference before "et al."
    NAME: ClassVar[int] = 70  # characters of a file name, about

    @property
    def file(self) -> str:
        """Its PDF's name, readable: stickgold-2005-sleep-dependent-memory-consolidation.pdf (the user,
        2026-10-18, on names like W2086162146.pdf)."""
        first = self.site or (self.surname(self.authors[0]) if self.authors else "anonymous")
        named = f"{first} {self.title}" if self.site else f"{first} {self.year} {self.title}"
        plain = unicodedata.normalize("NFKD", named).encode("ascii", "ignore")
        words = re.findall(r"[a-z0-9]+", plain.decode().lower())
        kept: list[str] = []
        for word in words:  # whole words, to about 70 characters
            if kept and len("-".join([*kept, word])) > self.NAME:
                break
            kept.append(word)
        return "-".join(kept) + f".{self.kind}"

    def reference(self, document: str) -> Reference:
        """The paper as a bibliography gives it, from the service's record rather than a model's reading."""
        if self.site:  # a web page: its publisher is its author; in the text, "(Wikipedia: Murano)"
            return Reference(
                document=document,
                authors=self.site,
                title=self.title,
                year=str(self.year),
                venue=self.site,
                short=f"{self.site}: {self.title}",
                url=self.page,
            )
        surnames = [self.surname(a) for a in self.authors] or ["Anonymous"]
        listed = ", ".join(self.initialled(a) for a in self.authors[: self.LISTED])
        authors = listed + (", et al." if len(self.authors) > self.LISTED else "")
        if len(surnames) == 1:
            short = f"{surnames[0]}, {self.year}"
        elif len(surnames) == 2:
            short = f"{surnames[0]} and {surnames[1]}, {self.year}"
        else:
            short = f"{surnames[0]} et al., {self.year}"
        return Reference(
            document=document,
            authors=authors,
            title=self.title,
            year=str(self.year),
            venue=self.venue,
            short=short,
            url=self.page if not self.venue.startswith("arXiv") else "",
        )

    @staticmethod
    def surname(author: str) -> str:
        return author.split()[-1] if author.split() else author

    @classmethod
    def initialled(cls, author: str) -> str:
        """ "Daya Guo" as "Guo, D."; a single name (an organisation, "DeepSeek-AI") as it is."""
        names = author.split()
        if len(names) < 2:
            return author
        return f"{names[-1]}, " + " ".join(f"{n[0]}." for n in names[:-1])


class PaperSearch(Protocol):
    def search(self, query: str, limit: int) -> list[Candidate]: ...

    def titled(self, title: str) -> Candidate | None:
        """The paper with this title, if the service has it."""
        ...

    def download(self, paper: Candidate, path: Path) -> None:
        """Its PDF, or OSError."""
        ...


class Fetching:
    """HTTP for a search service: a user agent, a timeout and a pause after each request."""

    AGENT = "agentswarm-learn/0.1 (paper search for a tutorial)"
    TIMEOUT_S = 60
    PAUSE_S = 1.0

    def fetch(self, url: str) -> bytes:
        request = urllib.request.Request(url, headers={"User-Agent": self.AGENT})
        with urllib.request.urlopen(request, timeout=self.TIMEOUT_S) as response:
            data: bytes = response.read()
        time.sleep(self.PAUSE_S)
        return data

    def download(self, paper: Candidate, path: Path) -> None:
        """The first of its PDF links that gives a PDF (2026-10-18: publishers refused 7 of 8 links on
        sleep and memory, with 403s or a bot check; repositories often serve the same paper)."""
        for url in paper.pdfs:
            try:
                data = self.fetch(url)
            except OSError:
                continue
            if data.startswith(b"%PDF"):  # not a publisher's landing page or a bot check
                path.write_bytes(data)
                return
        raise OSError(f"no PDF of {paper.id} could be downloaded")

    @staticmethod
    def words(title: str) -> list[str]:
        return re.findall(r"[a-z0-9]+", title.lower())


class ArxivSearch(Fetching):
    """arXiv's public API (export.arxiv.org): no key; it asks for a pause of 3 seconds between requests."""

    API = "https://export.arxiv.org/api/query"
    PDF = "https://arxiv.org/pdf/{id}"
    ATOM: ClassVar[dict[str, str]] = {"a": "http://www.w3.org/2005/Atom"}
    PAUSE_S = 3.0

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
                    venue=f"arXiv preprint {match.group(1)}",
                    pdfs=(cls.PDF.format(id=match.group(1)),),
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


class OpenAlexSearch(Fetching):
    """OpenAlex (api.openalex.org): every field, about 250 million works, no key needed (without one, a small
    free daily allowance shared by the network's address; a search costs it about $0.001). Open-access works
    only, so a person can fetch what a program may not."""

    API = "https://api.openalex.org/works"
    FIELDS = (
        "id,title,publication_year,authorships,abstract_inverted_index,best_oa_location,primary_location,"
        "cited_by_count,locations,doi,open_access"
    )

    def search(self, query: str, limit: int) -> list[Candidate]:
        words = " ".join(re.findall(r"[\w-]+", query))
        return self.works(f"title_and_abstract.search:{words},is_oa:true", limit * 3)[:limit]

    def titled(self, title: str) -> Candidate | None:
        wanted = self.words(title)
        found = self.works(f"title.search:{' '.join(wanted)},is_oa:true", 5)
        return next((c for c in found if self.words(c.title) == wanted), None)

    def works(self, filter: str, limit: int) -> list[Candidate]:
        query = {"filter": filter, "per-page": min(limit, 50), "select": self.FIELDS}
        return self.parse(self.fetch(self.API + "?" + urllib.parse.urlencode(query)))

    @classmethod
    def parse(cls, page: bytes) -> list[Candidate]:
        found: list[Candidate] = []
        for work in OpenAlexPage.model_validate_json(page).results:
            pdfs = cls.links(work)
            if not work.title or work.publication_year is None:
                continue
            source = work.primary_location.source if work.primary_location is not None else None
            found.append(
                Candidate(
                    id=work.id.rsplit("/", 1)[-1],
                    title=" ".join(work.title.split()),
                    authors=tuple(a.author.display_name for a in work.authorships if a.author.display_name),
                    year=work.publication_year,
                    abstract=cls.abstract(work.abstract_inverted_index or {}),
                    venue=source.display_name if source is not None and source.display_name else "",
                    cited=work.cited_by_count,
                    pdfs=pdfs,
                    page=work.doi or "",
                    free=(work.open_access.oa_url or "") if work.open_access is not None else "",
                    access=(work.open_access.oa_status or "") if work.open_access is not None else "",
                )
            )
        return found

    @staticmethod
    def links(work: "OpenAlexWork") -> tuple[str, ...]:
        """Every open PDF link of a work, repositories (PubMed Central, arXiv, a university's) before
        journals, which more often refuse a program."""
        places = [work.best_oa_location, *work.locations]
        ranked = sorted(
            (p for p in places if p is not None and p.pdf_url),
            key=lambda p: p.source is None or p.source.type != "repository",
        )
        return tuple(dict.fromkeys(p.pdf_url for p in ranked if p.pdf_url))

    @staticmethod
    def abstract(index: dict[str, list[int]]) -> str:
        """OpenAlex gives an abstract as each word's positions."""
        placed = sorted((at, word) for word, positions in index.items() for at in positions)
        return " ".join(word for _, word in placed)


class OpenAlexAuthor(BaseModel):
    display_name: str | None = None


class OpenAlexAuthorship(BaseModel):
    author: OpenAlexAuthor


class OpenAlexSource(BaseModel):
    display_name: str | None = None
    type: str | None = None  # journal, repository, ...


class OpenAlexLocation(BaseModel):
    pdf_url: str | None = None
    source: OpenAlexSource | None = None


class OpenAlexAccess(BaseModel):
    oa_status: str | None = None
    oa_url: str | None = None


class OpenAlexWork(BaseModel):
    id: str
    title: str | None = None
    publication_year: int | None = None
    authorships: list[OpenAlexAuthorship] = []
    abstract_inverted_index: dict[str, list[int]] | None = None
    best_oa_location: OpenAlexLocation | None = None
    primary_location: OpenAlexLocation | None = None
    cited_by_count: int | None = None
    doi: str | None = None
    open_access: OpenAlexAccess | None = None
    locations: list[OpenAlexLocation] = []


class OpenAlexPage(BaseModel):
    results: list[OpenAlexWork]


class WikipediaSearch(Fetching):
    """English Wikipedia's API: free, no key; every article, as plain text. For topics papers don't cover (a
    city, its history and art; the user, 2026-10-19: a guide to Venice). A Wikipedia article is a secondary
    source: good for history and background, and cited by its title and a link."""

    API = "https://en.wikipedia.org/w/api.php"
    ARTICLE = "https://en.wikipedia.org/wiki/{title}"
    PAUSE_S = 0.5
    AGENT = "agentswarm-learn/0.1 (https://github.com/andreasatle/agentswarm; a tutorial from sources)"

    def search(self, query: str, limit: int) -> list[Candidate]:
        return self.pages({"generator": "search", "gsrsearch": query, "gsrlimit": limit, "gsrnamespace": 0})

    def titled(self, title: str) -> Candidate | None:
        found = self.pages({"titles": title, "redirects": 1})
        return found[0] if found else None

    def pages(self, chosen: dict[str, str | int]) -> list[Candidate]:
        query = {
            "action": "query",
            "format": "json",
            "formatversion": 2,
            "prop": "extracts|info",
            "exintro": 1,
            "explaintext": 1,
            "exlimit": "max",
            **chosen,
        }
        answer = WikipediaAnswer.model_validate_json(
            self.fetch(self.API + "?" + urllib.parse.urlencode(query))
        )
        pages = sorted(answer.query.pages if answer.query is not None else [], key=lambda p: p.index or 0)
        return [self.candidate(p) for p in pages if not p.missing]

    @classmethod
    def candidate(cls, page: "WikipediaPage") -> Candidate:
        name = page.title.replace(" ", "_")
        return Candidate(
            id=f"wikipedia:{name}",
            title=page.title,
            authors=("Wikipedia",),
            year=int(page.touched[:4]) if page.touched else 0,
            abstract=" ".join((page.extract or "").split()),
            site="Wikipedia",
            kind="txt",
            page=cls.ARTICLE.format(title=urllib.parse.quote(name)),
        )

    def download(self, paper: Candidate, path: Path) -> None:
        title = paper.title
        query = {
            "action": "query",
            "format": "json",
            "formatversion": 2,
            "prop": "extracts",
            "explaintext": 1,
            "titles": title,
            "redirects": 1,
        }
        answer = WikipediaAnswer.model_validate_json(
            self.fetch(self.API + "?" + urllib.parse.urlencode(query))
        )
        pages = answer.query.pages if answer.query is not None else []
        text = pages[0].extract if pages and not pages[0].missing else None
        if not text:
            raise OSError(f"Wikipedia has no text for {title}")
        path.write_text(f"{title}\n\n{text}\n")


class WikipediaPage(BaseModel):
    title: str
    missing: bool = False
    extract: str | None = None
    touched: str | None = None
    index: int | None = None


class WikipediaPages(BaseModel):
    pages: list[WikipediaPage] = []


class WikipediaAnswer(BaseModel):
    query: WikipediaPages | None = None


class WebPage(Fetching):
    """A page the user names (an official site: UNESCO, a city's tourism office), kept as HTML and cited by
    its title, its site and a link."""

    TITLE: ClassVar[re.Pattern[str]] = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)

    def record(self, url: str, year: int) -> tuple[Candidate, bytes]:
        data = self.fetch(url)
        found = self.TITLE.search(data.decode("utf-8", "replace"))
        title = html.unescape(" ".join(found.group(1).split())) if found else url
        host = urllib.parse.urlparse(url).hostname or url
        site = host.removeprefix("www.")
        candidate = Candidate(
            id=f"web:{url}",
            title=title,
            authors=(site,),
            year=year,
            abstract="",
            site=site,
            kind="html",
            page=url,
        )
        return candidate, data


class PaperSource(StrEnum):
    OPENALEX = "openalex"  # every field
    ARXIV = "arxiv"  # machine learning, physics, maths: every paper open
    WIKIPEDIA = "wikipedia"  # background for topics papers don't cover: places, history, art

    def search(self) -> PaperSearch:
        match self:
            case PaperSource.OPENALEX:
                return OpenAlexSearch()
            case PaperSource.ARXIV:
                return ArxivSearch()
            case PaperSource.WIKIPEDIA:
                return WikipediaSearch()


class QueryRequest(Frozen):
    """The payload a searcher receives: the topic and the reader."""

    topic: str
    reader_knows: str
    queries: int  # how many to write


class SearchQueries(Frozen):
    """Your queries for the search service: a few words each, covering the topic's main threads."""

    queries: list[str] = Field(description="short keyword queries, e.g. 'process reward model verification'")
    known: list[str] = Field(
        default_factory=list,
        description="exact titles of well-known papers on the topic you are sure exist (each is looked up)",
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
    file: str | None  # its file in the folder; None: not there yet (the download failed)


class ReadingFolder(Frozen):
    """The papers picked for a topic, as kept beside their PDFs: papers.json for programs, papers.md for the
    user. Refreshed from the files actually there, whatever their names (the user, 2026-10-18: a PDF fetched
    by hand keeps the publisher's name, such as 9497.full.pdf)."""

    topic: str
    papers: tuple[Found, ...]

    RECORDS: ClassVar[str] = "papers.json"
    LISTING: ClassVar[str] = "papers.md"
    OPENING: ClassVar[int] = 3000  # characters of a document searched for a paper's title

    @classmethod
    def load(cls, folder: Path) -> "ReadingFolder | None":
        path = folder / cls.RECORDS
        return cls.model_validate_json(path.read_text()) if path.is_file() else None

    def save(self, folder: Path) -> None:
        (folder / self.RECORDS).write_text(self.model_dump_json(indent=1) + "\n")
        (folder / self.LISTING).write_text(self.render())

    def match(self, name: str, text: str) -> Found | None:
        """The paper a file holds: by its name, else by the paper's title on its first page."""
        named = next((f for f in self.papers if name in (f.file, f.paper.file)), None)
        if named is not None:
            return named
        opening = self.letters(text[: self.OPENING])
        return next((f for f in self.papers if self.letters(f.paper.title) in opening), None)

    def refreshed(self, files: Sequence[tuple[str, str]]) -> "ReadingFolder":
        """With each paper's file as found among (name, text) of the folder's documents."""
        held: dict[str, str] = {}
        for name, text in files:
            found = self.match(name, text)
            if found is not None:
                held.setdefault(found.paper.id, name)
        papers = tuple(f.model_copy(update={"file": held.get(f.paper.id)}) for f in self.papers)
        return self.model_copy(update={"papers": papers})

    @staticmethod
    def letters(text: str) -> str:
        """Lower-case letters and digits only: a title broken over lines or hyphenated still matches."""
        return re.sub(r"[^a-z0-9]", "", text.lower()) or "-"

    def render(self) -> str:
        lines = [f"# Papers for: {self.topic}", ""]
        for n, f in enumerate(self.papers, 1):
            authors = ", ".join(f.paper.authors[:3]) + (" et al." if len(f.paper.authors) > 3 else "")
            where = f.paper.venue or f.paper.id
            state = f"`{f.file}`" if f.file is not None else "**not here yet**"
            lines.append(f"{n}. **{f.paper.title}** ({authors}, {f.paper.year}), {where}: {state}.")
            lines.append(f"   {f.why}")
        missing = [f for f in self.papers if f.file is None]
        if missing:
            lines += [
                "",
                "**Download these by hand** (the publisher refused a program); any file name works:",
                "",
            ]
            for f in missing:
                links = [f.paper.free, *f.paper.pdfs, f.paper.page]  # the free copy first
                listed = ", ".join(dict.fromkeys(link for link in links if link)) or "(no link known)"
                note = " (bronze: free only if the publisher allows it)" if f.paper.access == "bronze" else ""
                lines.append(f"- {f.paper.title}{note}: {listed}")
        return "\n".join(lines) + "\n"


class PaperFinder:
    """The queries are run on the search service and each known title is looked up there (kept only if
    found); the picked ids are checked against the candidates (an id not among them is dropped) and their
    PDFs downloaded."""

    QUERY = """\
You plan a search for the sources of a tutorial or guide on the topic given, for the reader described. Write
the number of short keyword queries asked for, each covering a different thread of the topic (its foundations,
the main competing ideas or methods, the evidence for them; for a place, its history, buildings, art and
practical matters), so that together they find the sources it needs. Search ranks by matching words, not
importance, so also list the exact titles of the well-known papers or articles on the topic that you are sure
of: each is looked up, and kept only if found."""

    PICK = """\
You choose the papers a tutorial on the topic will be written from, for the reader described, from the
candidates found. Pick the number asked for that together teach the topic: the papers that introduced the key
ideas, the main competing approaches, and the evidence comparing them. Prefer papers that build on one
another, and influential original work (times cited, where given, is a hint) over small variations; a good
review may anchor the set. Use only ids from the candidates. Order them as a reader should meet them, and say
in one sentence what each adds."""

    QUERIES = 5
    PER_QUERY = 8
    KNOWN = 10
    CANDIDATES = 40  # known titles first; 2026-10-18: about 80 full abstracts cost $0.15 a pick on Sonnet
    ABSTRACT = 600  # characters of each abstract the picker reads

    def __init__(self, worker: Worker, search: PaperSearch) -> None:
        self._worker = worker
        self._search = search
        self._found: dict[str, Candidate] = {}  # every candidate as found, PDF links included

    async def candidates(self, topic: str, know: str) -> list[Candidate]:
        planned = await self._worker.run(
            self.QUERY, QueryRequest(topic=topic, reader_knows=know, queries=self.QUERIES), SearchQueries
        )
        found = self._found
        for title in planned.known[: self.KNOWN]:
            known = await asyncio.to_thread(self._search.titled, title)
            if known is not None:
                found.setdefault(known.id, known)
        for query in planned.queries[: self.QUERIES]:
            for candidate in await asyncio.to_thread(self._search.search, query, self.PER_QUERY):
                found.setdefault(candidate.id, candidate)
        return list(found.values())[: self.CANDIDATES]

    def shown(self, candidates: Sequence[Candidate]) -> tuple[Candidate, ...]:
        """As the picker sees them: abstracts cut, no links. Picked whether or not they download (2026-10-18:
        about 30% of OpenAlex's links let a program download, and the essential papers are often the refused
        ones); the user fetches the rest by hand."""
        return tuple(
            c.model_copy(update={"abstract": c.abstract[: self.ABSTRACT], "pdfs": (), "page": "", "free": ""})
            for c in candidates
        )

    async def pick(self, topic: str, know: str, papers: int, candidates: Sequence[Candidate]) -> list[Found]:
        request = PickRequest(
            topic=topic, reader_knows=know, papers=papers, candidates=self.shown(candidates)
        )
        chosen = await self._worker.run(self.PICK, request, ReadingList)
        known = {c.id: self._found.get(c.id, c) for c in candidates}
        picked: list[Found] = []
        for paper in chosen.papers:
            name = re.sub(r"v\d+$", "", paper.id.strip())
            if name in known and all(f.paper.id != name for f in picked):  # an invented id is dropped
                picked.append(Found(paper=known[name], why=paper.why, file=None))
        return picked[:papers]

    async def find(self, topic: str, know: str, papers: int, out: Path) -> ReadingFolder:
        """Searches, picks and downloads into `out`, and writes the reading list there."""
        picked = await self.pick(topic, know, papers, await self.candidates(topic, know))
        out.mkdir(parents=True, exist_ok=True)
        done: list[Found] = []
        for found in picked:
            try:
                await asyncio.to_thread(self._search.download, found.paper, out / found.paper.file)
            except OSError:
                done.append(found)  # the user fetches it by hand
                continue
            done.append(found.model_copy(update={"file": found.paper.file}))
        folder = ReadingFolder(topic=topic, papers=tuple(done))
        folder.save(out)
        return folder
