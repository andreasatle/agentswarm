import json
from pathlib import Path
from typing import ClassVar

import pytest
from pydantic import BaseModel

from agentswarm.evaluation.documents import DocumentImport
from agentswarm.evaluation.papers import (
    ArxivSearch,
    Candidate,
    Coverage,
    CoverageRequest,
    Found,
    OpenAlexSearch,
    OpenAlexWork,
    PageMetadata,
    PaperFinder,
    PickedPaper,
    PickRequest,
    QueryRequest,
    ReadingFolder,
    ReadingList,
    SearchQueries,
    Standing,
    TavilySearch,
    ThreadAnswer,
    WebPage,
    WikipediaSearch,
)
from agentswarm.llm.workers import Toolbox
from tests.support.fakes import ScriptedWorker

FEED = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/2305.20050v1</id>
    <published>2023-05-31T17:59:00Z</published>
    <title>Let's Verify
      Step by Step</title>
    <summary>  Process supervision beats outcome supervision.  </summary>
    <author><name>Hunter Lightman</name></author>
    <author><name>Vineet Kosaraju</name></author>
  </entry>
</feed>
"""


def paper(id: str, title: str = "A paper") -> Candidate:
    return Candidate(id=id, title=title, authors=("A. Author",), year=2024, abstract="About it.")


class FakeSearch:
    def __init__(
        self,
        results: dict[str, list[Candidate]],
        titles: dict[str, Candidate] | None = None,
        ignore_limit: bool = False,
    ) -> None:
        self.refuse: set[str] = {"broken"}
        self._results = results
        self._ignore_limit = ignore_limit
        self._titles = titles or {}
        self.downloaded: list[str] = []

    def search(self, query: str, limit: int) -> list[Candidate]:
        return self._results.get(query, [])[: None if self._ignore_limit else limit]

    def titled(self, title: str) -> Candidate | None:
        return self._titles.get(title)

    def download(self, paper: Candidate, path: Path) -> None:
        if paper.id in self.refuse:
            raise OSError("no such PDF")
        self.downloaded.append(paper.id)
        path.write_bytes(b"%PDF")

    def described(self, paper: Candidate) -> Candidate:
        return paper


class FakeReader:
    """A downloaded source's text: the file as written."""

    def read(self, path: Path) -> str:
        return path.read_text(errors="replace")


def finder(picked: list[str], search: FakeSearch) -> tuple[PaperFinder, list[PickRequest]]:
    asked: list[PickRequest] = []

    def pick(payload: BaseModel, toolbox: Toolbox | None) -> BaseModel:
        assert isinstance(payload, PickRequest)
        asked.append(payload)
        return ReadingList(papers=[PickedPaper(id=p, why=f"{p} adds this") for p in picked])

    worker = ScriptedWorker(
        {
            SearchQueries: lambda payload, toolbox: SearchQueries(
                queries=["verifiers", "search"], known=["Let's Verify Step by Step", "Invented"]
            ),
            ReadingList: pick,
        }
    )
    return PaperFinder(worker, search, reader=FakeReader()), asked


class TestArxivSearch:
    def test_an_atom_entry_becomes_a_candidate_without_its_version(self) -> None:
        [found] = ArxivSearch.parse(FEED)
        assert found.id == "2305.20050" and found.year == 2023
        assert found.title == "Let's Verify Step by Step"
        assert found.authors == ("Hunter Lightman", "Vineet Kosaraju")
        assert found.abstract == "Process supervision beats outcome supervision."
        assert found.venue == "arXiv preprint 2305.20050" and found.pdfs == (
            "https://arxiv.org/pdf/2305.20050",
        )


WORKS = b"""{"results": [
  {"id": "https://openalex.org/W2086162146", "title": "Sleep-dependent memory consolidation",
   "publication_year": 2005, "authorships": [{"author": {"display_name": "Matthew P. Walker"}}],
   "abstract_inverted_index": {"Sleep": [0], "consolidates": [1], "memory": [2]},
   "best_oa_location": {"pdf_url": "https://www.nature.com/articles/nature04286.pdf", "source": null},
   "primary_location": {"source": {"display_name": "Nature"}}, "cited_by_count": 1849,
   "open_access": {"oa_status": "green", "oa_url": "https://www.ncbi.nlm.nih.gov/pmc/articles/1"}},
  {"id": "https://openalex.org/W1", "title": "No open PDF", "publication_year": 2001, "authorships": [],
   "best_oa_location": {"pdf_url": null}, "primary_location": null}
]}"""


class TestOpenAlexSearch:
    def test_works_become_candidates_with_their_links(self) -> None:
        found, without = OpenAlexSearch.parse(WORKS)
        assert without.pdfs == ()  # still a candidate: a person may fetch it
        assert found.free == "https://www.ncbi.nlm.nih.gov/pmc/articles/1" and found.access == "green"
        assert found.id == "W2086162146" and found.year == 2005 and found.cited == 1849
        assert found.abstract == "Sleep consolidates memory" and found.venue == "Nature"
        assert found.reference("w2086162146").short == "Walker, 2005"

    def test_repository_links_come_before_the_journals(self) -> None:
        work = OpenAlexWork.model_validate(
            {
                "id": "https://openalex.org/W9",
                "best_oa_location": {"pdf_url": "https://journal/a.pdf", "source": {"type": "journal"}},
                "locations": [
                    {"pdf_url": "https://journal/a.pdf", "source": {"type": "journal"}},
                    {"pdf_url": "https://pmc/a.pdf", "source": {"type": "repository"}},
                    {"pdf_url": None},
                ],
            }
        )
        assert OpenAlexSearch.links(work) == ("https://pmc/a.pdf", "https://journal/a.pdf")

    def test_a_page_that_is_not_a_pdf_is_refused(self, tmp_path: Path) -> None:
        class Landing(OpenAlexSearch):
            def fetch(self, url: str) -> bytes:
                return b"<html>Please verify you are human</html>"

        with pytest.raises(OSError):
            Landing().download(
                paper("W1").model_copy(update={"pdfs": ("https://x/p.pdf",)}), tmp_path / "W1.pdf"
            )
        assert not (tmp_path / "W1.pdf").exists()


class TestCandidate:
    def test_the_reference_comes_from_the_record(self) -> None:
        r1 = Candidate(
            id="2501.12948",
            title="DeepSeek-R1",
            authors=("DeepSeek-AI", "Daya Guo", *(f"A{n} B{n}" for n in range(10))),
            year=2025,
            abstract="",
            venue="arXiv preprint 2501.12948",
        )
        reference = r1.reference("2501-12948")
        assert reference.year == "2025" and reference.short == "DeepSeek-AI et al., 2025"
        assert reference.authors.startswith("DeepSeek-AI, Guo, D., B0, A.") and reference.authors.endswith(
            "et al."
        )
        assert reference.venue == "arXiv preprint 2501.12948"
        two = Candidate(id="1", title="T", authors=("Ann Lee", "Bo Kim"), year=2020, abstract="")
        assert two.reference("1").short == "Lee and Kim, 2020"
        stray = Candidate(id="2", title="T", authors=("Steffen Gais", "Jan Born A"), year=2004, abstract="")
        assert stray.reference("2").short == "Gais and Born, 2004"  # OpenAlex's "Jan Born A"
        assert stray.reference("2").authors == "Gais, S., Born, J."

    def test_a_file_name_is_readable_plain_and_cut_at_a_word(self) -> None:
        replay = Candidate(
            id="W1",
            title="Replay and Time Compression of Recurring Spike Sequences in the Hippocampus",
            authors=("Zoltán Nádasdy",),
            year=1999,
            abstract="",
        )
        assert (
            replay.download_name
            == "nadasdy-1999-replay-and-time-compression-of-recurring-spike-sequences.pdf"
        )


class TestPaperFinder:
    async def test_candidates_from_every_query_are_pooled_once(self, tmp_path: Path) -> None:
        search = FakeSearch({"verifiers": [paper("1"), paper("2")], "search": [paper("2"), paper("3")]})
        found, asked = finder(["3", "1"], search)
        picked = await found.find("test-time compute", "ML", 5, tmp_path)
        assert [c.id for c in asked[0].candidates] == ["1", "2", "3"]
        assert [f.paper.id for f in picked.papers] == ["3", "1"]  # in the picker's order
        assert search.downloaded == ["3", "1"] and (tmp_path / paper("3").download_name).is_file()
        assert ReadingFolder.load(tmp_path) == picked

    async def test_known_titles_come_first_and_only_when_found(self, tmp_path: Path) -> None:
        verify = paper("2305.20050", "Let's Verify Step by Step")
        search = FakeSearch({"verifiers": [paper("1")]}, {"Let's Verify Step by Step": verify})
        found, asked = finder(["2305.20050"], search)
        await found.find("topic", "ML", 5, tmp_path)
        assert [c.id for c in asked[0].candidates] == ["2305.20050", "1"]

    async def test_the_candidates_are_capped_with_short_abstracts(self, tmp_path: Path) -> None:
        many = [paper(str(n)).model_copy(update={"abstract": "x" * 2000}) for n in range(60)]
        search = FakeSearch({"verifiers": many}, ignore_limit=True)  # arXiv's relevance pages, pooled
        found, asked = finder(["1"], search)
        await found.find("topic", "ML", 5, tmp_path)
        assert len(asked[0].candidates) == PaperFinder.CANDIDATES
        assert all(len(c.abstract) == PaperFinder.ABSTRACT for c in asked[0].candidates)

    async def test_a_paper_that_wont_download_is_still_picked_and_listed_for_the_user(
        self, tmp_path: Path
    ) -> None:
        gone = paper("W7", "Gone").model_copy(
            update={"pdfs": ("https://journal/x.pdf",), "page": "https://doi.org/1", "free": "https://pmc/1"}
        )
        search = FakeSearch({"verifiers": [gone, paper("1", "Here")]})
        search.refuse = {"W7"}
        found, asked = finder(["W7", "1"], search)
        picked = await found.find("topic", "ML", 5, tmp_path)
        assert (
            asked[0].candidates[0].pdfs == () and asked[0].candidates[0].page == ""
        )  # no links for the picker
        assert picked.papers[0].file is None and picked.papers[1].file == "author-2024-here.pdf"
        listing = (tmp_path / ReadingFolder.LISTING).read_text()
        assert "**Gone** (A. Author, 2024), W7: **not here yet**." in listing
        assert "- Gone: https://pmc/1, https://journal/x.pdf, https://doi.org/1" in listing
        (tmp_path / "x.full.pdf").write_text("x")  # fetched by hand, the publisher's name
        refreshed = picked.refreshed(
            [("x.full.pdf", "Journal 2024\nGone\nA. Author"), ("author-2024-here.pdf", "")]
        )
        assert [f.file for f in refreshed.papers] == ["x.full.pdf", "author-2024-here.pdf"]
        assert "not here yet" not in refreshed.render()

    async def test_invented_ids_and_repeats_are_dropped_and_the_count_kept(self, tmp_path: Path) -> None:
        search = FakeSearch({"verifiers": [paper("1"), paper("2"), paper("3")]})
        found, _ = finder(["9999.0001", "1v2", "1", "2", "3"], search)
        picked = await found.find("topic", "ML", 2, tmp_path)
        assert [f.paper.id for f in picked.papers] == ["1", "2"]

    def test_the_coverage_check_reads_a_page_without_its_navigation(self) -> None:
        page = (
            "Wide expert parallelism\n* [L](https://linkedin.com/share?u=x)\n[Technical Blog](https://n.com/blog)\n"
            "![](https://n.com/image.webp)\nDispatch sends each token to its experts' GPUs; see [DeepEP](https://x).\n"
        )
        assert PaperFinder.readable(page) == (
            "Wide expert parallelism Dispatch sends each token to its experts' GPUs; see [DeepEP](https://x)."
        )


class TestReadingFolder:
    def test_a_paper_removed_by_the_user_is_listed_as_removed_not_as_one_to_download(self) -> None:
        paper = Candidate(id="W1", title="A paper", authors=("A. Author",), year=2024, abstract="")
        folder = ReadingFolder(topic="t", papers=(Found(paper=paper, why="", file="a.pdf"),))
        removed = folder.marked("a.pdf", removed=True)
        assert "**removed by you**" in removed.render() and "Download these by hand" not in removed.render()
        assert removed.marked("a.pdf", removed=False) == folder

    def test_a_paper_fetched_by_hand_is_known_by_its_doi_or_most_of_its_title(self) -> None:
        rem = paper("W1", "REM Sleep, Prefrontal Theta, and the Consolidation of Human Emotional Memory")
        rem = rem.model_copy(update={"page": "https://doi.org/10.1093/cercor/bhn155"})
        flies = paper("W2", "Sleep and Synaptic Homeostasis: Structural Evidence in Drosophila")
        folder = ReadingFolder(
            topic="t", papers=(Found(paper=rem, why="", file=None), Found(paper=flies, why="", file=None))
        )
        two_columns = "Cerebral Cortex\nREM Sleep, Prefrontal Theta, and the Masaki Nishida, Jori Pearsall\n"
        two_columns += "Consolidation of Human Emotional\nMemory Sleep and Neuroimaging Laboratory"
        assert folder.match("bhn155.pdf", "doi:10.1093/cercor/bhn155\n" + two_columns) == folder.papers[0]
        assert folder.match("x.pdf", two_columns) == folder.papers[0]  # by its words, without the DOI
        assert folder.match("y.pdf", "Sleep homeostasis in mice, a review") is None
        planned = rem.download_name  # a name the finder would have given it, on some other document
        assert folder.match(planned, "An unrelated document") is None

    def test_a_page_that_names_another_files_paper_is_not_that_paper(self) -> None:
        book = paper("W1", "Theorem Proving in Lean 4")
        folder = ReadingFolder(topic="t", papers=(Found(paper=book, why="", file="book.txt"),))
        homepage = "Lean community\nRead Theorem Proving in Lean 4 to get started with mathlib."
        assert folder.match("community.html", homepage) is None
        assert folder.match("book.txt", homepage) == folder.papers[0]

    def test_a_refresh_keeps_each_paper_with_its_own_file_whatever_comes_first(self) -> None:
        book = paper("W1", "Theorem Proving in Lean 4")
        folder = ReadingFolder(topic="t", papers=(Found(paper=book, why="", file="zz-book.txt"),))
        homepage = "Lean community\nRead Theorem Proving in Lean 4 to get started with mathlib."
        refreshed = folder.refreshed(
            [("aa-community.html", homepage), ("zz-book.txt", "Theorem Proving in Lean 4")]
        )
        assert refreshed.papers[0].file == "zz-book.txt"


class TestDocumentFiles:
    def test_a_folder_stands_for_its_documents_without_the_reading_list(self, tmp_path: Path) -> None:
        for name in ("b.pdf", "a.pdf", "papers.md", "papers.json", "README.md", ".DS_Store"):
            (tmp_path / name).write_text("x")
        extra = tmp_path.parent / "extra.txt"
        assert DocumentImport.files([tmp_path, extra]) == [tmp_path / "a.pdf", tmp_path / "b.pdf", extra]

    def test_an_arxiv_record_beside_a_paper_becomes_its_reference_in_the_seed(self, tmp_path: Path) -> None:
        papers = tmp_path / "papers"
        papers.mkdir()
        (papers / "2501.12948.txt").write_text("DeepSeek-R1, a paper dated 2026 on its title page.")
        (papers / "other.txt").write_text("A paper without a record.")
        (papers / "9497.full.txt").write_text(
            "J. Neurosci. 1999\nReplay and Time Com-\npression of Spike Sequences"
        )
        record = Candidate(
            id="2501.12948", title="DeepSeek-R1", authors=("DeepSeek-AI",), year=2025, abstract=""
        )
        replay = Candidate(
            id="W1580901834",
            title="Replay and Time Compression of Spike Sequences",
            authors=(),
            year=1999,
            abstract="",
        )
        listed = (Found(paper=record, why="", file=None), Found(paper=replay, why="", file=None))
        ReadingFolder(topic="t", papers=listed).save(papers)
        files = DocumentImport.files([papers])
        assert [f.name for f in files] == ["2501.12948.txt", "9497.full.txt", "other.txt"]
        DocumentImport(files, tmp_path / "out", "A tutorial.", perspectives=(), approve=False).write()
        given = json.loads((tmp_path / "out" / "document" / "seed" / "docs" / "references.json").read_text())
        assert [(r["document"], r["year"]) for r in given] == [("2501-12948", "2025"), ("9497-full", "1999")]

    def test_the_template_models_load(self) -> None:
        assert "planner" in DocumentImport.models()


ARTICLES = b"""{"query": {"pages": [
  {"title": "Torcello", "index": 2, "touched": "2026-10-01T00:00:00Z", "extract": "Torcello is an island."},
  {"title": "Murano", "index": 1, "touched": "2026-09-30T00:00:00Z",
   "extract": "Murano  is a series of islands."},
  {"title": "Nowhere", "missing": true}
]}}"""


class TestWebShort:
    def test_a_web_citation_is_the_site_and_the_start_of_its_title(self) -> None:
        assert Candidate.web_short(
            "klarna.com", "Klarna AI assistant handles two-thirds of chats | Klarna"
        ) == ("klarna.com: Klarna AI assistant handles\u2026")
        assert Candidate.web_short("intercom.com", "Fin AI Agent Automation Rate | Intercom Help") == (
            "intercom.com: Fin AI Agent Automation\u2026"
        )
        assert Candidate.web_short("Wikipedia", "Murano") == "Wikipedia: Murano"
        assert Candidate.web_short("arxiv.org", "[2302.06590] The Impact of AI on Developers") == (
            "arxiv.org: The Impact of AI\u2026"
        )
        assert Candidate.web_short(
            "deloitte.com", "Hands Off the Task, Eyes on the Outcome | Deloitte US"
        ) == (
            "deloitte.com: Hands Off the Task\u2026"  # no comma before the ellipsis
        )
        assert Candidate.web_short("rework.com", '"AI Bank Reconciliation: How Matching Works"') == (
            "rework.com: AI Bank Reconciliation: How\u2026"
        )


class TestWikipediaSearch:
    def test_articles_in_search_order_cited_by_title_and_link(self) -> None:
        class Answering(WikipediaSearch):
            def fetch(self, url: str) -> bytes:
                return ARTICLES

        murano, torcello = Answering().search("venice islands", 5)
        assert (murano.title, torcello.title) == ("Murano", "Torcello")
        assert murano.abstract == "Murano is a series of islands." and murano.year == 2026
        assert murano.download_name == "wikipedia-murano.txt"
        reference = murano.reference("wikipedia-murano")
        assert (
            reference.short == "Wikipedia: Murano" and reference.url == "https://en.wikipedia.org/wiki/Murano"
        )

    def test_a_downloaded_article_is_its_title_and_text(self, tmp_path: Path) -> None:
        class Answering(WikipediaSearch):
            def fetch(self, url: str) -> bytes:
                page = b'{"title": "Murano", "extract": "Glass.\\n\\n== History ==\\nOld."}'
                return b'{"query": {"pages": [' + page + b"]}}"

        Answering().download(paper("x", "Murano"), tmp_path / "m.txt")
        assert (tmp_path / "m.txt").read_text() == "Murano\n\nGlass.\n\n== History ==\nOld.\n"


class TestWebPage:
    def test_a_named_page_is_cited_by_its_title_site_and_link(self) -> None:
        class Answering(WebPage):
            def fetch(self, url: str) -> bytes:
                title = b"<title>Venice and its Lagoon - UNESCO\n World Heritage</title>"
                return b"<html><head>" + title + b"</head></html>"

        page, data = Answering().record("https://whc.unesco.org/en/list/394/")
        assert page.title == "Venice and its Lagoon" and data.startswith(b"<html>")  # its site label dropped
        assert page.site == "whc.unesco.org" and page.download_name.endswith(".html")
        assert page.reference("x").url == "https://whc.unesco.org/en/list/394/"

    @staticmethod
    def pdf(info: bytes) -> bytes:
        """A one-page PDF with no text and these document properties."""
        objects = [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] >>",
            b"<< " + info + b" >>",
        ]
        body = b"%PDF-1.4\n"
        offsets: list[int] = []
        for n, obj in enumerate(objects, 1):
            offsets.append(len(body))
            body += f"{n} 0 obj\n".encode() + obj + b"\nendobj\n"
        xref = len(body)
        body += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
        body += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
        trailer = f"trailer << /Size {len(objects) + 1} /Root 1 0 R /Info 4 0 R >>\n"
        return body + (trailer + f"startxref\n{xref}\n%%EOF\n").encode()

    def test_a_named_pdf_is_kept_as_a_pdf_under_its_title(self) -> None:
        body = self.pdf(b"/Title (The Lean 4 Theorem Prover) /CreationDate (D:20210810122917)")

        class Paper(WebPage):
            def fetch(self, url: str) -> bytes:
                return body

        page, _ = Paper().record("https://lean-lang.org/papers/lean4.pdf")
        assert page.title == "The Lean 4 Theorem Prover" and page.download_name.endswith(".pdf")
        assert page.year == 2021  # the file's creation date: the paper names none

    def test_a_named_arxiv_pdf_is_cited_by_its_abstract_pages_authors(self) -> None:
        body = self.pdf(b"/CreationDate (D:20240312)")
        abstract = (
            b'<meta name="citation_title" content="Branch-Train-MiX: Mixing Expert LLMs" />'
            b'<meta name="citation_author" content="Sukhbaatar, Sainbayar" />'
            b'<meta name="citation_author" content="Golovneva, Olga" />'
        )

        class Arxiv(WebPage):
            asked: ClassVar[list[str]] = []

            def fetch(self, url: str) -> bytes:
                self.asked.append(url)
                return abstract if "/abs/" in url else body

        page, data = Arxiv().record("https://arxiv.org/pdf/2403.07816")
        assert data == body and page.kind == "pdf" and Arxiv.asked[-1] == "https://arxiv.org/abs/2403.07816"
        assert (page.title, page.authors, page.year) == (
            "Branch-Train-MiX: Mixing Expert LLMs",
            ("Sainbayar Sukhbaatar", "Olga Golovneva"),
            2024,
        )
        assert page.reference("btx").authors == "Sukhbaatar, S., Golovneva, O."
        assert WebPage.landing("https://lean-lang.org/papers/lean4.pdf") is None

    def test_a_blogs_schema_org_data_names_its_people_or_its_body_and_its_date(self) -> None:
        nvidia = (
            b'<meta property="og:site_name" content="NVIDIA Technical Blog" />'
            b'<script type="application/ld+json">{"@context":"https://schema.org","@graph":['
            b'{"@type":"Organization","name":"NVIDIA"},'
            b'{"@type":"Article","headline":"Dense vs. MoE Models",'
            b'"datePublished":"2026-09-15T17:00:11+00:00",'
            b'"author":{"@type":"Person","name":"Elizabeth Goodman"}}]}</script>'
        )
        said = WebPage.metadata("https://developer.nvidia.com/blog/dense-vs-moe", nvidia)
        assert (said.authors, said.year, said.title) == (("Elizabeth Goodman",), 2026, "Dense vs. MoE Models")
        honeyhive = (
            b'<script type="application/ld+json">{"@type":"Article","datePublished":"2026-11-19",'
            b'"author":{"@type":"Organization","name":"HoneyHive"}}</script>'
        )
        said = WebPage.metadata("https://www.honeyhive.ai/learn/moe", honeyhive)
        assert (said.authors, said.organisation, said.year) == ((), "HoneyHive", 2026)
        broken = b'<script type="application/ld+json">{not json</script><meta name="author" content="A. B" />'
        assert WebPage.metadata("https://x.org/p", broken).authors == ("A. B",)

    def test_a_team_named_by_open_graph_and_a_date_in_words_or_in_the_address(self) -> None:
        lmsys = (
            b'<meta property="article:published_time" content="May 5, 2025"/>'
            b'<meta property="article:author" content="The SGLang Team"/>'
            b'<meta property="article:author" content="https://facebook.com/someone"/>'
        )
        said = WebPage.metadata("https://www.lmsys.org/blog/large-scale-ep", lmsys)
        assert (said.authors, said.year) == (("The SGLang Team",), 2025)
        assert Candidate.initialled("The SGLang Team") == "The SGLang Team"  # a group, not "Team, T. S."
        assert Candidate.initialled("Leonardo de Moura") == "de Moura, L."  # not "Moura, L. d."
        assert Candidate.surname("Leonardo de Moura") == "de Moura"
        assert Candidate.initialled("SurfSense Team") == "SurfSense Team"  # a body, not "Team, S."

    def test_authors_given_as_one_string_are_listed_one_each(self) -> None:
        page = Candidate(
            id="web:x",
            title="Introducing reciprocal rank fusion",
            authors=("Ryan Bogan, Martin Gaievski, Minal Shah, Fanit Kolchina",),
            year=2025,
            abstract="",
            site="opensearch.org",
        )
        assert page.reference("x").authors == "Bogan, R., Gaievski, M., Shah, M., Kolchina, F."
        surname_first = page.model_copy(update={"authors": ("Bogan, Ryan",), "site": ""})
        assert surname_first.people() == ["Bogan, Ryan"]  # one name, surname first: not split
        assert (
            Candidate.web_title(
                "MoE-Inference-Bench: Performance Evaluation | Proceedings of the SC '25 Workshops of SC"
            )
            == "MoE-Inference-Bench: Performance Evaluation"
        )
        assert WebPage.published("https://www.lmsys.org/blog/2025-05-05-large-scale-ep", b"") == 2025
        assert WebPage.published("https://x.org/posts/2024/03/a-post", b"") == 2024
        assert WebPage.published("https://x.org/items/2024-99-a", b"") == 0

    def test_a_repository_is_cited_by_its_owner_under_its_own_title(self) -> None:
        github = b'<meta property="og:site_name" content="GitHub" /><meta property="og:title" content=' + (
            b'"GitHub - deepseek-ai/DeepEP: DeepEP: an efficient expert-parallel communication library" />'
        )

        class Repository(WebPage):
            def fetch(self, url: str) -> bytes:
                return github

        page, _ = Repository().record("https://github.com/deepseek-ai/DeepEP")
        assert page.title == "DeepEP: an efficient expert-parallel communication library"
        reference = page.reference("deepep")
        assert reference.authors == "deepseek-ai" and reference.year == "n.d."

    def test_a_paper_that_names_no_one_is_looked_up_in_the_catalogue_by_its_title(self) -> None:
        body = self.pdf(
            b"/Title (DeepSpeed-MoE: Advancing Mixture-of-Experts Inference) /CreationDate (D:2022)"
        )
        work = paper("W9").model_copy(
            update={
                "title": "DeepSpeed-MoE: Advancing Mixture-of-Experts Inference",
                "authors": ("Samyam Rajbhandari", "Conglong Li"),
                "year": 2022,
                "venue": "International Conference on Machine Learning",
            }
        )

        class Catalogue:
            def catalogued(self, title: str) -> Candidate | None:
                return work if title == work.title else None

        class Paper(WebPage):
            def fetch(self, url: str) -> bytes:
                return body

        page, _ = Paper(Catalogue()).record("https://proceedings.mlr.press/v162/rajbhandari22a.pdf")
        reference = page.reference("deepspeed")
        assert reference.authors == "Rajbhandari, S., Li, C." and reference.year == "2022"
        assert reference.venue == "International Conference on Machine Learning"
        page, _ = Paper().record("https://proceedings.mlr.press/v162/rajbhandari22a.pdf")  # no catalogue
        assert page.reference("deepspeed").authors == "proceedings.mlr.press"

    def test_a_pdf_title_that_is_a_file_name_gives_way_to_its_first_lines(self) -> None:
        word = self.pdf(b"/Title (Microsoft Word - A computer-checked proof.doc) /Author (gonthier)")
        assert WebPage.pdf_described("https://x.se/4col.pdf", word) == PageMetadata()  # no text: nothing said
        assert WebPage.first_title("HISTORY OF INTERACTIVE THEOREM\nPROVING\nJohn Harrison") == (
            "History of Interactive Theorem Proving"
        )
        assert WebPage.first_title("Propositions as Types \u2217\nPhilip Wadler") == "Propositions as Types"
        assert WebPage.first_title("Branch-Train-MiX:\nMixing Expert LLMs\nSainbayar Sukhbaatar") == (
            "Branch-Train-MiX: Mixing Expert LLMs"
        )

    def test_a_page_names_its_authors_year_and_title_in_its_citation_metadata(self) -> None:
        page = (
            b"<html><head><title>Principia Mathematica (Stanford Encyclopedia of Philosophy)</title>"
            b'<meta name="citation_title" content="Principia Mathematica" />'
            b'<meta name="citation_author" content="Irvine, Andrew David" />'
            b'<meta content="1996/05/21" name="citation_publication_date" />'
            b'<meta name="DC.creator" content="Someone Else" /></head></html>'
        )
        said = WebPage.metadata("https://plato.stanford.edu/entries/principia-mathematica", page)
        assert said == PageMetadata(
            title="Principia Mathematica", authors=("Andrew David Irvine",), year=1996
        )
        assert WebPage.metadata(
            "https://x.net", b'<meta name="DC.creator" content="Irvine, A. D.">'
        ).authors == ("A. D. Irvine",)

    def test_a_named_source_is_dated_by_its_own_metadata_not_the_day_it_was_fetched(self) -> None:
        nature = b'<meta name="dc.date" content="2023-12-14"/>'
        nature += b'<meta name="citation_publication_date" content="2024/01"/>'
        assert WebPage.published("https://www.nature.com/articles/x", nature) == 2024
        assert (
            WebPage.published("https://x.net/post", b'<meta itemprop="datePublished" content="2025-07-15"/>')
            == 2025
        )
        assert WebPage.published("https://arxiv.org/pdf/2310.06770", b"") == 2023
        assert WebPage.published("https://lean-lang.org/papers/lean4.pdf", b"") == 0


class Answering(TavilySearch):
    """Tavily with canned answers: what it was asked, and no network."""

    def __init__(self, answers: dict[str, bytes]) -> None:
        super().__init__(key="test-key")
        self.asked: list[tuple[str, BaseModel]] = []
        self._answers = answers

    def post(self, path: str, body: BaseModel) -> bytes:
        self.asked.append((path, body))
        return self._answers[path]


class TestTavilySearch:
    RESULTS = json.dumps(
        {
            "results": [
                {
                    "url": "https://actv.avmspa.it/en/tickets",
                    "title": "ACTV  Tickets",
                    "content": "Fares  and passes.",
                    "score": 0.9,
                    "raw_content": None,
                },
                {
                    "url": "https://www.example-blog.com/venice",
                    "title": "Venice tips",
                    "content": "Go early.",
                    "published_date": "2025-04-01",
                    "score": 0.5,
                },
            ],
            "answer": None,
        }
    ).encode()

    def test_pages_are_cited_by_their_site_title_and_link(self) -> None:
        search = Answering({"/search": self.RESULTS})
        actv, blog = search.search("venice vaporetto tickets", 30)
        path, body = search.asked[0]
        assert path == "/search" and body.model_dump()["max_results"] == 20  # the service's most
        assert (actv.id, actv.title, actv.site, actv.year) == (
            "web:https://actv.avmspa.it/en/tickets",
            "ACTV Tickets",
            "actv.avmspa.it",
            0,  # no date given: none made up
        )
        assert actv.abstract == "Fares and passes." and actv.kind == "txt" and blog.year == 2025
        assert blog.site == "example-blog.com" and search.titled("ACTV Tickets") is None
        reference = actv.model_copy(update={"standing": Standing.OFFICIAL}).reference("actv")
        assert reference.venue == "actv.avmspa.it (official)" and reference.url == actv.page
        assert reference.year == "n.d."

    def test_a_page_downloads_as_its_title_link_and_text(self, tmp_path: Path) -> None:
        text = (
            b'{"results": [{"url": "https://actv.avmspa.it/en/tickets", "raw_content": "A 24-hour pass..."}]}'
        )
        page = Answering({"/search": self.RESULTS, "/extract": text}).search("q", 1)[0]
        Answering({"/extract": text}).download(page, tmp_path / "p.txt")
        assert (
            tmp_path / "p.txt"
        ).read_text() == "ACTV Tickets\nhttps://actv.avmspa.it/en/tickets\n\nA 24-hour pass...\n"
        with pytest.raises(OSError, match="no text"):
            Answering({"/extract": b'{"results": [], "failed_results": [{"url": "x"}]}'}).download(
                page, tmp_path / "q.txt"
            )

    def test_an_arxiv_page_is_dated_by_its_id(self) -> None:
        answer = json.dumps(
            {
                "results": [
                    {"url": "https://arxiv.org/abs/2001.08361", "title": "Scaling Laws", "content": "Loss."}
                ]
            }
        ).encode()
        (kaplan,) = Answering({"/search": answer}).search("scaling laws", 3)
        assert kaplan.year == 2020
        assert kaplan.kind == "pdf" and kaplan.pdfs == (
            "https://arxiv.org/pdf/2001.08361",
        )  # the paper itself

    def test_a_kept_page_is_described_by_its_own_metadata_when_it_has_any(self) -> None:
        class Pages(Answering):
            def fetch(self, url: str) -> bytes:
                if "refused" in url:
                    raise OSError("403")
                author = b'<meta property="og:title" content="Hilbert\'s Program | SEP" />'
                author += b'<meta name="citation_author" content="Zach, Richard">'
                return author + b'<meta name="citation_publication_date" content="2003/07/31">'

        search = Pages({})
        page = Candidate(
            id="web:x",
            title="Hilbert's Program",
            authors=("plato.stanford.edu",),
            year=0,
            abstract="",
            site="plato.stanford.edu",
            page="https://plato.stanford.edu/entries/hilbert-program",
        )
        zach = search.described(page)
        assert (zach.title, zach.authors, zach.year) == ("Hilbert's Program", ("Richard Zach",), 2003)
        reference = zach.reference("hilbert")
        assert (reference.authors, reference.year) == ("Zach, R.", "2003")
        assert reference.short == "plato.stanford.edu: Hilbert's Program"
        refused = page.model_copy(update={"page": "https://refused.org/x"})
        assert search.described(refused) == refused

    def test_a_paper_whose_site_refuses_programs_is_found_in_the_catalogue_by_its_title(self) -> None:
        work = paper("W1", "MoE-Inference-Bench: Performance Evaluation").model_copy(
            update={"authors": ("Krishna Teja Chitty-Venkata",), "year": 2025, "venue": "SC"}
        )

        class Catalogue:
            def catalogued(self, title: str) -> Candidate | None:
                return work if title == work.title else None

        class Refusing(Answering):
            def __init__(self) -> None:
                super().__init__({})
                self._catalogue = Catalogue()

            def fetch(self, url: str) -> bytes:
                raise OSError("403")

        found = Candidate(
            id="web:acm",
            title="MoE-Inference-Bench: Performance Evaluation | Proceedings of the SC '25 Workshops",
            authors=("dl.acm.org",),
            year=0,
            abstract="",
            site="dl.acm.org",
            page="https://dl.acm.org/doi/10.1145/3731599.3767706",
        )
        reference = Refusing().described(found).reference("bench")
        assert (reference.authors, reference.year, reference.venue) == ("Chitty-Venkata, K. T.", "2025", "SC")
        assert reference.title == "MoE-Inference-Bench: Performance Evaluation"

    def test_a_title_loses_the_tags_and_site_label_the_search_gives_it(self) -> None:
        assert Candidate.web_title("Propositions as Types - Informatics Homepages Server") == (
            "Propositions as Types"
        )
        assert Candidate.web_title(
            "[PDF] A computer-checked proof of the Four Colour Theorem 1 The story"
        ) == ("A computer-checked proof of the Four Colour Theorem 1 The story")
        assert Candidate.web_title(
            "N.G. de Bruijn (1918\u20132012) and his Road to Automath ... - SciSpace"
        ) == ("N.G. de Bruijn (1918\u20132012) and his Road to Automath ...")
        long = "Gödel and the limits of logic - what the incompleteness theorems mean for mathematics"
        assert Candidate.web_title(long) == long  # a long tail is the title's own

    def test_without_a_key_it_says_which_one_is_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(TavilySearch.KEY, raising=False)
        with pytest.raises(OSError, match="TAVILY_API_KEY"):
            TavilySearch().search("venice", 3)


class TestThreads:
    async def test_the_picks_cover_the_threads_and_say_what_kind_of_site_they_are(
        self, tmp_path: Path
    ) -> None:
        asked: list[PickRequest] = []

        def pick(payload: BaseModel, toolbox: Toolbox | None) -> BaseModel:
            assert isinstance(payload, PickRequest)
            asked.append(payload)
            return ReadingList(
                papers=[PickedPaper(id="1", why="fares", covers=1, standing=Standing.OFFICIAL)]
            )

        worker = ScriptedWorker(
            {
                SearchQueries: lambda payload, toolbox: SearchQueries(
                    threads=["getting around", "what to see"], queries=["vaporetto"]
                ),
                ReadingList: pick,
                Coverage: lambda payload, toolbox: Coverage(threads=[]),
            }
        )
        search = FakeSearch({"vaporetto": [paper("1"), paper("2")]})
        folder = await PaperFinder(worker, search, queries=8, reader=FakeReader()).find(
            "Venice", "a first-time visitor", 5, tmp_path
        )
        assert [(t.number, t.question) for t in asked[0].threads] == [
            (1, "getting around"),
            (2, "what to see"),
        ]
        assert asked[0].brief == ""  # no brief of the user's: the plan works from the topic
        (found,) = folder.papers
        assert (found.covers, found.paper.standing) == ("getting around", Standing.OFFICIAL)
        listing = folder.render()
        assert "(official)" in listing and "Covers: getting around" in listing
        # the picker's word isn't enough: covered only with a passage the check finds in the source
        assert "**Threads no source covers:** getting around; what to see" in listing
        undated = folder.model_copy(
            update={
                "papers": (found.model_copy(update={"paper": found.paper.model_copy(update={"year": 0})}),)
            }
        )
        assert "(A. Author, n.d.)" in undated.render()

    async def test_the_threads_the_user_approved_are_searched_for_as_they_are(self, tmp_path: Path) -> None:
        planned: list[QueryRequest] = []

        def plan(payload: BaseModel, toolbox: Toolbox | None) -> BaseModel:
            assert isinstance(payload, QueryRequest)
            planned.append(payload)
            return SearchQueries(threads=["a reworded question"], queries=["vaporetto"])

        def pick(payload: BaseModel, toolbox: Toolbox | None) -> BaseModel:
            assert isinstance(payload, PickRequest)
            assert [t.question for t in payload.threads] == ["what it costs"]
            return ReadingList(papers=[PickedPaper(id="1", why="fares", covers=1)])

        worker = ScriptedWorker(
            {SearchQueries: plan, ReadingList: pick, Coverage: lambda p, t: Coverage(threads=[])}
        )
        finder = PaperFinder(
            worker, FakeSearch({"vaporetto": [paper("1")]}), reader=FakeReader(), threads=("what it costs",)
        )
        folder = await finder.find("Venice", "a visitor", 5, tmp_path)
        assert planned[0].threads == ("what it costs",) and folder.threads == ("what it costs",)


class TestBrief:
    async def test_the_users_brief_reaches_the_plan_and_the_picker(self, tmp_path: Path) -> None:
        planned: list[BaseModel] = []

        def plan(payload: BaseModel, toolbox: Toolbox | None) -> BaseModel:
            planned.append(payload)
            return SearchQueries(threads=["costs"], queries=["vaporetto"])

        def pick(payload: BaseModel, toolbox: Toolbox | None) -> BaseModel:
            planned.append(payload)
            return ReadingList(papers=[PickedPaper(id="1", why="costs", covers=1)])

        def check(payload: BaseModel, toolbox: Toolbox | None) -> BaseModel:
            planned.append(payload)
            return Coverage(threads=[])

        worker = ScriptedWorker({SearchQueries: plan, ReadingList: pick, Coverage: check})
        brief = "Cover what scaling costs and who pays."
        finder = PaperFinder(
            worker, FakeSearch({"vaporetto": [paper("1")]}), brief=brief, reader=FakeReader()
        )
        await finder.find("LLM scaling", "ML", 5, tmp_path)
        query, picking, checking = planned
        assert isinstance(query, QueryRequest) and query.brief == brief
        assert isinstance(picking, PickRequest) and picking.brief == brief
        assert isinstance(checking, CoverageRequest) and checking.brief == brief


class TextSearch(FakeSearch):
    """Downloads each page as its text."""

    def __init__(
        self,
        results: dict[str, list[Candidate]],
        texts: dict[str, str],
        titles: dict[str, Candidate] | None = None,
    ) -> None:
        super().__init__(results, titles)
        self._texts = texts

    def download(self, paper: Candidate, path: Path) -> None:
        self.downloaded.append(paper.id)
        path.write_text(self._texts[paper.id])


class TestFollowUp:
    TEXTS: ClassVar[dict[str, str]] = {
        "fares": "A single vaporetto ticket costs 9.50 euros and lasts 75 minutes.",
        "museums": "The Doge's Palace is open daily from 9 to 18, and the Accademia closes on Mondays.",
    }

    def worker(
        self, coverage: list[Coverage], asked: list[BaseModel]
    ) -> tuple[ScriptedWorker, list[PickRequest]]:
        picks: list[PickRequest] = []

        def pick(payload: BaseModel, toolbox: Toolbox | None) -> BaseModel:
            assert isinstance(payload, PickRequest)
            picks.append(payload)
            return ReadingList(papers=[PickedPaper(id=c.id, why="it answers") for c in payload.candidates])

        def check(payload: BaseModel, toolbox: Toolbox | None) -> BaseModel:
            asked.append(payload)
            return coverage[len(asked) - 1]

        plan = SearchQueries(threads=["what it costs", "what to see"], queries=["vaporetto"])
        return ScriptedWorker(
            {SearchQueries: lambda payload, toolbox: plan, ReadingList: pick, Coverage: check}
        ), picks

    async def test_an_uncovered_thread_is_searched_again_and_picked_for_alone(self, tmp_path: Path) -> None:
        asked: list[BaseModel] = []
        fares, museums = paper("fares", "Fares"), paper("museums", "Museums")
        coverage = [
            Coverage(
                threads=[
                    ThreadAnswer(thread=1, file="author-2024-fares.pdf", quote="costs 9.50 euros and lasts"),
                    ThreadAnswer(thread=2, queries=["venice museum hours"]),
                ]
            ),
            Coverage(
                threads=[
                    ThreadAnswer(thread=1, file="author-2024-fares.pdf", quote="costs 9.50 euros and lasts"),
                    ThreadAnswer(
                        thread=2, file="author-2024-museums.pdf", quote="the Accademia closes on Mondays"
                    ),
                ]
            ),
        ]
        worker, picks = self.worker(coverage, asked)
        search = TextSearch({"vaporetto": [fares], "venice museum hours": [fares, museums]}, self.TEXTS)
        folder = await PaperFinder(worker, search, reader=FakeReader()).find(
            "Venice", "a visitor", 5, tmp_path
        )
        first, _ = asked
        assert isinstance(first, CoverageRequest)
        assert [s.text for s in first.sources] == [self.TEXTS["fares"]]
        follow = picks[1]  # only the new page, for the thread still open
        assert [c.id for c in follow.candidates] == ["museums"] and follow.papers == 1
        assert [(t.number, t.question) for t in follow.threads] == [(2, "what to see")]
        assert [f.paper.id for f in folder.papers] == ["fares", "museums"]
        assert folder.progress == (1, 2)
        listing = folder.render()
        assert "1 of 2 after the first search, 2 after 1 more round." in listing
        assert "no source covers" not in listing

    async def test_a_passage_not_in_the_source_covers_nothing_and_a_round_that_adds_nothing_ends_it(
        self, tmp_path: Path
    ) -> None:
        asked: list[BaseModel] = []
        invented = ThreadAnswer(thread=1, file="author-2024-fares.pdf", quote="tickets are free on Sundays")
        coverage = [
            Coverage(threads=[invented, ThreadAnswer(thread=2, queries=["venice museum hours"])]),
            Coverage(threads=[invented, ThreadAnswer(thread=2, queries=["venice sights"])]),
        ]
        worker, _ = self.worker(coverage, asked)
        search = TextSearch(
            {"vaporetto": [paper("fares")], "venice museum hours": [paper("museums")]}, self.TEXTS
        )
        folder = await PaperFinder(worker, search, reader=FakeReader()).find(
            "Venice", "a visitor", 5, tmp_path
        )
        assert len(asked) == 2 and folder.progress == (0, 0)  # stalled: no third check, no third search
        assert folder.answered == ()
        assert "**Threads no source covers:** what it costs; what to see" in folder.render()


class TestKnownTitles:
    """The web has no catalogue of titles, so the planner's well-known papers are looked up in the scholarly
    indexes (2026-11-26: two runs where 11 such papers were added by hand)."""

    class Down(FakeSearch):
        def titled(self, title: str) -> Candidate | None:
            raise OSError("index down")

    def test_a_title_is_looked_up_in_the_first_index_that_has_it(self) -> None:
        ragas = paper("2309.15217", "Ragas")
        later = FakeSearch({}, {"Ragas": paper("W1", "Ragas"), "ARES": paper("W2", "ARES")})
        web = TavilySearch(papers=(self.Down({}), FakeSearch({}, {"Ragas": ragas}), later))
        assert web.titled("Ragas") == ragas  # an index that is down is passed over
        assert web.titled("ARES") == paper("W2", "ARES") and web.titled("Unknown") is None
        assert TavilySearch().titled("Ragas") is None

    def test_an_index_paper_downloads_as_its_pdf_and_keeps_its_record(self, tmp_path: Path) -> None:
        class Fetched(TavilySearch):
            def fetch(self, url: str) -> bytes:
                return b"%PDF-1.5" if url == "https://arxiv.org/pdf/2309.15217" else b""

        ragas = paper("2309.15217", "Ragas").model_copy(
            update={"pdfs": ("https://arxiv.org/pdf/2309.15217",)}
        )
        web = Fetched()
        web.download(ragas, tmp_path / "ragas.pdf")
        assert (tmp_path / "ragas.pdf").read_bytes() == b"%PDF-1.5" and web.described(ragas) == ragas
        with pytest.raises(OSError):  # a closed paper: listed for the user, never extracted as a page
            web.download(paper("W9", "Closed"), tmp_path / "closed.pdf")

    async def test_a_paper_found_by_title_and_by_its_web_page_is_one_candidate(self, tmp_path: Path) -> None:
        pdf = ("https://arxiv.org/pdf/2305.20050",)
        verify = paper("2305.20050", "Let's Verify Step by Step").model_copy(update={"pdfs": pdf})
        page = paper("web:https://arxiv.org/abs/2305.20050", "Let's Verify").model_copy(update={"pdfs": pdf})
        search = FakeSearch({"verifiers": [page, paper("1")]}, {"Let's Verify Step by Step": verify})
        found, asked = finder(["2305.20050"], search)
        await found.find("topic", "ML", 5, tmp_path)
        assert [c.id for c in asked[0].candidates] == ["2305.20050", "1"]

    async def test_a_follow_up_round_looks_up_the_titles_named_for_a_gap(self, tmp_path: Path) -> None:
        asked: list[BaseModel] = []
        texts = {
            **TestFollowUp.TEXTS,
            "2410.21819": "Self-preference bias makes a judge favour its own text.",
        }
        quote = "costs 9.50 euros and lasts"
        coverage = [
            Coverage(
                threads=[
                    ThreadAnswer(thread=1, file="author-2024-fares.pdf", quote=quote),
                    ThreadAnswer(thread=2, queries=["judge bias"], known=["Self-Preference Bias"]),
                ]
            ),
            Coverage(
                threads=[
                    ThreadAnswer(thread=1, file="author-2024-fares.pdf", quote=quote),
                    ThreadAnswer(
                        thread=2,
                        file="author-2024-self-preference-bias.pdf",
                        quote="makes a judge favour its own",
                    ),
                ]
            ),
        ]
        worker, picks = TestFollowUp().worker(coverage, asked)
        bias = paper("2410.21819", "Self-Preference Bias")
        search = TextSearch({"vaporetto": [paper("fares", "Fares")]}, texts, {"Self-Preference Bias": bias})
        folder = await PaperFinder(worker, search, reader=FakeReader()).find("judges", "ML", 5, tmp_path)
        assert [c.id for c in picks[1].candidates] == ["2410.21819"]  # found by its title, not the query
        assert folder.progress == (1, 2)
