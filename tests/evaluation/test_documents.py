import json
from pathlib import Path

from agentswarm.core.config import ProjectConfig, RetrievalConfig
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

    def test_writers_read_passages_when_the_policy_asks(self, tmp_path: Path) -> None:
        (tmp_path / "paper.txt").write_text("text\n")
        passages = RetrievalConfig(embeddings="ollama:nomic-embed-text", k=6, query_prefix="search_query: ")
        summary = DocumentImport(
            [tmp_path / "paper.txt"], tmp_path / "out", "A tutorial.", passages=passages
        ).write()
        writer = next(t for t in ProjectConfig.load(summary.projects[0]).teams if t.use == "writer")
        assert writer.retrieval == passages
        whole = DocumentImport([tmp_path / "paper.txt"], tmp_path / "whole", "A tutorial.").write()
        assert (
            next(t for t in ProjectConfig.load(whole.projects[0]).teams if t.use == "writer").retrieval
            is None
        )

    def test_reviews_by_the_perspectives_asked_for(self, tmp_path: Path) -> None:
        (tmp_path / "paper.txt").write_text("text\n")
        summary = DocumentImport(
            [tmp_path / "paper.txt"], tmp_path / "out", "A tutorial.", (), 4000, ("reader", "facts")
        ).write()
        review = ProjectConfig.load(summary.projects[0]).review
        assert review is not None and [str(p) for p in review.perspectives] == ["reader", "facts"]


class TestDocumentAssembly:
    def test_sources_are_numbered_by_first_citation_with_their_entries_and_pages(
        self, tmp_path: Path
    ) -> None:
        level = tmp_path / "level"
        (level / "sections").mkdir(parents=True)
        (level / "docs").mkdir()
        (level / "sections" / "01-a.md").write_text(
            "## A\n\nGlass is made there [@wikipedia-murano-01].\n"
            "Furnaces moved [@history-p010, page 12] [@history-p010, page 14] [@wikipedia-murano-02].\n"
            "- Boats come too [@history-p010, page 12].\n"
            "Glass and boats [@wikipedia-murano-01] [@history-p010] [@wikipedia-murano-02] [@history-p010].\n"
        )
        murano = Reference(
            document="wikipedia-murano",
            authors="Wikipedia",
            title="Murano",
            year="2026",
            venue="Wikipedia",
            short="Wikipedia: Murano",
            url="https://en.wikipedia.org/wiki/Murano",
        )
        history = Reference(
            document="history", authors="Lane", title="Venice", year="1973", short="Lane, 1973"
        )
        (level / "docs" / "references.json").write_text(
            json.dumps([murano.model_dump(), history.model_dump()])
        )
        text = (DocumentAssembly.write(level, tmp_path / "d.md") or tmp_path).read_text()
        assert "Glass is made there[^1].\n" in text
        assert "Furnaces moved[^2][^1].\n" in text  # one number per source in a sentence
        assert "- Boats come too[^2].\n" in text
        assert "Glass and boats[^1][^2].\n" in text  # apart, each source still once
        assert "[^1]: Wikipedia (2026). [*Murano*](https://en.wikipedia.org/wiki/Murano).\n" in text
        assert "[^2]: Lane (1973). *Venice*. Cited at pages 12, 14.\n" in text
        assert text.index("## References") < text.index("[^1]: ")

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
        assert "First[^1]." in text and "Later[^2]." in text
        assert "[^1]: `paper-p001`: Paper, pages 1-2\n" in text
        assert "[^2]: `paper-p003`: Paper, pages 3-5 Cited at page 4." in text


class TestReferences:
    def test_the_parts_of_one_document_share_its_number(self, tmp_path: Path) -> None:
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
        assert "It predicts[^1]. Also[^1]." in text
        assert text.endswith(
            "## References\n\n[^1]: Assran, M., et al. (2023). *I-JEPA*. CVPR. Cited at page 4.\n"
        )
