"""The guide project: a newcomer's guide to a few research papers, measured by questions no agent sees.

From Qasper (CC BY 4.0: questions written by NLP practitioners who read only a paper's title and abstract,
answered with evidence by others from the full paper; docs/design/documents.md) this writes a seed project:
the chosen papers' full text as sources, a brief, the document checker, and nothing written yet. The papers'
answerable questions go beside the seed, never into it: a reader model answers them from the guide alone to
score it (evaluation/questions.py).

Qasper's JSON: {paper id: {title, abstract, full_text: [{section_name, paragraphs}], qas: [{question,
question_id, answers: [{answer: {unanswerable, extractive_spans, yes_no, free_form_answer, ...}}]}]}}.
"""

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class PaperSection(Frozen):
    name: str
    paragraphs: tuple[str, ...]


class HiddenQuestion(Frozen):
    """A question about one paper, with each annotator's answer as plain text."""

    id: str
    paper: str  # the paper's id, e.g. "1909.00694"
    question: str
    answers: tuple[str, ...]  # at least one


class Paper(Frozen):
    id: str
    title: str
    abstract: str
    sections: tuple[PaperSection, ...]
    questions: tuple[HiddenQuestion, ...]  # the answerable ones

    def markdown(self) -> str:
        """The paper as a source file: title, abstract and sections, one paragraph per line."""
        parts = [f"# {self.title}", "", "## Abstract", "", self.abstract]
        for section in self.sections:
            parts += ["", f"## {section.name}", "", *[p for p in section.paragraphs if p.strip()]]
        return "\n".join(parts) + "\n"


class QasperSet:
    """One of Qasper's JSON files: its papers, in its order."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def papers(self) -> list[Paper]:
        raw: dict[str, Any] = json.loads(self._path.read_text())
        return [self.paper(id, entry) for id, entry in raw.items()]

    def search(self, words: Sequence[str]) -> list[Paper]:
        """Papers whose title or abstract holds every word, ignoring case."""
        wanted = [w.lower() for w in words]
        return [p for p in self.papers() if all(w in f"{p.title} {p.abstract}".lower() for w in wanted)]

    @classmethod
    def paper(cls, id: str, entry: dict[str, Any]) -> Paper:
        sections = tuple(
            PaperSection(
                name=str(s.get("section_name") or ""), paragraphs=tuple(str(p) for p in s["paragraphs"])
            )
            for s in entry.get("full_text", [])
        )
        questions = [cls.question(id, qa) for qa in entry.get("qas", [])]
        return Paper(
            id=id,
            title=str(entry["title"]),
            abstract=str(entry["abstract"]),
            sections=sections,
            questions=tuple(q for q in questions if q is not None),
        )

    @staticmethod
    def question(paper: str, qa: dict[str, Any]) -> HiddenQuestion | None:
        """The question with every annotator's answer as text; None when no annotator could answer it."""
        answers: list[str] = []
        for annotation in qa.get("answers", []):
            answer: dict[str, Any] = annotation["answer"]
            if answer.get("unanswerable"):
                continue
            if answer.get("yes_no") is not None:
                answers.append("Yes" if answer["yes_no"] else "No")
            elif answer.get("free_form_answer"):
                answers.append(str(answer["free_form_answer"]))
            elif answer.get("extractive_spans"):
                answers.append("; ".join(str(s) for s in answer["extractive_spans"]))
        if not answers:
            return None
        return HiddenQuestion(
            id=str(qa["question_id"]), paper=paper, question=str(qa["question"]), answers=tuple(answers)
        )


class GuideSummary(Frozen):
    papers: int
    questions: int
    words: int  # the guide's length cap
    seed: Path
    held_out: Path

    def render(self) -> str:
        return (
            f"{self.papers} papers as sources in {self.seed}, a guide of at most {self.words} words; "
            f"{self.questions} hidden questions in {self.held_out}"
        )


class GuideImport:
    """A seed for a guide to the chosen papers, and their questions held out."""

    QUESTIONS: ClassVar[str] = "questions.json"
    # What the guide is for. The hidden questions are practitioners' (data, baselines, metrics, numbers),
    # so the exam matches the reference brief; a newcomer's guide spends its words on explanations they don't
    # ask about (2026-10-16: two review arms doubled such a guide, and no question was gained).
    PURPOSES: ClassVar[dict[str, str]] = {
        "reference": (
            "Write a reference guide to {topic} for a practitioner: someone who knows the field and wants "
            "to look up what each of these papers did without reading it. For each paper, give its aim; its "
            "method; its data (sources, size, how it was collected or annotated); the baselines it compares "
            "with; its evaluation metrics; and its results, with the numbers. Then say how the papers "
            "relate. Prefer the specific facts (names, numbers, settings) to general explanation."
        ),
        "newcomer": (
            "Write a guide for a newcomer to {topic}: someone who knows programming and a little "
            "machine learning, but hasn't read these papers. After reading it, they should understand what "
            "each paper set out to do, how it went about it (the method, the data, how it was evaluated), "
            "what it found, and how the papers relate."
        ),
    }
    BRIEF = """\
# The brief

{purpose}

## Sources

The papers are in `sources/`, one file each. Use them, and nothing else, for every fact:

{papers}

## Rules

- The guide lives in `sections/`, one Markdown file per section, numbered in reading order \
(`sections/01-introduction.md`, ...), each starting with a `## ` heading.
- At most {words} words in all.
- Every factual statement cites the paper it comes from, like `[@{example}]`; a citation names a file in \
`sources/` without its extension.
- One sentence per line.
"""

    CHECK = '''"""The guide's rules, checked deterministically; from the root: python tools/check_doc.py

- every file in sections/ is Markdown, starts with a '## ' heading and has text under it;
- one sentence per line;
- every citation [@name] names a file in sources/, and every section cites at least one;
- at most {words} words in all sections together.

Exit code 0 when everything holds; otherwise one problem per line on stdout and exit code 1.
"""

import re
import sys
from pathlib import Path

WORDS = {words}
CITATION = re.compile(r"\\[@([^\\],\\s]+)(?:,[^\\]]*)?\\]")  # [@name] or [@name, page 121]
TWO_SENTENCES = re.compile(r"[.!?] +[A-Z]")
# Abbreviations end in a dot without ending the sentence: a.m., p.m., U.S., Dr., e.g.
TITLES = "Mr|Mrs|Ms|Dr|St|Jr|Sr|No|Nos|Fig|Vol|vs|cf|al|Inc|Co|Corp|Ltd"
ABBREVIATIONS = re.compile(r"\\b(?:[A-Za-z]\\.){{2,}}|\\b(?:" + TITLES + r")\\.")


class GuideCheck:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.problems: list[str] = []

    def run(self) -> list[str]:
        sections = self.root / "sections"
        found = sections.glob("*") if sections.is_dir() else []
        files = sorted(p for p in found if not p.name.startswith("."))
        words = sum(self.check_section(path) for path in files)
        if words > WORDS:
            self.problems.append(f"the guide has {{words}} words; the brief allows at most {{WORDS}}")
        return self.problems

    def check_section(self, path: Path) -> int:
        name = path.relative_to(self.root)
        if path.suffix != ".md":
            self.problems.append(f"{{name}}: sections must be Markdown files")
            return 0
        lines = [line.rstrip() for line in path.read_text().splitlines() if line.strip()]
        if not lines or not lines[0].startswith("## "):
            self.problems.append(f"{{name}}: must start with a '## ' heading")
        text = [line for line in lines[1:] if not line.startswith("#")]
        if not text:
            self.problems.append(f"{{name}}: has no text under its heading")
        for number, line in enumerate(text, 1):
            plain = ABBREVIATIONS.sub("", CITATION.sub("", line))
            if TWO_SENTENCES.search(plain) and not line.lstrip().startswith(("-", "*", "|")):
                self.problems.append(f"{{name}}: one sentence per line: {{line[:60]!r}}")
        cited = CITATION.findall("\\n".join(text))
        for source in cited:
            if not (self.root / "sources" / f"{{source}}.md").is_file():
                self.problems.append(f"{{name}}: [@{{source}}] names no file in sources/")
        if text and not cited:
            self.problems.append(f"{{name}}: cites no source")
        return sum(len(CITATION.sub("", line).split()) for line in text)


if __name__ == "__main__":
    found = GuideCheck(Path(".")).run()
    for problem in found:
        print(problem)
    sys.exit(1 if found else 0)
'''

    def __init__(
        self,
        qasper: Path,
        papers: Sequence[str],
        topic: str,
        out: Path,
        words: int = 3000,
        purpose: str = "reference",
    ) -> None:
        if purpose not in self.PURPOSES:
            raise ValueError(f"purpose {purpose!r}: one of {', '.join(self.PURPOSES)}")
        self._purpose = purpose
        self._qasper = qasper  # one of Qasper's JSON files
        self._ids = tuple(papers)
        self._topic = topic
        self._out = out
        self._words = words

    def chosen(self) -> list[Paper]:
        by_id = {p.id: p for p in QasperSet(self._qasper).papers()}
        missing = [id for id in self._ids if id not in by_id]
        if missing:
            raise ValueError(f"not in {self._qasper.name}: {', '.join(missing)}")
        return [by_id[id] for id in self._ids]

    def write(self) -> GuideSummary:
        papers = self.chosen()
        seed, hidden = self._out / "seed", self._out / "held_out"
        for folder in (seed / "sources", seed / "sections", seed / "tools", seed / "docs", hidden):
            folder.mkdir(parents=True, exist_ok=True)
        for paper in papers:
            (seed / "sources" / f"{paper.id}.md").write_text(paper.markdown())
        (seed / "sections" / ".gitkeep").write_text("")
        listed = "\n".join(f"- `{p.id}`: {p.title}" for p in papers)
        purpose = self.PURPOSES[self._purpose].format(topic=self._topic)
        brief = self.BRIEF.format(purpose=purpose, papers=listed, words=self._words, example=papers[0].id)
        (seed / "docs" / "brief.md").write_text(brief)
        (seed / "tools" / "check_doc.py").write_text(self.CHECK.format(words=self._words))
        questions = [q for p in papers for q in p.questions]
        (hidden / self.QUESTIONS).write_text(json.dumps([q.model_dump() for q in questions], indent=1))
        return GuideSummary(
            papers=len(papers), questions=len(questions), words=self._words, seed=seed, held_out=hidden
        )
