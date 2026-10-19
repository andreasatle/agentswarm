from pathlib import Path

from agentswarm.core.config import ProjectConfig
from agentswarm.core.domain import Reference
from agentswarm.evaluation.documents import DocumentAssembly, DocumentImport, SourceParts, SourceText


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


class TestOptions:
    def test_a_tutorial_project_without_reviews_runs_straight_through_on_its_budget(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "paper.txt").write_text("Diffusion adds noise step by step.\n")
        summary = DocumentImport(
            [tmp_path / "paper.txt"], tmp_path / "out", "A tutorial.", (), 4000, (), False, 0.8, "diffusion"
        ).write()
        config = ProjectConfig.load(summary.projects[0])
        assert summary.projects[0].parent.name == "diffusion"
        assert config.review is None and config.brief is not None and not config.brief.approve
        assert config.budgets.usd_per_day == 0.8

    def test_reviews_by_the_perspectives_asked_for(self, tmp_path: Path) -> None:
        (tmp_path / "paper.txt").write_text("text\n")
        summary = DocumentImport(
            [tmp_path / "paper.txt"], tmp_path / "out", "A tutorial.", (), 4000, ("reader", "facts")
        ).write()
        review = ProjectConfig.load(summary.projects[0]).review
        assert review is not None and [str(p) for p in review.perspectives] == ["reader", "facts"]


class TestDocumentAssembly:
    def test_a_reference_named_by_the_whole_file_name_owns_the_shortened_parts(self) -> None:
        reference = Reference(
            document="bricken-2023-towards-monosemanticity",
            authors="B",
            title="T",
            year="2023",
            short="B, 2023",
        )
        assert DocumentAssembly.owner("bricken-2023-towards-monoseman-01", [reference]) == reference
        assert DocumentAssembly.owner("bricken-2024-other-01", [reference]) is None

    def test_sections_in_order_under_the_briefs_title_with_the_sources_cited(self, tmp_path: Path) -> None:
        level = tmp_path / "level"
        (level / "sections").mkdir(parents=True)
        (level / "sources").mkdir()
        (level / "docs").mkdir()
        (level / "docs" / "brief.md").write_text("# The brief: Diffusion for beginners\n\n## Audience\n")
        (level / "sections" / "02-b.md").write_text("## Two\n\nLater [@paper-p003, page 4].\n")
        (level / "sections" / "01-a.md").write_text("## One\n\nFirst [@paper-p001].\n")
        (level / "sources" / "paper-p001.md").write_text("# Paper, pages 1-2\n\n...")
        (level / "sources" / "paper-p003.md").write_text("# Paper, pages 3-5\n\n...")
        out = DocumentAssembly.write(level, tmp_path / "document.md")
        assert out is not None
        text = out.read_text()
        assert text.startswith("# Diffusion for beginners\n\n## One\n")
        assert text.index("## One") < text.index("## Two")
        assert "- `paper-p001`: Paper, pages 1-2" in text and "- `paper-p003`: Paper, pages 3-5" in text


class TestReferences:
    def test_citations_in_the_readers_form_and_a_bibliography(self, tmp_path: Path) -> None:
        level = tmp_path / "level"
        for sub in ("sections", "sources", "docs"):
            (level / sub).mkdir(parents=True)
        (level / "docs" / "brief.md").write_text("# The brief: JEPA\n")
        (level / "docs" / "references.json").write_text(
            '[{"document": "ijepa", "authors": "Assran, M., et al.", "title": "I-JEPA", "year": "2023", '
            '"venue": "CVPR", "short": "Assran et al., 2023"}]'
        )
        (level / "sections" / "01-a.md").write_text(
            "## One\n\nIt predicts [@ijepa-p003, page 4]. Also [@ijepa-p001].\n"
        )
        out = DocumentAssembly.write(level, tmp_path / "document.md")
        assert out is not None
        text = out.read_text()
        assert "It predicts (Assran et al., 2023, p. 4). Also (Assran et al., 2023)." in text
        assert text.endswith("## References\n\n- Assran, M., et al. (2023). *I-JEPA*. CVPR.\n")
