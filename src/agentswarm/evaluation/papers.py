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
import os
import re
import subprocess
import tempfile
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


class Standing(StrEnum):
    """What kind of publisher a web source has: how far a reader may trust it, and for what (RESEARCH.md
    section 15: deep research agents' citations often rest on weak sites)."""

    OFFICIAL = "official"  # the body that runs or decides the thing: a city, its transit operator, a museum
    INSTITUTIONAL = "institutional"  # a university, UNESCO, a public agency writing about it
    SCHOLARLY = "scholarly"  # a paper or a scholarly book
    REFERENCE = "reference"  # an encyclopedia
    PRESS = "press"  # a newspaper or magazine with editors
    GUIDE = "guide"  # an established travel or how-to publisher
    COMMERCIAL = "commercial"  # a seller of tickets, tours or rooms
    BLOG = "blog"  # one person's site


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
    standing: Standing | None = None  # a web source's kind of publisher, as the picker judged it

    LISTED: ClassVar[int] = 10  # authors named in a reference before "et al."
    NAME: ClassVar[int] = 70  # characters of a file name, about

    @property
    def download_name(self) -> str:
        """The name a file gets when this program downloads it, readable (stickgold-2005-sleep-dependent-
        memory-consolidation.pdf). Only for files the program writes itself: a name is never shown or
        matched before its file exists (the user, 2026-11-01: made-up names lead to broken links)."""
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
        if self.site:  # a web page: its authors if it names them, else its publisher; "(Wikipedia: Murano)"
            return Reference(
                document=document,
                authors=self.listed() if self.authors and self.authors != (self.site,) else self.site,
                title=self.title,
                year=str(self.year) if self.year else "n.d.",  # a page with no date says so
                venue=f"{self.site} ({self.standing})" if self.standing else self.site,
                short=self.web_short(self.site, self.title),
                url=self.page,
            )
        surnames = [self.surname(a) for a in self.authors] or ["Anonymous"]
        authors = self.listed()
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

    def listed(self) -> str:
        """The authors as a bibliography lists them: "Assran, M., Duval, Q., ..., et al." after ten."""
        listed = ", ".join(self.initialled(a) for a in self.authors[: self.LISTED])
        return listed + (", et al." if len(self.authors) > self.LISTED else "")

    SHORT_WORDS: ClassVar[int] = 4  # words of a web page's title in its in-text citation
    LABEL_WORDS: ClassVar[int] = 4  # words of a site's label after a title: " - Informatics Homepages Server"
    SEPARATOR: ClassVar[re.Pattern[str]] = re.compile(r"\s+[|\u2013\u2014-]\s+")

    @classmethod
    def web_title(cls, title: str) -> str:
        """A search result's title without what the site adds to it: leading tags ("[PDF] ...") and a short
        trailing label of the site (" - SciSpace", " | Intercom Help"). The references showed both
        (principia-to-lean, 2026-11-22). A cut title keeps its ellipsis: it is all the search gave."""
        own = " ".join(title.split())
        own = re.sub(r"^(?:\[[^\]]*\]\s*)+", "", own) or own
        separators = list(cls.SEPARATOR.finditer(own))
        if separators and len(own[separators[-1].end() :].split()) <= cls.LABEL_WORDS:
            own = own[: separators[-1].start()]
        return own.strip() or title

    @classmethod
    def web_short(cls, site: str, title: str) -> str:
        """A web page's in-text citation: its site and the start of its title, "(klarna.com: Klarna AI
        assistant handles two-thirds…)". The whole title after every sentence buried a blog post in citations
        (2026-11-13); the site alone can't tell two pages of one site apart. A trailing " | Site" or " - Site"
        is the page's own label for its site, and is left out."""
        own = re.split(r"\s+[|\u2013\u2014-]\s+", title)[0].strip().strip("\"'\u201c\u201d")
        own = re.sub(r"^(?:\[[^\]]*\]\s*)+", "", own) or title  # "[2302.06590] ...", "[PDF] ...": no tags
        words = own.split()
        cut = len(words) > cls.SHORT_WORDS  # a cut title ends in "…", without the punctuation it was cut at
        start = " ".join(words[: cls.SHORT_WORDS]).rstrip(",:;.-") + ("\u2026" if cut else "")
        return f"{site}: {start}"

    @staticmethod
    def names(author: str) -> list[str]:
        """The name's words, without a stray initial after the surname: OpenAlex gave "Jan Born A" for Jan
        Born, and the document cited "Gais and A, 2004" (2026-11-01)."""
        names = author.split()
        if len(names) > 2 and len(names[-1].rstrip(".")) == 1:
            return names[:-1]
        return names

    @classmethod
    def surname(cls, author: str) -> str:
        names = cls.names(author)
        return names[-1] if names else author

    @classmethod
    def initialled(cls, author: str) -> str:
        """ "Daya Guo" as "Guo, D."; a single name (an organisation, "DeepSeek-AI") as it is."""
        names = cls.names(author)
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

    def described(self, paper: Candidate) -> Candidate:
        """Its record with what the source says about itself (a web page's authors, year and title)."""
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

    def described(self, paper: Candidate) -> Candidate:
        return paper  # a catalogue's record (arXiv, OpenAlex, Wikipedia) is already the source's own

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


class TavilyQuery(Frozen):
    query: str
    max_results: int
    search_depth: str = "basic"  # 1 credit; "advanced" is 2


class TavilyResult(BaseModel):
    model_config = ConfigDict(frozen=True)  # the service adds fields; only these are read

    url: str
    title: str
    content: str = ""  # the passage of the page that matched
    published_date: str | None = None  # given for news results


class TavilyResults(BaseModel):
    results: list[TavilyResult] = []


class TavilyExtract(Frozen):
    urls: list[str]
    extract_depth: str = "basic"  # 1 credit per 5 pages; never charged for a page that fails


class TavilyPage(BaseModel):
    url: str
    raw_content: str | None = None


class TavilyExtracted(BaseModel):
    results: list[TavilyPage] = []


class TavilySearch(Fetching):
    """The open web through Tavily's search API (goal 14; docs/design/web-research.md): ranked pages for a
    query, and a page's text cleaned of its navigation, so no scraping code of ours meets a site. A key in the
    environment (TAVILY_API_KEY); 1,000 free credits a month, a search 1 credit, 5 pages' text 1 credit. A web
    page is cited by its site, its title and its link."""

    API = "https://api.tavily.com"
    KEY = "TAVILY_API_KEY"
    PAUSE_S = 0.2

    def __init__(self, key: str | None = None) -> None:
        self._key = key

    def post(self, path: str, body: BaseModel) -> bytes:
        key = self._key or os.environ.get(self.KEY)
        if not key:
            raise OSError(f"web search needs {self.KEY} in the environment (.env)")
        request = urllib.request.Request(
            self.API + path,
            data=body.model_dump_json().encode(),
            method="POST",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        )
        with urllib.request.urlopen(request, timeout=self.TIMEOUT_S) as response:
            data: bytes = response.read()
        time.sleep(self.PAUSE_S)
        return data

    def search(self, query: str, limit: int) -> list[Candidate]:
        found = TavilyResults.model_validate_json(
            self.post("/search", TavilyQuery(query=query, max_results=min(limit, 20)))
        )
        return [self.candidate(r) for r in found.results]

    def titled(self, title: str) -> Candidate | None:
        return None  # the web has no catalogue of titles: the queries find its pages

    def candidate(self, result: TavilyResult) -> Candidate:
        site = (urllib.parse.urlparse(result.url).hostname or result.url).removeprefix("www.")
        dated = re.match(r"\d{4}", result.published_date or "")
        arxiv = re.search(r"arxiv\.org/(?:abs|pdf|html)/((\d{2})\d{2}\.\d{4,5})", result.url)
        if arxiv and dated is None:  # an arXiv id starts with its year and month: 2001.08361 is 2020
            dated = re.match(r"\d{4}", f"20{arxiv.group(2)}")
        return Candidate(
            id=f"web:{result.url}",
            title=Candidate.web_title(result.title) if result.title.strip() else result.url,
            authors=(site,),
            year=int(dated.group(0)) if dated else 0,  # no date: not the year it was read (2026-11-13)
            abstract=" ".join(result.content.split()),
            site=site,
            # An arXiv page is the paper's abstract: the paper itself is its PDF, which arXiv lets programs
            # download (2026-11-10: Kaplan et al. came as 7 KB of abstract page)
            kind="pdf" if arxiv else "txt",
            pdfs=(ArxivSearch.PDF.format(id=arxiv.group(1)),) if arxiv else (),
            page=result.url,
        )

    def download(self, paper: Candidate, path: Path) -> None:
        if paper.pdfs:
            Fetching.download(self, paper, path)
            return
        extracted = TavilyExtracted.model_validate_json(
            self.post("/extract", TavilyExtract(urls=[paper.page]))
        )
        text = next((r.raw_content for r in extracted.results if r.raw_content), None)
        if not text:
            raise OSError(f"no text could be taken from {paper.page}")
        path.write_text(f"{paper.title}\n{paper.page}\n\n{text.strip()}\n")

    def described(self, paper: Candidate) -> Candidate:
        """The page's own authors, year and title, from its citation metadata or its PDF; the search's
        record where the page says nothing or can't be fetched. Searched sources were cited by their site,
        undated, under the search's title (principia-to-lean, 2026-11-22: seven Stanford Encyclopedia
        entries, each naming its author and date)."""
        try:
            data = self.fetch(paper.page)
        except OSError:
            return paper
        said = WebPage.metadata(paper.page, data)
        return paper.model_copy(
            update={
                "title": said.title or paper.title,
                "authors": said.authors or paper.authors,
                "year": paper.year or said.year,
            }
        )


class PageMetadata(Frozen):
    """What a page or PDF says about itself; each field empty where it says nothing."""

    title: str = ""
    authors: tuple[str, ...] = ()  # forename first, as the catalogues give them
    year: int = 0


class WebPage(Fetching):
    """A page the user names (an official site: UNESCO, a city's tourism office), kept as HTML and cited by
    its title, its site and a link; and what any page or PDF says about itself (its citation metadata)."""

    TITLE: ClassVar[re.Pattern[str]] = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
    META: ClassVar[re.Pattern[str]] = re.compile(r"<meta\s[^>]*>", re.I)
    ATTRIBUTE: ClassVar[re.Pattern[str]] = re.compile(r"""([\w:.-]+)\s*=\s*(["'])(.*?)\2""", re.S)
    TITLES: ClassVar[tuple[str, ...]] = ("citation_title", "dc.title", "og:title")
    AUTHORS: ClassVar[tuple[str, ...]] = (
        "citation_author",
        "dc.creator",
        "author",
    )  # the first that names any
    JOINING: ClassVar[frozenset[str]] = frozenset(
        {"and", "of", "for", "the", "in", "on", "with", "to", "a", "an"}
    )
    # a PDF's Title that is its file's name, not its title ("Microsoft Word - A computer-checked proof...doc")
    FILE_TITLE: ClassVar[re.Pattern[str]] = re.compile(r"^Microsoft \w+ - |\.(?:docx?|tex|dvi|pdf)$", re.I)
    MARKS: ClassVar[str] = (
        " *\u2217\u2020\u2021"  # footnote marks after a title (Wadler: an asterisk operator)
    )

    def record(self, url: str) -> tuple[Candidate, bytes]:
        data = self.fetch(url)
        pdf = data.startswith(
            b"%PDF"
        )  # a named paper, not a page (lean-lang.org/papers/lean4.pdf, 2026-11-21)
        said = self.metadata(url, data)
        found = None if pdf else self.TITLE.search(data.decode("utf-8", "replace"))
        title = said.title or (html.unescape(" ".join(found.group(1).split())) if found else "")
        site = (urllib.parse.urlparse(url).hostname or url).removeprefix("www.")
        candidate = Candidate(
            id=f"web:{url}",
            title=title or url,
            authors=said.authors or (site,),
            year=said.year,
            abstract="",
            site=site,
            kind="pdf" if pdf else "html",
            page=url,
        )
        return candidate, data

    @classmethod
    def metadata(cls, url: str, data: bytes) -> PageMetadata:
        """A page's title and authors from its citation metadata (citation_*, Dublin Core, Open Graph), its
        year from that or its address; a PDF's from its own properties and first lines."""
        if data.startswith(b"%PDF"):
            return cls.pdf_described(url, data)
        metas = cls.metas(data.decode("utf-8", "replace"))
        title = next((v for key in cls.TITLES for k, v in metas if k == key and v), "")
        authors: tuple[str, ...] = ()
        for key in cls.AUTHORS:
            authors = tuple(cls.forename_first(v) for k, v in metas if k == key and v)
            if authors:
                break
        return PageMetadata(title=title, authors=authors, year=cls.published(url, data))

    @classmethod
    def metas(cls, page: str) -> list[tuple[str, str]]:
        """Each <meta> tag's name (or property, or itemprop), lower case, and its content, in order."""
        found: list[tuple[str, str]] = []
        for tag in cls.META.findall(page):
            attributes = {k.lower(): html.unescape(v) for k, _, v in cls.ATTRIBUTE.findall(tag)}
            name = attributes.get("name") or attributes.get("property") or attributes.get("itemprop")
            if name and "content" in attributes:
                found.append((name.lower(), " ".join(attributes["content"].split())))
        return found

    @staticmethod
    def forename_first(author: str) -> str:
        """ "Irvine, Andrew David" as "Andrew David Irvine"; a name without one comma as it is."""
        parts = [p.strip() for p in author.split(",")]
        return f"{parts[1]} {parts[0]}" if len(parts) == 2 and all(parts) else author

    # a journal's own citation year first; then Dublin Core, Open Graph, schema.org
    DATED: ClassVar[tuple[str, ...]] = (
        "citation_publication_date",
        "dc.date",
        "article:published_time",
        "datepublished",
    )
    JSON_DATE: ClassVar[re.Pattern[str]] = re.compile(r'"datePublished"\s*:\s*"(\d{4})')
    ARXIV: ClassVar[re.Pattern[str]] = re.compile(r"arxiv\.org/(?:abs|pdf)/(\d{2})(\d{2})\.\d{4,5}")
    YEAR: ClassVar[re.Pattern[str]] = re.compile(r"\b(?:19|20)\d{2}\b")

    @classmethod
    def published(cls, url: str, page: bytes) -> int:
        """The year a named page or paper came out, from its address (arXiv's yymm number) or its metadata; 0
        when neither says, shown as "n.d." (the fetch year was used before, so FunSearch, 2023, was cited as
        2026; lean-agents-why, 2026-11-21)."""
        arxiv = cls.ARXIV.search(url)
        if arxiv:
            return 2000 + int(arxiv.group(1))
        text = page.decode("utf-8", "replace")
        metas = cls.metas(text)
        for key in cls.DATED:
            year = next((re.match(r"\d{4}", v) for k, v in metas if k == key and re.match(r"\d{4}", v)), None)
            if year:
                return int(year.group(0))
        found = cls.JSON_DATE.search(text)
        return int(found.group(1)) if found else 0

    @classmethod
    def pdf_described(cls, url: str, data: bytes) -> PageMetadata:
        """A PDF's title (its Title property unless that is a file name, else its first lines) and year (an
        arXiv number, else the file's creation date: Wadler 2015, Gonthier 2005, Lean 4 2021 in
        principia-to-lean). No authors: the Author property is often a login ("gonthier")."""
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "named.pdf"
            path.write_bytes(data)
            info = subprocess.run(["pdfinfo", str(path)], capture_output=True, text=True, check=False).stdout
            first = subprocess.run(
                ["pdftotext", "-l", "1", "-enc", "UTF-8", str(path), "-"],
                capture_output=True,
                text=True,
                check=False,
            ).stdout
        fields = {k.strip(): v.strip() for k, _, v in (line.partition(":") for line in info.splitlines())}
        meta = fields.get("Title", "")
        title = meta if meta and not cls.FILE_TITLE.search(meta) else cls.first_title(first)
        arxiv = cls.ARXIV.search(url)
        created = cls.YEAR.search(fields.get("CreationDate", ""))
        year = 2000 + int(arxiv.group(1)) if arxiv else int(created.group(0)) if created else 0
        return PageMetadata(title=title, year=year)

    @classmethod
    def first_title(cls, text: str) -> str:
        """A title from a PDF's first lines, joined where it breaks: after "and", "of"... ("The Lean 4 Theorem
        Prover and / Programming Language") or between capitals ("HISTORY OF INTERACTIVE THEOREM / PROVING"),
        and an all-capitals title in title case."""
        lines = [" ".join(line.split()) for line in text.splitlines() if line.strip()][:3]
        title = lines[0].rstrip(cls.MARKS) if lines else ""
        for line in lines[1:]:
            joins = title.rsplit(" ", 1)[-1].lower() in cls.JOINING or (title.isupper() and line.isupper())
            if not joins:
                break
            title += " " + line.rstrip(cls.MARKS)
        if title.isupper():
            words = title.lower().split()
            title = " ".join(w if i and w in cls.JOINING else w.capitalize() for i, w in enumerate(words))
        return title


class PaperSource(StrEnum):
    OPENALEX = "openalex"  # every field
    ARXIV = "arxiv"  # machine learning, physics, maths: every paper open
    WIKIPEDIA = "wikipedia"  # background for topics papers don't cover: places, history, art
    WEB = "web"  # the open web through a search API: official sites, institutions, press, guides

    @property
    def queries(self) -> int:
        """Queries a search plan writes: the web needs one or two for every thread of a request (getting
        around, prices, what to see), a scholarly index fewer, each finding many papers."""
        return 8 if self is PaperSource.WEB else PaperFinder.QUERIES

    def search(self) -> PaperSearch:
        match self:
            case PaperSource.OPENALEX:
                return OpenAlexSearch()
            case PaperSource.ARXIV:
                return ArxivSearch()
            case PaperSource.WIKIPEDIA:
                return WikipediaSearch()
            case PaperSource.WEB:
                return TavilySearch()


class QueryRequest(Frozen):
    """The payload a searcher receives: the topic, the reader, and the brief when the user wrote one."""

    topic: str
    reader_knows: str
    queries: int  # how many to write
    # The user's own brief: what the document must cover. 2026-11-10: planned from the topic line alone, a
    # search for an analysis of LLM scaling found nothing on costs, energy, new architectures or crash risk
    brief: str = ""


class SearchQueries(Frozen):
    """Your plan: the threads the document must cover, then short queries for the search service."""

    threads: list[str] = Field(
        default_factory=list,
        description="the questions the reader needs answered, one per thread of the topic (for a guide to a "
        "city: getting there, getting around, what to see, when to go, what it costs, its history)",
    )
    queries: list[str] = Field(description="short keyword queries, e.g. 'process reward model verification'")
    known: list[str] = Field(
        default_factory=list,
        description="exact titles of well-known papers on the topic you are sure exist (each is looked up)",
    )


class Thread(Frozen):
    """A question the document must answer, numbered so a pick can name it exactly."""

    number: int
    question: str


class PickRequest(Frozen):
    """The payload a picker receives: the topic, the reader and the candidates found."""

    topic: str
    reader_knows: str
    papers: int  # how many to pick
    brief: str = ""  # the user's own brief, when they wrote one
    threads: tuple[Thread, ...] = ()  # what the picks must cover between them, each at least once
    candidates: tuple[Candidate, ...]


class PickedPaper(Frozen):
    id: str = Field(description="the candidate's id, exactly as given")
    why: str = Field(description="one sentence: what this source gives the document that the others don't")
    covers: int | None = Field(
        default=None, description="the number of the thread it answers (empty when none is listed)"
    )
    standing: Standing | None = Field(
        default=None, description="for a web page: what kind of publisher its site is; empty for a paper"
    )


class ReadingList(Frozen):
    """Your choice: the papers, in the order a reader should meet them."""

    papers: list[PickedPaper]


class Found(Frozen):
    paper: Candidate
    why: str
    covers: str = ""  # the thread of the request it was picked for
    file: str | None  # its file in the folder; None: not there yet (the download failed)


class SourceExcerpt(Frozen):
    """The start of a downloaded source, as the coverage check reads it."""

    file: str
    title: str
    text: str


class CoverageRequest(Frozen):
    """The payload of the coverage check: the threads the document must cover and the sources found."""

    topic: str
    reader_knows: str
    brief: str = ""
    threads: tuple[Thread, ...]
    sources: tuple[SourceExcerpt, ...]


class ThreadAnswer(Frozen):
    thread: int = Field(description="the thread's number, exactly as given")
    file: str | None = Field(default=None, description="the source that answers it; empty when none does")
    quote: str = Field(default="", description="the passage of that source that answers it, copied verbatim")
    queries: list[str] = Field(
        default_factory=list,
        description="when no source answers it: one or two short, narrower queries that would find one",
    )


class Coverage(Frozen):
    """For every thread: the source and passage that answer it, or the queries that might find one."""

    threads: list[ThreadAnswer]


class CoveredThread(Frozen):
    """A thread a source answers, with the passage that shows it (checked to be in the source)."""

    thread: str
    file: str
    quote: str


class TextReader(Protocol):
    def read(self, path: Path) -> str: ...


class ReadingFolder(Frozen):
    """The papers picked for a topic, as kept beside their PDFs: papers.json for programs, papers.md for the
    user. Refreshed from the files actually there, whatever their names (the user, 2026-10-18: a PDF fetched
    by hand keeps the publisher's name, such as 9497.full.pdf)."""

    topic: str
    papers: tuple[Found, ...]
    threads: tuple[str, ...] = ()  # what the request needs covered, as the search plan listed it
    answered: tuple[CoveredThread, ...] = ()  # threads answered by a quoted passage, at the last check
    progress: tuple[int, ...] = ()  # threads covered at each check: after the first search, after each round

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
        """The paper a file holds: by its name; else by its DOI or its title on the first page; else by most
        of its title's words there (a two-column first page interleaves the title with the authors: Nishida et
        al. 2009, fetched by hand as bhn155.pdf, 2026-11-01)."""
        named = next((f for f in self.papers if f.file is not None and name == f.file), None)
        if named is not None:
            return named
        # A record kept for another file is that file's: a page that links to it near its top is not it (the
        # Lean community homepage, added by hand, took the record of the textbook it links to, 2026-11-21)
        free = [f for f in self.papers if f.file is None or f.file == name]
        start = text[: self.OPENING]
        opening = self.letters(start)
        for found in free:
            doi = self.doi(found.paper.page)
            if (doi and doi in start.lower()) or self.letters(found.paper.title) in opening:
                return found
        words = set(re.findall(r"[a-z0-9]+", start.lower()))
        scored = [(self.covered(f.paper.title, words), f) for f in free]
        best = max(scored, key=lambda pair: pair[0], default=(0.0, None))
        return best[1] if best[0] >= self.COVERED else None

    COVERED: ClassVar[float] = 0.9  # of a title's words found on the first page, for a match by words

    @staticmethod
    def doi(page: str) -> str:
        """The DOI in a landing page's address (https://doi.org/10.1093/cercor/bhn155), lower-cased."""
        found = re.search(r"10\.\d{4,9}/\S+", page)
        return found.group(0).lower() if found else ""

    @staticmethod
    def covered(title: str, words: set[str]) -> float:
        wanted = [w for w in re.findall(r"[a-z0-9]+", title.lower()) if len(w) > 2]
        return sum(w in words for w in wanted) / len(wanted) if len(wanted) >= 4 else 0.0

    def refreshed(self, files: Sequence[tuple[str, str]]) -> "ReadingFolder":
        """With each paper's file as found among (name, text) of the folder's documents: files known by name
        first, so a page that names another file's paper near its top can't take it by coming first
        alphabetically (the Lean community homepage took the textbook's record, 2026-11-21)."""
        held: dict[str, str] = {}
        names = {name for name, _ in files}
        for f in self.papers:
            if f.file is not None and f.file in names and f.file not in held.values():
                held[f.paper.id] = f.file
        for name, text in files:
            if name in held.values():
                continue
            found = self.match(name, text)
            if found is not None and found.paper.id not in held:
                held[found.paper.id] = name
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
            standing = f" ({f.paper.standing})" if f.paper.standing else ""
            year = f.paper.year or "n.d."  # a page with no date
            lines.append(f"{n}. **{f.paper.title}** ({authors}, {year}), {where}{standing}: {state}.")
            lines.append(f"   {f.why}" + (f" Covers: {f.covers}" if f.covers else ""))
        if self.progress:  # checked against the sources' text: covered only with a quoted passage
            rounds = len(self.progress) - 1
            plural = "s" if rounds != 1 else ""
            later = f", {self.progress[-1]} after {rounds} more round{plural}" if rounds else ""
            first = f"{self.progress[0]} of {len(self.threads)} after the first search"
            lines += ["", f"**Threads covered by a quoted passage:** {first}{later}."]
            lines += [f'- {c.thread}: `{c.file}`, "{c.quote}"' for c in self.answered]
            uncovered = [t for t in self.threads if all(c.thread != t for c in self.answered)]
        else:
            uncovered = [t for t in self.threads if all(f.covers != t for f in self.papers)]
        if uncovered:
            lines += ["", "**Threads no source covers:** " + "; ".join(uncovered)]
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
    PDFs downloaded.

    Then, when the plan listed threads, the search goes on until they are covered (2026-11-14,
    docs/design/web-research.md): a check reads the downloaded sources and names, for each thread, the source
    and the passage that answer it, a passage plain code finds in that source; for the threads none answers it
    writes narrower queries, whose new results are picked from for those threads only. It stops when every
    thread is covered, after ROUNDS rounds, or when a round covers no new thread."""

    QUERY = """\
You plan a search for the sources of a tutorial or guide on the topic given, for the reader described. First
list the threads the document must cover, as the questions this reader needs answered (for a field: its
foundations, the main competing ideas or methods, the evidence for them; for a place: getting there, getting
around, what to see, when to go, what it costs, its history and art); when a brief is given, every part of it
is a thread of its own, except the document's own conclusions (what the cases have in common, the lessons
to draw), which the writer draws from the sources and no search finds. Then write the number of short keyword
queries asked for, so that every thread has at least one. Search ranks by matching words, not importance, so
also list the exact titles of the well-known papers or articles on the topic that you are sure of: each is
looked up, and kept only if found."""

    PICK = """\
You choose the papers a tutorial on the topic will be written from, for the reader described, from the
candidates found. Pick the number asked for that together teach the topic: the papers that introduced the key
ideas, the main competing approaches, and the evidence comparing them. Prefer papers that build on one
another, and influential original work (times cited, where given, is a hint) over small variations; a good
review may anchor the set. When threads are listed, the picks must cover every one of them between them: say
which thread each answers, by its number. For a web page, say what kind of publisher its site is, and prefer
official and institutional sites where a thread has them: a ticket seller's or a blogger's page only where
nothing better covers its thread. Use only ids from the candidates. Order them as a reader should meet them,
and say in one sentence what each adds."""

    COVER = """\
You check which of the threads a document must cover are answered by the sources found so far, for the reader
described. For each thread, name the source that answers it and copy the passage that does, word for word (a
sentence or two). A passage answers a thread only if the document could be written from it: it gives what the
question asks for (the facts, figures, steps or evidence), and for a broad thread its substance, not one
general sentence about it. A source that only mentions the subject, or touches the thread in passing, doesn't
answer it: then say no source answers it. When none does, write one or two short search queries, narrower
than a first search, that would find a source that does."""

    QUERIES = 5
    PER_QUERY = 8
    KNOWN = 10
    CANDIDATES = 40  # known titles first; 2026-10-18: about 80 full abstracts cost $0.15 a pick on Sonnet
    ABSTRACT = 600  # characters of each abstract the picker reads
    ROUNDS = 2  # follow-up rounds at most (the design: gap-driven search typically stops after 2)
    PER_GAP = 2  # follow-up queries searched for each uncovered thread
    FOLLOW_UPS = 8  # follow-up queries in a round at most
    MORE = 4  # sources added in a round at most: one for each uncovered thread
    EXCERPT = 4000  # characters of each source the check reads
    MIN_QUOTE = 20  # characters; a shorter passage matches too easily to show a thread is answered

    def __init__(
        self,
        worker: Worker,
        search: PaperSearch,
        queries: int | None = None,
        brief: str = "",
        *,
        reader: TextReader,
    ) -> None:
        self._brief = brief  # the user's own brief: the plan and the picks cover all of it
        self._worker = worker
        self._search = search
        self._reader = reader  # a downloaded source's text, for the coverage check
        self._queries = queries or self.QUERIES
        self._found: dict[str, Candidate] = {}  # every candidate as found, PDF links included
        self._threads: tuple[str, ...] = ()  # the threads the query plan listed, for the picker to cover

    async def candidates(self, topic: str, know: str) -> list[Candidate]:
        planned = await self._worker.run(
            self.QUERY,
            QueryRequest(topic=topic, reader_knows=know, queries=self._queries, brief=self._brief),
            SearchQueries,
        )
        self._threads = tuple(planned.threads)
        found = self._found
        for title in planned.known[: self.KNOWN]:
            known = await asyncio.to_thread(self._search.titled, title)
            if known is not None:
                found.setdefault(known.id, known)
        for query in planned.queries[: self._queries]:
            for candidate in await asyncio.to_thread(self._search.search, query, self.PER_QUERY):
                found.setdefault(candidate.id, candidate)
        return list(found.values())[: self.CANDIDATES]

    async def searched(self, queries: Sequence[str]) -> list[Candidate]:
        """The candidates the follow-up queries find that no earlier search found."""
        new: dict[str, Candidate] = {}
        for query in list(dict.fromkeys(queries))[: self.FOLLOW_UPS]:
            for candidate in await asyncio.to_thread(self._search.search, query, self.PER_QUERY):
                if candidate.id not in self._found:
                    new.setdefault(candidate.id, candidate)
        self._found.update(new)
        return list(new.values())[: self.CANDIDATES]

    def shown(self, candidates: Sequence[Candidate]) -> tuple[Candidate, ...]:
        """As the picker sees them: abstracts cut, no links. Picked whether or not they download (2026-10-18:
        about 30% of OpenAlex's links let a program download, and the essential papers are often the refused
        ones); the user fetches the rest by hand."""
        return tuple(
            c.model_copy(update={"abstract": c.abstract[: self.ABSTRACT], "pdfs": (), "page": "", "free": ""})
            for c in candidates
        )

    def numbered(self) -> tuple[Thread, ...]:
        return tuple(Thread(number=n, question=t) for n, t in enumerate(self._threads, 1))

    async def pick(
        self,
        topic: str,
        know: str,
        papers: int,
        candidates: Sequence[Candidate],
        threads: tuple[Thread, ...] | None = None,
    ) -> list[Found]:
        """`threads`: those the picks must cover; all of them unless given (a follow-up round: the gaps)."""
        request = PickRequest(
            topic=topic,
            reader_knows=know,
            papers=papers,
            brief=self._brief,
            threads=self.numbered() if threads is None else threads,
            candidates=self.shown(candidates),
        )
        chosen = await self._worker.run(self.PICK, request, ReadingList)
        known = {c.id: self._found.get(c.id, c) for c in candidates}
        picked: list[Found] = []
        for paper in chosen.papers:
            name = re.sub(r"v\d+$", "", paper.id.strip())
            if name in known and all(f.paper.id != name for f in picked):  # an invented id is dropped
                held = known[name]
                if paper.standing is not None:
                    held = held.model_copy(update={"standing": paper.standing})
                covers = self.thread(paper.covers)
                picked.append(Found(paper=held, why=paper.why, covers=covers, file=None))
        return picked[:papers]

    def thread(self, number: int | None) -> str:
        """The question a pick's thread number names; empty for none, or a number not listed. Picks named
        threads in their own words until 2026-11-12, which never equalled the plan's, so every thread was
        reported uncovered."""
        if number is None or not 1 <= number <= len(self._threads):
            return ""
        return self._threads[number - 1]

    async def check(
        self, topic: str, know: str, picked: Sequence[Found], out: Path
    ) -> tuple[tuple[CoveredThread, ...], list[str]]:
        """The threads the downloaded sources answer, each with a passage found in its source, and the
        follow-up queries for the rest."""
        texts: dict[str, str] = {}
        for found in picked:
            if found.file is not None:
                try:
                    texts[found.file] = await asyncio.to_thread(self._reader.read, out / found.file)
                except (OSError, RuntimeError):  # a file that gives no text answers nothing
                    continue
        titles = {f.file: f.paper.title for f in picked}
        request = CoverageRequest(
            topic=topic,
            reader_knows=know,
            brief=self._brief,
            threads=self.numbered(),
            sources=tuple(
                SourceExcerpt(file=file, title=titles[file], text=" ".join(text.split())[: self.EXCERPT])
                for file, text in texts.items()
            ),
        )
        answered = await self._worker.run(self.COVER, request, Coverage)
        covered: list[CoveredThread] = []
        queries: list[str] = []
        for answer in answered.threads:
            question = self.thread(answer.thread)
            if not question or any(c.thread == question for c in covered):
                continue
            quote = " ".join(answer.quote.split())
            if (
                answer.file in texts
                and len(quote) >= self.MIN_QUOTE
                and quote in " ".join(texts[answer.file].split())
            ):
                covered.append(CoveredThread(thread=question, file=answer.file, quote=quote))
            else:
                queries += answer.queries[: self.PER_GAP]
        return tuple(sorted(covered, key=lambda c: self._threads.index(c.thread))), queries

    async def download(self, picked: Sequence[Found], out: Path) -> list[Found]:
        out.mkdir(parents=True, exist_ok=True)
        done: list[Found] = []
        for found in picked:
            try:
                await asyncio.to_thread(self._search.download, found.paper, out / found.paper.download_name)
            except OSError:
                done.append(found)  # the user fetches it by hand
                continue
            described = await asyncio.to_thread(self._search.described, found.paper)
            done.append(found.model_copy(update={"paper": described, "file": found.paper.download_name}))
        return done

    async def find(self, topic: str, know: str, papers: int, out: Path) -> ReadingFolder:
        """Searches, picks and downloads into `out`, searches on for the threads not yet covered, and writes
        the reading list there."""
        picked = await self.download(
            await self.pick(topic, know, papers, await self.candidates(topic, know)), out
        )
        covered: tuple[CoveredThread, ...] = ()
        progress: list[int] = []
        for round in range(self.ROUNDS + 1) if self._threads else ():
            covered, queries = await self.check(topic, know, picked, out)
            progress.append(len(covered))
            stalled = len(progress) > 1 and progress[-1] <= progress[-2]
            if len(covered) == len(self._threads) or stalled or round == self.ROUNDS:
                break
            held = {c.thread for c in covered}
            gaps = tuple(t for t in self.numbered() if t.question not in held)
            more = await self.searched(queries)
            extra = await self.pick(topic, know, min(len(gaps), self.MORE), more, gaps) if more else []
            if not extra:
                break
            picked += await self.download(extra, out)
        folder = ReadingFolder(
            topic=topic,
            papers=tuple(picked),
            threads=self._threads,
            answered=covered,
            progress=tuple(progress),
        )
        folder.save(out)
        return folder
