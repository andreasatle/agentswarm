"""Hybrid search over a document's sources (docs/RESEARCH.md section 14: keyword and dense search catch
different misses, and reciprocal rank fusion merges them without tuning).

A source is cut into passages of whole paragraphs, each knowing the page it starts on. One SQLite database
holds them three ways: the text, a full-text index (FTS5, ranked by BM25) and a vector index (sqlite-vec,
cosine distance). A query runs both searches within the sources it names and keeps the passages ranked
highest by either. Embeddings are kept by the hash of their text, so a source indexed again costs nothing.
"""

import hashlib
import re
import sqlite3
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import ClassVar, Protocol

from pydantic import BaseModel, ConfigDict


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Passage(Frozen):
    source: str  # the source's name, as cited: [@name]
    page: int | None  # the page the passage starts on, when the source marks its pages
    text: str


class Embedder(Protocol):
    """Turns text into vectors; a real one wraps a LangChain embedding model (llm/embeddings.py)."""

    @property
    def name(self) -> str: ...  # which model: vectors of different models never mix

    async def documents(self, texts: Sequence[str]) -> list[list[float]]: ...

    async def query(self, text: str) -> list[float]: ...


class Chunker:
    """Cuts a source into passages of whole paragraphs, up to a size; a page marker starts a new passage.

    Options measured on the retrieval benchmark (docs/design/rag.md): `overlap` carries a passage's last
    paragraph into the next when it is at most that long, so a statement near a cut is whole in one of them;
    `context` starts each passage with the source's title line (contextual retrieval, the free version)."""

    PAGE: ClassVar[re.Pattern[str]] = re.compile(r"^\[page (\d+)\]$")
    PART: ClassVar[re.Pattern[str]] = re.compile(r",? part \d+ of \d+$")

    def __init__(self, size: int = 1200, overlap: int = 0, context: bool = False) -> None:
        self._size = size  # characters: a few paragraphs, enough to hold a claim and its context
        self._overlap = overlap  # characters; 0: passages don't share text
        self._context = context

    def passages(self, source: str, text: str) -> list[Passage]:
        found: list[Passage] = []
        page: int | None = None
        start: int | None = None
        held: list[str] = []
        title = self.title(text) if self._context else ""

        def flush() -> None:
            if held:
                body = "\n\n".join(held)
                found.append(Passage(source=source, page=start, text=f"{title}\n\n{body}" if title else body))
                carried = held[-1] if 0 < len(held[-1]) <= self._overlap and len(held) > 1 else None
                held.clear()
                if carried is not None:
                    held.append(carried)

        for paragraph in (p.strip() for p in re.split(r"\n\s*\n", text)):
            if not paragraph:
                continue
            marked = self.PAGE.match(paragraph.splitlines()[0])
            if marked:
                flush()
                held.clear()  # a new page starts clean: a passage knows the one page it starts on
                page = int(marked.group(1))
                paragraph = "\n".join(paragraph.splitlines()[1:]).strip()
                if not paragraph:
                    continue
            for piece in self.pieces(paragraph):
                if held and sum(len(p) for p in held) + len(piece) > self._size:
                    flush()
                if not held:  # a carried paragraph is on the same page: a page marker clears it
                    start = page
                held.append(piece)
        flush()
        held.clear()
        return found

    @classmethod
    def title(cls, text: str) -> str:
        """The source's title line, without its part number: "# name, part 2 of 3" gives "name"."""
        first = text.lstrip().split("\n", 1)[0]
        return cls.PART.sub("", first.lstrip("# ").strip()) if first.startswith("#") else ""

    def pieces(self, paragraph: str) -> list[str]:
        """A paragraph longer than a passage, cut at line breaks (text from PDFs often has no blank lines
        for a whole page: the sleep sources had passages of 6,000 characters)."""
        if len(paragraph) <= self._size:
            return [paragraph]
        pieces: list[str] = []
        lines: list[str] = []
        for line in paragraph.splitlines():
            if lines and sum(len(x) + 1 for x in lines) + len(line) > self._size:
                pieces.append("\n".join(lines))
                lines = []
            lines.append(line)
        if lines:
            pieces.append("\n".join(lines))
        return pieces


class PassageIndex:
    """Passages of named sources, searchable by keywords and by meaning, in one SQLite database."""

    FUSION: ClassVar[int] = 60  # reciprocal rank fusion's constant (Cormack et al. 2009): 1 / (60 + rank)
    WORD: ClassVar[re.Pattern[str]] = re.compile(r"[A-Za-z0-9]{3,}")

    def __init__(self, embedder: Embedder, path: Path | None = None, chunker: Chunker | None = None) -> None:
        import sqlite_vec  # an optional extra (`retrieval`): imported only where an index is built

        self._embedder = embedder
        self._chunker = chunker or Chunker()
        self._db = sqlite3.connect(str(path) if path is not None else ":memory:")
        self._db.enable_load_extension(True)
        sqlite_vec.load(self._db)
        self._db.enable_load_extension(False)
        self._serialize = sqlite_vec.serialize_float32
        self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS sources (name TEXT PRIMARY KEY, hash TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS passages (
                id INTEGER PRIMARY KEY, source TEXT NOT NULL, page INTEGER, text TEXT NOT NULL);
            CREATE VIRTUAL TABLE IF NOT EXISTS passage_words USING fts5(
                text, source UNINDEXED, content='passages', content_rowid='id');
            CREATE TABLE IF NOT EXISTS vectors (
                hash TEXT, model TEXT, vector BLOB, PRIMARY KEY (hash, model));
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            """
        )
        meta = dict(self._db.execute("SELECT key, value FROM meta").fetchall())
        if "model" in meta and meta["model"] != embedder.name:
            raise ValueError(
                f"the index at {path} holds vectors of {meta['model']}, not {embedder.name}: use another file"
            )
        self._dimensions = int(meta["dimensions"]) if "dimensions" in meta else None

    def close(self) -> None:
        self._db.close()

    async def sync(self, sources: Mapping[str, str]) -> None:
        """Index every source whose text is new or changed since it was last indexed."""
        known = dict(self._db.execute("SELECT name, hash FROM sources").fetchall())
        for name, text in sources.items():
            digest = self.digest(text)
            if known.get(name) != digest:
                await self._index(name, text, digest)

    @staticmethod
    def excerpt(passages: Sequence[Passage]) -> str:
        """Passages as one text for a model: each with the page it starts on, gaps marked [...]."""
        return "\n\n[...]\n\n".join(
            (f"[page {p.page}]\n" if p.page is not None else "") + p.text for p in passages
        )

    async def search(self, query: str, sources: Sequence[str], k: int) -> list[Passage]:
        """The k passages of the named sources ranked highest by keywords, meaning, or both."""
        named = list(dict.fromkeys(sources))
        if not named or self._dimensions is None:
            return []
        pool = k * 4  # each search ranks more than it keeps, so fusion can promote a passage both liked
        scores: dict[int, float] = {}
        for ranked in (self._keywords(query, named, pool), await self._meaning(query, named, pool)):
            for rank, id in enumerate(ranked):
                scores[id] = scores.get(id, 0.0) + 1.0 / (self.FUSION + rank + 1)
        best = sorted(scores, key=lambda id: -scores[id])[:k]
        return [self._passage(id) for id in best]

    async def _index(self, name: str, text: str, digest: str) -> None:
        """Embed first, then replace the source's passages in one step with no await inside, so writers
        indexing the same source at once (they run in parallel) can't interleave and store it twice."""
        passages = self._chunker.passages(name, text)
        vectors = await self._vectors([p.text for p in passages])
        known = self._db.execute("SELECT hash FROM sources WHERE name = ?", (name,)).fetchone()
        if known is not None and known[0] == digest:
            return  # another caller indexed this text while we embedded it
        old = [row[0] for row in self._db.execute("SELECT id FROM passages WHERE source = ?", (name,))]
        for id in old:  # an external-content FTS table is told what leaves, with the text it had
            row = self._db.execute("SELECT text, source FROM passages WHERE id = ?", (id,)).fetchone()
            self._db.execute(
                "INSERT INTO passage_words(passage_words, rowid, text, source) VALUES ('delete', ?, ?, ?)",
                (id, row[0], row[1]),
            )
            self._db.execute("DELETE FROM passages WHERE id = ?", (id,))
            if self._dimensions is not None:
                self._db.execute("DELETE FROM passage_vectors WHERE id = ?", (id,))
        if vectors and self._dimensions is None:
            self._dimensions = len(vectors[0])
            self._db.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS passage_vectors USING vec0("
                f"id INTEGER PRIMARY KEY, embedding float[{self._dimensions}] distance_metric=cosine, "
                "source TEXT)"
            )
            self._db.executemany(
                "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
                [("model", self._embedder.name), ("dimensions", str(self._dimensions))],
            )
        for passage, vector in zip(passages, vectors, strict=True):
            cursor = self._db.execute(
                "INSERT INTO passages(source, page, text) VALUES (?, ?, ?)",
                (passage.source, passage.page, passage.text),
            )
            id = cursor.lastrowid
            self._db.execute(
                "INSERT INTO passage_words(rowid, text, source) VALUES (?, ?, ?)", (id, passage.text, name)
            )
            self._db.execute(
                "INSERT INTO passage_vectors(id, embedding, source) VALUES (?, ?, ?)",
                (id, self._serialize(vector), name),
            )
        self._db.execute("INSERT OR REPLACE INTO sources(name, hash) VALUES (?, ?)", (name, digest))
        self._db.commit()

    async def _vectors(self, texts: Sequence[str]) -> list[list[float]]:
        """Each text's vector, from the store when this model has embedded the same text before."""
        digests = [self.digest(t) for t in texts]
        stored: dict[str, list[float]] = {}
        for digest in set(digests):
            row = self._db.execute(
                "SELECT vector FROM vectors WHERE hash = ? AND model = ?", (digest, self._embedder.name)
            ).fetchone()
            if row is not None:
                stored[digest] = self.unpack(row[0])
        missing = [i for i, d in enumerate(digests) if d not in stored]
        if missing:
            fresh = await self._embedder.documents([texts[i] for i in missing])
            for i, vector in zip(missing, fresh, strict=True):
                stored[digests[i]] = vector
                self._db.execute(
                    "INSERT OR REPLACE INTO vectors(hash, model, vector) VALUES (?, ?, ?)",
                    (digests[i], self._embedder.name, self._serialize(vector)),
                )
        return [stored[d] for d in digests]

    def _keywords(self, query: str, sources: Sequence[str], limit: int) -> list[int]:
        words = list(dict.fromkeys(w.lower() for w in self.WORD.findall(query)))
        if not words:
            return []
        match = " OR ".join(f'"{w}"' for w in words)  # quoted: no word is read as FTS5 syntax
        marks = ",".join("?" * len(sources))
        rows = self._db.execute(
            f"SELECT rowid FROM passage_words WHERE passage_words MATCH ? AND source IN ({marks}) "
            "ORDER BY rank LIMIT ?",
            (match, *sources, limit),
        )
        return [row[0] for row in rows]

    async def _meaning(self, query: str, sources: Sequence[str], limit: int) -> list[int]:
        vector = await self._embedder.query(query)
        marks = ",".join("?" * len(sources))
        rows = self._db.execute(
            f"SELECT id FROM passage_vectors WHERE embedding MATCH ? AND k = ? AND source IN ({marks}) "
            "ORDER BY distance",
            (self._serialize(vector), limit, *sources),
        )
        return [row[0] for row in rows]

    def _passage(self, id: int) -> Passage:
        source, page, text = self._db.execute(
            "SELECT source, page, text FROM passages WHERE id = ?", (id,)
        ).fetchone()
        return Passage(source=source, page=page, text=text)

    @staticmethod
    def digest(text: str) -> str:
        return hashlib.sha256(text.encode()).hexdigest()

    @staticmethod
    def unpack(blob: bytes) -> list[float]:
        import struct

        return list(struct.unpack(f"{len(blob) // 4}f", blob))
