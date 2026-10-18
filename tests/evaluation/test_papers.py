from pathlib import Path

from pydantic import BaseModel

from agentswarm.evaluation.documents import DocumentImport
from agentswarm.evaluation.papers import (
    ArxivSearch,
    Candidate,
    PaperFinder,
    PickedPaper,
    PickRequest,
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
        self, results: dict[str, list[Candidate]], titles: dict[str, Candidate] | None = None
    ) -> None:
        self._results = results
        self._titles = titles or {}
        self.downloaded: list[str] = []

    def search(self, query: str, limit: int) -> list[Candidate]:
        return self._results.get(query, [])[:limit]

    def titled(self, title: str) -> Candidate | None:
        return self._titles.get(title)

    def download(self, paper: str, path: Path) -> None:
        if paper == "broken":
            raise OSError("no such PDF")
        self.downloaded.append(paper)
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


class TestPaperFinder:
    async def test_candidates_from_every_query_are_pooled_once(self, tmp_path: Path) -> None:
        search = FakeSearch({"verifiers": [paper("1"), paper("2")], "search": [paper("2"), paper("3")]})
        found, asked = finder(["3", "1"], search)
        picked = await found.find("test-time compute", "ML", 5, tmp_path)
        assert [c.id for c in asked[0].candidates] == ["1", "2", "3"]
        assert [f.paper.id for f in picked] == ["3", "1"]  # in the picker's order
        assert search.downloaded == ["3", "1"] and (tmp_path / "3.pdf").is_file()

    async def test_known_titles_come_first_and_only_when_found(self, tmp_path: Path) -> None:
        verify = paper("2305.20050", "Let's Verify Step by Step")
        search = FakeSearch({"verifiers": [paper("1")]}, {"Let's Verify Step by Step": verify})
        found, asked = finder(["2305.20050"], search)
        await found.find("topic", "ML", 5, tmp_path)
        assert [c.id for c in asked[0].candidates] == ["2305.20050", "1"]

    async def test_invented_ids_and_repeats_are_dropped_and_the_count_kept(self, tmp_path: Path) -> None:
        search = FakeSearch({"verifiers": [paper("1"), paper("2"), paper("3")]})
        found, _ = finder(["9999.0001", "1v2", "1", "2", "3"], search)
        picked = await found.find("topic", "ML", 2, tmp_path)
        assert [f.paper.id for f in picked] == ["1", "2"]

    async def test_a_failed_download_is_listed_as_failed(self, tmp_path: Path) -> None:
        search = FakeSearch({"verifiers": [paper("broken", "Gone"), paper("1", "Here")]})
        found, _ = finder(["broken", "1"], search)
        picked = await found.find("topic", "ML", 5, tmp_path)
        assert picked[0].file is None and picked[1].file == tmp_path / "1.pdf"
        listing = (tmp_path / "papers.md").read_text()
        assert "**Gone**" in listing and "download failed" in listing and "`1.pdf`" in listing


class TestDocumentFiles:
    def test_a_folder_stands_for_its_documents_without_the_reading_list(self, tmp_path: Path) -> None:
        for name in ("b.pdf", "a.pdf", "papers.md", "README.md", ".DS_Store"):
            (tmp_path / name).write_text("x")
        extra = tmp_path.parent / "extra.txt"
        assert DocumentImport.files([tmp_path, extra]) == [tmp_path / "a.pdf", tmp_path / "b.pdf", extra]

    def test_the_template_models_load(self) -> None:
        assert "planner" in DocumentImport.models()
