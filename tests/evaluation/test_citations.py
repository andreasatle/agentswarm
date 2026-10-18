from pathlib import Path

from pydantic import BaseModel

from agentswarm.evaluation.citations import CitationAnswer, CitationAudit, CitationRequest, Support
from agentswarm.llm.workers import FakeWorker, Toolbox


def ruling(support: Support, page_right: bool | None = None):
    def step(toolbox: Toolbox | None) -> BaseModel:
        return CitationAnswer(reason="the source says so", support=support, page_right=page_right)

    return step


def level(tmp_path: Path) -> Path:
    for sub in ("sections", "sources"):
        (tmp_path / sub).mkdir(parents=True, exist_ok=True)
    (tmp_path / "sources" / "rep-p001.md").write_text(
        "# Rep, pages 1-3\n\n[page 1]\nIntro.\n\n[page 2]\nThe loss is L1.\n\n[page 3]\nResults.\n"
    )
    (tmp_path / "sections" / "01-a.md").write_text(
        "## A\n\nThe loss is L1 [@rep-p001, page 2].\nAn uncited line.\nIt beats all [@rep-p001].\n"
    )
    return tmp_path


class TestCitationAudit:
    def test_cited_lines_are_sampled_and_a_named_page_is_given_with_its_neighbours(
        self, tmp_path: Path
    ) -> None:
        audit = CitationAudit(level(tmp_path), FakeWorker([]))
        assert [s for _, s in audit.statements()] == [
            "The loss is L1 [@rep-p001, page 2].",
            "It beats all [@rep-p001].",
        ]
        source = audit.sources("The loss is L1 [@rep-p001, page 2].")[0]
        assert source.page == 2 and "[page 1]" in source.text and "[page 3]" in source.text

    async def test_each_statement_ruled_and_counted(self, tmp_path: Path) -> None:
        worker = FakeWorker([ruling(Support.SUPPORTED, True), ruling(Support.PARTLY)])
        audit = CitationAudit(level(tmp_path), worker)
        results = await audit.audit(audit.statements())
        assert isinstance(worker.calls[0][1], CitationRequest)
        text = CitationAudit.render(results)
        assert text.startswith("2 statements: 1 supported, 1 partly, 0 unsupported, 0 not ruled")
        assert "named page right 1 of 1" in text
