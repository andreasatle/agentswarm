"""A document project from the user's own files (docs/design/documents.md, real use).

The user gives a list of documents and a goal ("summaries for managers and for engineers"). This converts each
file to text in a seed's `sources/` (PDF by pdftotext; Word, HTML, RTF and others by pandoc; text and Markdown
as they are), adds the document checker (citations name a source, one sentence per line, a length cap) and an
empty `sections/`, and writes a project file per audience: the same sources, its own goal. Running a project
drafts the brief and outline first (the briefer), pauses for the user to read and edit it, then writes, cites
and reviews. There are no hidden questions here: the user is the judge.
"""

import re
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel, ConfigDict

from agentswarm.evaluation.qasper import GuideImport


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class SourceText:
    """A document's text, by its kind."""

    PLAIN: ClassVar[tuple[str, ...]] = (".txt", ".md", ".markdown")
    TIMEOUT_S = 120

    @classmethod
    def read(cls, path: Path) -> str:
        suffix = path.suffix.lower()
        if suffix in cls.PLAIN:
            return path.read_text(errors="replace")
        if suffix == ".pdf":
            command = ["pdftotext", "-enc", "UTF-8", str(path), "-"]
        else:  # Word, OpenDocument, HTML, RTF, EPUB: whatever pandoc reads
            command = ["pandoc", str(path), "-t", "plain", "--wrap=none"]
        try:
            result = subprocess.run(
                command, capture_output=True, text=True, timeout=cls.TIMEOUT_S, check=False
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise SourceError(f"{path.name}: can't convert it ({command[0]}: {error})") from error
        if result.returncode != 0 or not result.stdout.strip():
            raise SourceError(f"{path.name}: {command[0]} gave no text ({result.stderr.strip()[:200]})")
        return result.stdout

    @staticmethod
    def slug(text: str, limit: int = 50) -> str:
        return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:limit].strip("-") or "document"


class SourceParts:
    """A long document cut into parts of about `size` words, so a writer reads only the parts its section
    needs (2026-10-17: writers given three reports of 100,000 words ran out of steps before writing a line).
    A PDF is cut at page breaks and each page is marked, so a citation can name its page; other text at blank
    lines. A document that fits in one part stays whole."""

    SIZE: ClassVar[int] = 4000

    @classmethod
    def split(cls, name: str, title: str, text: str, size: int | None = None) -> list[tuple[str, str]]:
        """(name, file text) for each part."""
        size = size or cls.SIZE
        if len(text.split()) <= size:
            return [(name, f"# {title}\n\n{text.strip()}\n")]
        pages = text.split("\f")
        if len(pages) > 1:
            return cls.pages(name, title, pages, size)
        chunks: list[list[str]] = [[]]
        for paragraph in re.split(r"\n\s*\n", text):
            if chunks[-1] and sum(len(p.split()) for p in chunks[-1]) + len(paragraph.split()) > size:
                chunks.append([])
            chunks[-1].append(paragraph.strip())
        total = len(chunks)
        return [
            (f"{name}-{n:02d}", f"# {title}, part {n} of {total}\n\n" + "\n\n".join(c) + "\n")
            for n, c in enumerate(chunks, 1)
        ]

    @staticmethod
    def pages(name: str, title: str, pages: Sequence[str], size: int) -> list[tuple[str, str]]:
        parts: list[tuple[str, str]] = []
        start, chunk, words = 1, list[str](), 0
        for number, page in enumerate(pages, 1):
            chunk.append(f"[page {number}]\n{page.strip()}")
            words += len(page.split())
            if words >= size or number == len(pages):
                body = "\n\n".join(chunk)
                parts.append((f"{name}-p{start:03d}", f"# {title}, pages {start}-{number}\n\n{body}\n"))
                start, chunk, words = number + 1, [], 0
        return parts


class SourceError(RuntimeError):
    """A document couldn't be turned into text."""


class DocumentSummary(Frozen):
    sources: tuple[str, ...]
    words: int  # all sources together
    projects: tuple[Path, ...]

    def render(self) -> str:
        lines = [f"{len(self.sources)} sources, {self.words:,} words: {', '.join(self.sources)}"]
        lines += [f"  project: {p}" for p in self.projects]
        return "\n".join(lines)


class DocumentImport:
    """The seed and a project file per audience."""

    # The project file written per audience: a str.format template (YAML braces doubled)
    PROJECT: ClassVar[Path] = Path(__file__).with_name("document_project.yaml")

    def __init__(
        self, files: Sequence[Path], out: Path, goal: str, audiences: Sequence[str] = (), words: int = 3000
    ) -> None:
        self._files = tuple(files)
        self._out = out
        self._goal = goal
        self._audiences = tuple(audiences)
        self._words = words

    def write(self) -> DocumentSummary:
        texts: dict[str, str] = {}
        for path in self._files:
            name = SourceText.slug(path.stem, 30)
            while any(n == name or n.startswith(f"{name}-") for n in texts):
                name += "-2"
            texts.update(SourceParts.split(name, path.stem, SourceText.read(path)))
        projects: list[Path] = []
        for audience in self._audiences or ("",):
            folder = self._out / (SourceText.slug(audience) if audience else "document")
            seed = folder / "seed"
            for sub in ("sources", "sections", "tools"):
                (seed / sub).mkdir(parents=True, exist_ok=True)
            for name, text in texts.items():
                (seed / "sources" / f"{name}.md").write_text(text)
            (seed / "sections" / ".gitkeep").write_text("")
            (seed / "tools" / "check_doc.py").write_text(GuideImport.CHECK.format(words=self._words))
            goal = f"{self._goal} For this audience: {audience}." if audience else self._goal
            config = folder / "project.yaml"
            config.write_text(
                self.PROJECT.read_text().format(
                    goal=goal,
                    north_star=goal,
                    config=config,
                    root=Path("~/agentswarm-runs") / f"docs-{folder.name}",
                    project=f"docs-{folder.name}",
                    example=next(iter(texts), "name"),
                    words=self._words,
                )
            )
            projects.append(config)
        return DocumentSummary(
            sources=tuple(texts), words=sum(len(t.split()) for t in texts.values()), projects=tuple(projects)
        )
