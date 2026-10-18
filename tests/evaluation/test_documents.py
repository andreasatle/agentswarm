from pathlib import Path

from agentswarm.core.config import ProjectConfig
from agentswarm.evaluation.documents import DocumentImport, SourceParts, SourceText


class TestDocumentImport:
    def test_each_audience_gets_a_project_over_the_same_sources(self, tmp_path: Path) -> None:
        (tmp_path / "Q3 Report.txt").write_text("Revenue rose 4%.\n")
        (tmp_path / "plan.md").write_text("# Plan\n\nHire two engineers.\n")
        out = tmp_path / "out"
        summary = DocumentImport(
            [tmp_path / "Q3 Report.txt", tmp_path / "plan.md"],
            out,
            "A summary.",
            ["managers", "engineers"],
            800,
        ).write()
        assert summary.sources == ("q3-report", "plan")
        assert [p.parent.name for p in summary.projects] == ["managers", "engineers"]
        for project in summary.projects:
            config = ProjectConfig.load(project)
            assert config.brief is not None and config.brief.words == 800
            assert config.seed == project.parent / "seed"
            assert "For this audience" in config.brief.goal
        source = (out / "managers" / "seed" / "sources" / "q3-report.md").read_text()
        assert source == "# Q3 Report\n\nRevenue rose 4%.\n"
        assert "WORDS = 800" in (out / "engineers" / "seed" / "tools" / "check_doc.py").read_text()

    def test_without_an_audience_one_project(self, tmp_path: Path) -> None:
        (tmp_path / "a.txt").write_text("text\n")
        summary = DocumentImport([tmp_path / "a.txt"], tmp_path / "out", "A summary.").write()
        assert [p.parent.name for p in summary.projects] == ["document"]

    def test_names_become_source_slugs(self) -> None:
        assert SourceText.slug("Annual Report 2025 (final)") == "annual-report-2025-final"


class TestSourceParts:
    def test_a_pdf_is_cut_at_pages_and_each_page_is_marked(self) -> None:
        text = "\f".join(["one two three", "four five", "six seven eight", "nine"])
        parts = SourceParts.split("report", "Report", text, size=4)
        assert [n for n, _ in parts] == ["report-p001", "report-p003"]
        first = parts[0][1]
        assert first.startswith("# Report, pages 1-2\n") and "[page 2]\nfour five" in first

    def test_other_text_is_cut_at_blank_lines(self) -> None:
        parts = SourceParts.split("notes", "Notes", "a b c\n\nd e f\n\ng h", size=4)
        assert [(n, t.split("\n", 1)[0]) for n, t in parts] == [
            ("notes-01", "# Notes, part 1 of 3"),
            ("notes-02", "# Notes, part 2 of 3"),
            ("notes-03", "# Notes, part 3 of 3"),
        ]

    def test_a_short_document_stays_whole(self) -> None:
        assert SourceParts.split("memo", "Memo", "short text") == [("memo", "# Memo\n\nshort text\n")]
