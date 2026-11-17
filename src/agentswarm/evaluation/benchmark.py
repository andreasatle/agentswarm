"""The document benchmark: a few held-out document runs, measured the same way after every significant
change (docs/design/self-correction.md).

The user, 2026-11-13: "are we overfitting, creating a fix for every problem that doesn't generalize?" Fixes so
far were found on the runs that showed the problem and checked only by unit tests. These cases are never used
to find fixes; they only measure whether a change made documents better or cheaper. Each case is a run of a
document on sources already on disk (benchmark.yaml beside this file), as `agentswarm write --files` makes it.

A run is measured by its cost and time, its spend per model (a strong model's share shows escalations), its
last citation audit, and the reader-facing flaws a held-out judge finds in the finished document. The judge is
not part of any run: it reads the document as its reader would and reports each flaw with a quote, which plain
code checks is really in the document, so the count can't be padded.
"""

import asyncio
import contextlib
import json
import sqlite3
from enum import StrEnum
from pathlib import Path
from typing import ClassVar, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field

from agentswarm.llm.workers import Worker
from agentswarm.pipeline.graph import DocumentPipeline
from agentswarm.pipeline.run import Answer, Choice, RunRequest, RunView, Stage


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class FlawKind(StrEnum):
    BROKEN_TEXT = "broken_text"  # a sentence split or cut, a stray fragment, punctuation out of place
    CITATION_FORM = "citation_form"  # a citation misplaced, garbled, or cluttering the text
    UNCLEAR = "unclear"  # a passage the intended reader couldn't follow: undefined terms, jumps, ambiguity
    REPETITION = "repetition"  # the same point made again without adding anything


class Flaw(Frozen):
    kind: FlawKind
    quote: str = Field(description="the exact text the flaw is in, copied verbatim, at least a few words")
    why: str = Field(description="one sentence: what is wrong for the reader")


class JudgeRequest(Frozen):
    """The finished document and who it is for."""

    reader: str
    document: str


class JudgeReport(Frozen):
    """Every reader-facing flaw you found, each with its quote; none if there are none."""

    flaws: list[Flaw]


class ReaderJudge:
    """The benchmark's judge: reader-facing flaws in a finished document, each backed by a verbatim quote.

    Flaws whose quote isn't in the document are dropped. It judges form and clarity only; whether claims are
    true is the citation audit's job."""

    INSTRUCTIONS = """\
You read a finished document as the reader it was written for, and list every flaw that reader would meet in
its form and clarity. Do not judge whether its claims are true or well sourced; judge only how it reads.

- broken_text: a sentence split or cut off, a stray fragment, a line starting mid-sentence, punctuation out of
  place.
- citation_form: a citation in the wrong place, garbled, or so frequent or long that it gets in the way.
- unclear: a passage this reader couldn't follow: a term used before it is explained, a jump in the argument,
  an ambiguous sentence.
- repetition: the same point made again without adding anything.

For each flaw, copy the exact text it is in, word for word, at least a few words, and say in one sentence what
is wrong for the reader. List each flaw once. Report nothing that isn't a flaw."""
    MIN_QUOTE: ClassVar[int] = 12  # characters; shorter quotes match too easily to count as evidence

    def __init__(self, worker: Worker) -> None:
        self._worker = worker

    async def judge(self, reader: str, document: str) -> list[Flaw]:
        report = await self._worker.run(
            self.INSTRUCTIONS, JudgeRequest(reader=reader, document=document), JudgeReport
        )
        text = self.normal(document)
        kept: list[Flaw] = []
        for flaw in report.flaws:
            quote = self.normal(flaw.quote)
            if (
                len(quote) >= self.MIN_QUOTE
                and quote in text
                and all(self.normal(k.quote) != quote for k in kept)
            ):
                kept.append(flaw)
        return kept

    @staticmethod
    def normal(text: str) -> str:
        return " ".join(text.split())


class BenchmarkCase(Frozen):
    """One held-out document: its sources on disk and the request that writes it."""

    name: str
    topic: str
    reader: str
    goal: str = ""  # the whole brief; empty: the policy's tutorial template
    sources: Path  # a folder of source files (PDFs, text) and their papers.json, never changed
    words: int


class BenchmarkCases(Frozen):
    cases: tuple[BenchmarkCase, ...]

    FILE: ClassVar[Path] = Path(__file__).with_name("benchmark.yaml")

    @classmethod
    def load(cls, path: Path | None = None) -> Self:
        return cls.model_validate(yaml.safe_load((path or cls.FILE).read_text()))


class RunMeasure(Frozen):
    """One benchmark run, as it is compared between rounds."""

    case: str
    run: str
    usd: float
    minutes: float  # from the board's first event to its last
    usd_by_model: dict[str, float]
    audit_share: float | None  # fully supported citations in the last audit; None: no audit
    flaws: tuple[Flaw, ...]
    # Why the run didn't publish; None: it did. A run stopped before writing had no flaws to find, and it
    # showed as "flaws 0" beside the finished ones (benchmark r2, 2026-11-16: Docker was down)
    unfinished: str | None = None

    def flaw_counts(self) -> dict[FlawKind, int]:
        return {kind: sum(f.kind is kind for f in self.flaws) for kind in FlawKind}


class Benchmark:
    """Measures a finished document run of a benchmark case."""

    def __init__(self, judge: ReaderJudge) -> None:
        self._judge = judge

    async def measure(self, case: BenchmarkCase, view: RunView) -> RunMeasure:
        run = view.run
        board = run.folder / "board" / "blackboard.sqlite"
        minutes, by_model = self.board_figures(board)
        document = run.document.read_text() if run.document is not None and run.document.is_file() else ""
        flaws = await self._judge.judge(case.reader, document) if document else []
        return RunMeasure(
            case=case.name,
            run=run.request.name,
            usd=round(view.spent, 4),
            minutes=minutes,
            usd_by_model=by_model,
            audit_share=round(run.audits[-1].share, 3) if run.audits else None,
            flaws=tuple(flaws),
            unfinished=None
            if run.stage is Stage.PUBLISHED
            else (view.failure or run.held or f"ended at {run.stage}"),
        )

    @staticmethod
    def board_figures(board: Path) -> tuple[float, dict[str, float]]:
        """Minutes from the board's first event to its last, and the spend per model, read from its log."""
        with sqlite3.connect(f"file:{board}?mode=ro", uri=True) as db:
            span = db.execute(
                "SELECT (julianday(max(at)) - julianday(min(at))) * 1440 FROM events"
            ).fetchone()
            rows = db.execute(
                "SELECT json_extract(payload, '$.event.model'), sum(json_extract(payload, '$.event.usd'))"
                " FROM events WHERE kind = 'spent' GROUP BY 1"
            ).fetchall()
        return round(float(span[0] or 0.0), 1), {str(m): round(float(u or 0.0), 4) for m, u in rows}


class BenchmarkRound:
    """Runs every case once with the current code, answers each pause the way a benchmark must (yes to the
    sources and to publishing; a failed section accepted as it is, so it counts in the result), measures each
    run and appends it to the results file under the round's label."""

    BUDGET: ClassVar[float] = 2.5  # dollars per case; a run that needs more pauses and is stopped
    SOURCE_FILES: ClassVar[tuple[str, ...]] = ("*.pdf", "*.txt", "*.md")
    RECORDS: ClassVar[frozenset[str]] = frozenset({"papers.md"})  # the finder's reading list, not a source

    def __init__(self, pipeline: DocumentPipeline, benchmark: Benchmark, runs: Path, results: Path) -> None:
        self._pipeline = pipeline
        self._benchmark = benchmark
        self._runs = runs
        self._results = results

    def run(self, label: str, cases: BenchmarkCases) -> list[RunMeasure]:
        measured: list[RunMeasure] = []
        for case in cases.cases:
            name = f"bench-{label}-{case.name}"
            request = RunRequest(
                name=name,
                topic=case.topic,
                reader=case.reader,
                budget=self.BUDGET,
                words=case.words,
                files=self.files(case.sources),
                goal=case.goal or None,
            )
            with contextlib.suppress(Exception):  # a failed step: the run is kept, its measure shows how far
                self._pipeline.start(request, self._runs / name)
            view = self.answered(name)
            result = asyncio.run(self._benchmark.measure(case, view))
            with self._results.open("a") as out:
                out.write(json.dumps({"round": label, **result.model_dump(mode="json")}) + "\n")
            measured.append(result)
        return measured

    def answered(self, name: str) -> RunView:
        """Answers the run's pauses until it ends: at most one answer per stage, so a loop can't run on."""
        view = self._pipeline.view(name)
        for _ in range(10):
            if view.question is None or view.failure is not None:
                break
            choices = view.question.choices
            choice = next((c for c in (Choice.YES, Choice.ACCEPT) if c in choices), Choice.STOP)
            with contextlib.suppress(Exception):  # likewise: the view below shows the failure
                self._pipeline.answer(name, Answer(choice=choice))
            view = self._pipeline.view(name)
        return view

    @classmethod
    def files(cls, folder: Path) -> tuple[Path, ...]:
        found = {p for pattern in cls.SOURCE_FILES for p in folder.expanduser().glob(pattern)}
        return tuple(sorted(p for p in found if p.name not in cls.RECORDS))
