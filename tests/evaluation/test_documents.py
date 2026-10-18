from pathlib import Path

from agentswarm.core.config import ProjectConfig
from agentswarm.evaluation.documents import DocumentImport, SourceText


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
