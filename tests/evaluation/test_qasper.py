import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from agentswarm.evaluation.qasper import GuideImport, QasperSet


def answer(**fields: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "unanswerable": False,
        "extractive_spans": [],
        "yes_no": None,
        "free_form_answer": "",
        "evidence": [],
        "highlighted_evidence": [],
    }
    return {"answer": {**base, **fields}, "annotation_id": "x", "worker_id": "w"}


QASPER: dict[str, Any] = {
    "1909.00001": {
        "title": "Tagging with Little Data",
        "abstract": "We tag words.",
        "full_text": [{"section_name": "Introduction", "paragraphs": ["Tagging matters.", ""]}],
        "qas": [
            {
                "question": "Which dataset?",
                "question_id": "q1",
                "answers": [answer(extractive_spans=["PTB", "CoNLL"])],
            },
            {"question": "Is it fast?", "question_id": "q2", "answers": [answer(yes_no=False)]},
            {"question": "Why?", "question_id": "q3", "answers": [answer(unanswerable=True)]},
            {"question": "How big?", "question_id": "q4", "answers": [answer(free_form_answer="1M words")]},
        ],
    },
    "1909.00002": {
        "title": "Parsing Fast",
        "abstract": "We parse.",
        "full_text": [],
        "qas": [],
    },
}


@pytest.fixture
def qasper(tmp_path: Path) -> Path:
    path = tmp_path / "qasper-test.json"
    path.write_text(json.dumps(QASPER))
    return path


class TestQasperSet:
    def test_answerable_questions_keep_every_answer_as_text(self, qasper: Path) -> None:
        paper = QasperSet(qasper).papers()[0]
        assert [(q.id, q.answers) for q in paper.questions] == [
            ("q1", ("PTB; CoNLL",)),
            ("q2", ("No",)),
            ("q4", ("1M words",)),
        ]  # q3: no annotator could answer it

    def test_a_paper_as_a_source_file(self, qasper: Path) -> None:
        text = QasperSet(qasper).papers()[0].markdown()
        assert text == (
            "# Tagging with Little Data\n\n## Abstract\n\nWe tag words.\n\n"
            "## Introduction\n\nTagging matters.\n"
        )

    def test_search_by_words_in_title_or_abstract(self, qasper: Path) -> None:
        assert [p.id for p in QasperSet(qasper).search(["parse"])] == ["1909.00002"]


class TestGuideImport:
    def test_the_seed_holds_sources_brief_and_checker_and_the_questions_stay_out(
        self, qasper: Path, tmp_path: Path
    ) -> None:
        out = tmp_path / "out"
        summary = GuideImport(
            qasper, ["1909.00001", "1909.00002"], "tagging and parsing", out, words=50
        ).write()
        assert (summary.papers, summary.questions) == (2, 3)
        seed = out / "seed"
        assert sorted(p.name for p in (seed / "sources").iterdir()) == ["1909.00001.md", "1909.00002.md"]
        assert "[@1909.00001]" in (seed / "docs" / "brief.md").read_text()
        assert not list(seed.rglob("questions.json"))
        assert len(json.loads((out / "held_out" / "questions.json").read_text())) == 3

    def test_an_unknown_paper_is_refused(self, qasper: Path, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match=r"1909\.99999"):
            GuideImport(qasper, ["1909.99999"], "x", tmp_path / "out").write()


class TestGuideCheck:
    @staticmethod
    def check(seed: Path, sections: dict[str, str]) -> list[str]:
        for name, text in sections.items():
            (seed / "sections" / name).write_text(text)
        result = subprocess.run(
            [sys.executable, "tools/check_doc.py"], cwd=seed, capture_output=True, text=True, check=False
        )
        return result.stdout.splitlines()

    def test_a_cited_section_within_the_cap_passes(self, qasper: Path, tmp_path: Path) -> None:
        GuideImport(qasper, ["1909.00001"], "tagging", tmp_path / "out", words=50).write()
        good = "## Tagging\n\nThe paper tags words [@1909.00001].\nIt uses little data [@1909.00001].\n"
        assert self.check(tmp_path / "out" / "seed", {"01-tagging.md": good}) == []

    def test_uncited_unknown_long_and_crowded_text_is_reported(self, qasper: Path, tmp_path: Path) -> None:
        GuideImport(qasper, ["1909.00001"], "tagging", tmp_path / "out", words=12).write()
        problems = self.check(
            tmp_path / "out" / "seed",
            {
                "01-a.md": "## A\n\nIt tags words. It is fast.\n",
                "02-b.md": "## B\n\nSee [@nowhere] for words and more words here.\n",
            },
        )
        assert any("01-a.md: one sentence per line" in p for p in problems)
        assert any("01-a.md: cites no source" in p for p in problems)
        assert any("[@nowhere] names no file" in p for p in problems)
        assert any("words; the brief allows at most 12" in p for p in problems)
