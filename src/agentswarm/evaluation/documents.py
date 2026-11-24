"""A document project from the user's own files (docs/design/documents.md, real use).

The user gives a list of documents and a goal ("summaries for managers and for engineers"). This converts each
file to text in a seed's `sources/` (PDF by pdftotext; Word, HTML, RTF and others by pandoc; text and Markdown
as they are), adds the document checker (citations name a source, one sentence per line, a length cap) and an
empty `sections/`, and writes a project file per audience: the same sources, its own goal. Running a project
drafts the brief and outline first (the briefer), pauses for the user to read and edit it, then writes, cites
and reviews. There are no hidden questions here: the user is the judge.
"""

import json
import re
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import ClassVar

import yaml
from pydantic import BaseModel, ConfigDict

from agentswarm.core.config import ModelConfig, RetrievalConfig
from agentswarm.core.domain import Reference
from agentswarm.evaluation.papers import ArxivSearch, Candidate, Found, Numbered, ReadingFolder
from agentswarm.evaluation.qasper import GuideImport


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class SourceText:
    """A document's text, by its kind."""

    PLAIN: ClassVar[tuple[str, ...]] = (".txt", ".md", ".markdown")
    NAME: ClassVar[int] = 30  # characters of a file's name kept in its sources' names
    TIMEOUT_S = 120

    @classmethod
    def read(cls, path: Path) -> str:
        suffix = path.suffix.lower()
        if suffix in cls.PLAIN:
            return path.read_text(errors="replace")
        if suffix == ".pdf":
            return cls.pdf(path)
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

    @classmethod
    def pdf(cls, path: Path) -> str:
        """The first page in layout mode, which keeps a title page's author line (plain mode dropped it from
        I-JEPA's, 2026-10-18), whitespace collapsed; the rest in plain mode, which reads columns in order."""
        first = cls.run(["pdftotext", "-l", "1", "-layout", "-enc", "UTF-8", str(path), "-"], path)
        rest = cls.run(["pdftotext", "-f", "2", "-enc", "UTF-8", str(path), "-"], path, empty=True)
        title_page = "\n".join(" ".join(line.split()) for line in first.splitlines())
        return f"{title_page}\f{rest}" if rest.strip() else title_page

    @classmethod
    def run(cls, command: list[str], path: Path, empty: bool = False) -> str:
        try:
            result = subprocess.run(
                command, capture_output=True, text=True, timeout=cls.TIMEOUT_S, check=False
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise SourceError(f"{path.name}: can't convert it ({command[0]}: {error})") from error
        if result.returncode != 0 or (not empty and not result.stdout.strip()):
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

    REVIEW = """\
review:
  model: gpt-sol
  referee: referee
  finding_referee: referee-fast
  perspectives: [{perspectives}]
  quiet_rounds: 1
"""

    def __init__(
        self,
        files: Sequence[Path],
        out: Path,
        goal: str,
        audiences: Sequence[str] = (),
        words: int = 3000,
        perspectives: Sequence[str] = ("facts", "coverage", "reader", "consistency"),  # none: no reviews
        approve: bool = True,  # pause after the brief for the user to read it
        budget: float = 2.0,  # the run's cap in dollars
        folder: str | None = None,  # the project's folder under `out`; default: the audience, or "document"
        project: str | None = None,  # the project's name (its board and its spend); default: docs-<folder>
        passages: RetrievalConfig
        | None = None,  # writers on passages (docs/design/rag.md step 4); None: whole
    ) -> None:
        self._files = tuple(files)
        self._out = out
        self._goal = goal
        self._audiences = tuple(audiences)
        self._words = words
        self._perspectives = tuple(perspectives)
        self._approve = approve
        self._budget = budget
        self._folder = folder
        self._project = project
        self._passages = passages

    class Blank(dict[str, str]):
        def __missing__(self, key: str) -> str:
            return "0"

    @classmethod
    def models(cls) -> dict[str, ModelConfig]:
        """The models a document project uses, from its template (the paper finder uses them before any
        project exists)."""
        config = yaml.safe_load(cls.PROJECT.read_text().format_map(cls.Blank(review="", passages="")))
        return {name: ModelConfig.model_validate(m) for name, m in config["models"].items()}

    @staticmethod
    def files(paths: Sequence[Path]) -> list[Path]:
        """The files given, a folder standing for the documents in it (the reading list `papers.md` aside)."""
        found: list[Path] = []
        for path in paths:
            if path.is_dir():
                found += sorted(
                    p
                    for p in path.iterdir()
                    if p.is_file()
                    and p.name not in (ReadingFolder.LISTING, ReadingFolder.RECORDS)
                    and not p.name.startswith(".")
                    and p.name != "README.md"
                )
            else:
                found.append(path)
        return found

    @staticmethod
    def record(path: Path, text: str) -> Candidate | None:
        """The paper's record, when `find-papers` found it (papers.json beside it)."""
        folder = ReadingFolder.load(path.parent)
        found = folder.match(path.name, text) if folder is not None else None
        return found.paper if found is not None else None

    ARXIV_FILE: ClassVar[re.Pattern[str]] = re.compile(
        r"^(\d{4}\.\d{4,5})(?:v\d+)?\.pdf$"
    )  # 2312.09979v2.pdf

    @classmethod
    def refresh(cls, folder: Path, arxiv: Numbered | None = None) -> ReadingFolder | None:
        """The folder's reading list brought up to date with the documents actually there; a paper the user
        added from arXiv under its own name (2312.09979v2.pdf) joins the list with arXiv's record, so it is
        shown and cited by its authors (2026-11-23: two papers dropped in the studio showed nowhere)."""
        listed = ReadingFolder.load(folder)
        if listed is None:
            return None
        files = [(p.name, SourceText.read(p)) for p in cls.files([folder])]
        listed = listed.refreshed(files)
        held = {f.file for f in listed.papers if f.file is not None}
        known = {f.paper.id for f in listed.papers}
        added: list[Found] = []
        for name, _ in files:
            number = cls.ARXIV_FILE.match(name)
            if name in held or number is None or number.group(1) in known:
                continue
            try:
                paper = (arxiv or ArxivSearch()).numbered(number.group(1))
            except (OSError, ValueError):
                continue  # tried again the next time the list is shown
            if paper is not None:
                added.append(Found(paper=paper, why="Added by the user.", covers="", file=name))
        if added:
            listed = listed.model_copy(update={"papers": (*listed.papers, *added)})
        listed.save(folder)
        return listed

    @classmethod
    def unlisted(cls, folder: Path) -> tuple[str, ...]:
        """The folder's documents no paper of its reading list holds: the user's own, shown by name."""
        listed = ReadingFolder.load(folder)
        held = {f.file for f in listed.papers if f.file is not None} if listed is not None else set[str]()
        return tuple(p.name for p in cls.files([folder]) if p.name not in held)

    def write(self) -> DocumentSummary:
        texts: dict[str, str] = {}
        known: list[Reference] = []
        for path in self._files:
            name = SourceText.slug(path.stem, SourceText.NAME)
            while any(n == name or n.startswith(f"{name}-") for n in texts):
                name += "-2"
            text = SourceText.read(path)
            texts.update(SourceParts.split(name, path.stem, text))
            record = self.record(path, text)
            if record is not None:
                known.append(record.reference(name))
        projects: list[Path] = []
        for audience in self._audiences or ("",):
            folder = self._out / (self._folder or (SourceText.slug(audience) if audience else "document"))
            seed = folder / "seed"
            for sub in ("sources", "sections", "tools"):
                (seed / sub).mkdir(parents=True, exist_ok=True)
            for name, text in texts.items():
                (seed / "sources" / f"{name}.md").write_text(text)
            (seed / "sections" / ".gitkeep").write_text("")
            if known:  # the briefer's references give way to these (Briefer.REFERENCES)
                (seed / "docs").mkdir(exist_ok=True)
                (seed / "docs" / "references.json").write_text(
                    json.dumps([r.model_dump() for r in known], indent=1) + "\n"
                )
            (seed / "tools" / "check_doc.py").write_text(
                GuideImport.CHECK.format(words=self._words, limit=round(self._words * GuideImport.SLACK))
            )
            goal = f"{self._goal} For this audience: {audience}." if audience else self._goal
            config = folder / "project.yaml"
            config.write_text(
                self.PROJECT.read_text().format(
                    heading=goal.splitlines()[0],
                    goal=json.dumps(goal),  # a JSON string is a YAML string: any text, its lines included
                    north_star=json.dumps(goal),
                    config=config,
                    root=Path("~/agentswarm-runs") / f"docs-{folder.name}",
                    project=self._project or f"docs-{folder.name}",
                    example=next(iter(texts), "name"),
                    words=self._words,
                    approve="true" if self._approve else "false",
                    review=self.REVIEW.format(perspectives=", ".join(self._perspectives))
                    if self._perspectives
                    else "",
                    budget=self._budget,
                    passages=f"    retrieval: {self._passages.model_dump_json()}\n" if self._passages else "",
                )
            )
            projects.append(config)
        return DocumentSummary(
            sources=tuple(texts), words=sum(len(t.split()) for t in texts.values()), projects=tuple(projects)
        )


class DocumentAssembly:
    """A finished document as one Markdown file: its title from the brief, every section in order, and
    numbered references: each source a footnote [^n], numbered in the order the text first cites it, its
    entry the bibliography's (authors, year, title, venue) and the pages cited. A page shows [n] linking to
    the entry and a way back (the user, 2026-11-13: "shorten them to just be the ref number"). Without
    references from the briefer, an entry is the source's name and heading."""

    CITATION: ClassVar[re.Pattern[str]] = re.compile(r" ?\[@([^\],\s]+)(?:,\s*page\s+(\d+))?[^\]]*\]")
    REFERENCES = "docs/references.json"

    @staticmethod
    def once(markers: re.Match[str]) -> str:
        """A run of citation markers as footnotes, each source once, in the order first cited."""
        numbers = dict.fromkeys(re.findall(r"@@CITE(\d+)@@", markers.group(0)))
        return "".join(f"[^{n}]" for n in numbers)

    @classmethod
    def write(cls, level: Path, out: Path) -> Path | None:
        """`level` is a level's plain folder (levels/<level>/ under the root); None when no section exists."""
        sections = sorted((level / "sections").glob("*.md"))
        if not sections:
            return None
        title = cls.title(level / "docs" / "brief.md")
        body = "\n\n".join(p.read_text().strip() for p in sections)
        references = cls.references(level / cls.REFERENCES)
        numbers: dict[str, int] = {}  # a source's key: its reference's document, or the part's name
        entries: dict[str, str] = {}
        pages: dict[str, list[int]] = {}

        def key_of(part: str) -> tuple[str, str]:
            if references:
                reference = cls.owner(part, references)
                if reference is not None:
                    return reference.document, cls.entry(reference)
            return part, f"`{part}`: {cls.heading(level / 'sources' / f'{part}.md')}"

        def cited(match: re.Match[str]) -> str:
            key, entry = key_of(match.group(1))
            numbers.setdefault(key, len(numbers) + 1)
            entries.setdefault(key, entry)
            if match.group(2):
                pages.setdefault(key, []).append(int(match.group(2)))
            return f"@@CITE{numbers[key]}@@"

        body = cls.CITATION.sub(cited, body)
        # one marker per source in a sentence: [@a, page 3] [@b] [@a, page 5] is [^1][^2], each once in its
        # order (moe-systems-2 showed "[^2][^3][^2][^3]": only neighbours were merged)
        body = re.sub(r"(?:@@CITE\d+@@)+", cls.once, body)
        notes: list[str] = []
        for key, number in sorted(numbers.items(), key=lambda kv: kv[1]):
            cited_pages = sorted(set(pages.get(key, [])))
            where = (
                f" Cited at page{'s' if len(cited_pages) > 1 else ''} {', '.join(map(str, cited_pages))}."
                if cited_pages
                else ""
            )
            notes.append(f"[^{number}]: {entries[key]}{where}")
        tail = "## References\n\n" + "\n".join(notes) + "\n" if notes else ""
        out.write_text(f"{title}\n\n{body}\n\n{tail}")
        return out

    @staticmethod
    def entry(r: Reference) -> str:
        return (
            f"{r.authors} ({r.year}). "
            + (f"[*{r.title}*]({r.url})." if r.url else f"*{r.title}*.")
            + (f" {r.venue}." if r.venue and r.venue != r.authors else "")
        )

    @staticmethod
    def title(brief: Path) -> str:
        if brief.is_file():
            for line in brief.read_text().splitlines():
                if line.startswith("# "):
                    return "# " + line.removeprefix("# ").removeprefix("The brief: ")
        return "# The document"

    @staticmethod
    def heading(source: Path) -> str:
        return source.read_text().split("\n", 1)[0].lstrip("# ") if source.is_file() else "(not found)"

    @staticmethod
    def references(path: Path) -> list[Reference]:
        if not path.is_file():
            return []
        return [Reference.model_validate(r) for r in json.loads(path.read_text())]

    @staticmethod
    def owner(part: str, references: Sequence[Reference]) -> Reference | None:
        """The reference whose document the part belongs to: the longest name it starts with. A briefer may
        name the document by its whole file name, where the parts carry it cut to SourceText.NAME characters
        (2026-10-19: 32 citations of bricken-2023-towards-monosemanticity were left unrendered)."""
        owners = [
            r
            for r in references
            for name in {r.document, SourceText.slug(r.document, SourceText.NAME)}
            if part == name or part.startswith(f"{name}-")
        ]
        return max(owners, key=lambda r: len(r.document), default=None)
