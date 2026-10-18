import json
from pathlib import Path

import pytest
from pydantic import BaseModel

from agentswarm.evaluation.documents import DocumentImport
from agentswarm.evaluation.papers import (
    ArxivSearch,
    Candidate,
    Found,
    OpenAlexSearch,
    OpenAlexWork,
    PaperFinder,
    PickedPaper,
    PickRequest,
    ReadingFolder,
    ReadingList,
    SearchQueries,
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
    return PaperFinder(worker, search), asked


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

    def test_a_file_name_is_readable_plain_and_cut_at_a_word(self) -> None:
        replay = Candidate(
            id="W1",
            title="Replay and Time Compression of Recurring Spike Sequences in the Hippocampus",
            authors=("Zoltán Nádasdy",),
            year=1999,
            abstract="",
        )
        assert replay.file == "nadasdy-1999-replay-and-time-compression-of-recurring-spike-sequences.pdf"


class TestPaperFinder:
    async def test_candidates_from_every_query_are_pooled_once(self, tmp_path: Path) -> None:
        search = FakeSearch({"verifiers": [paper("1"), paper("2")], "search": [paper("2"), paper("3")]})
        found, asked = finder(["3", "1"], search)
        picked = await found.find("test-time compute", "ML", 5, tmp_path)
        assert [c.id for c in asked[0].candidates] == ["1", "2", "3"]
        assert [f.paper.id for f in picked.papers] == ["3", "1"]  # in the picker's order
        assert search.downloaded == ["3", "1"] and (tmp_path / paper("3").file).is_file()
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
