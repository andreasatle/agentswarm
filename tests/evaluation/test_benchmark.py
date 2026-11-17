import sqlite3
from pathlib import Path

from pydantic import BaseModel

from agentswarm.evaluation.benchmark import (
    Benchmark,
    BenchmarkCase,
    BenchmarkCases,
    BenchmarkRound,
    Flaw,
    FlawKind,
    JudgeReport,
    ReaderJudge,
)
from agentswarm.llm.workers import FakeWorker, Toolbox
from agentswarm.pipeline.run import DocumentRun, RunRequest, RunView, Stage

DOCUMENT = "# Title\n\n## One\n\nThe tool cut review time by 14% (Smith, 2024).\nIt helped new staff most.\n"


def report(*flaws: Flaw):
    def step(toolbox: Toolbox | None) -> BaseModel:
        return JudgeReport(flaws=list(flaws))

    return step


class TestReaderJudge:
    async def test_only_flaws_quoting_the_document_count_once_each(self) -> None:
        real = Flaw(kind=FlawKind.UNCLEAR, quote="It helped new  staff most.", why="who are the new staff?")
        again = Flaw(kind=FlawKind.REPETITION, quote="It helped new staff most.", why="said twice")
        invented = Flaw(kind=FlawKind.BROKEN_TEXT, quote="a sentence nowhere in it", why="made up")
        short = Flaw(kind=FlawKind.CITATION_FORM, quote="(Smith", why="too short to check")
        worker = FakeWorker([report(real, again, invented, short)])
        flaws = await ReaderJudge(worker).judge("an engineer", DOCUMENT)
        assert flaws == [real]


class TestBenchmark:
    def test_board_figures_are_its_time_span_and_spend_per_model(self, tmp_path: Path) -> None:
        board = tmp_path / "blackboard.sqlite"
        with sqlite3.connect(board) as db:
            db.execute("CREATE TABLE events (seq INTEGER PRIMARY KEY, at TEXT, kind TEXT, payload TEXT)")
            rows = [
                ("2026-11-13T10:00:00Z", "spent", '{"event": {"model": "haiku", "usd": 0.1}}'),
                ("2026-11-13T10:06:00Z", "spent", '{"event": {"model": "haiku", "usd": 0.2}}'),
                ("2026-11-13T10:12:00Z", "spent", '{"event": {"model": "sol", "usd": 0.5}}'),
            ]
            db.executemany("INSERT INTO events (at, kind, payload) VALUES (?, ?, ?)", rows)
        minutes, by_model = Benchmark.board_figures(board)
        assert minutes == 12.0 and by_model == {"haiku": 0.3, "sol": 0.5}

    async def test_a_run_that_stopped_before_writing_is_marked_unfinished_not_flawless(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "board").mkdir()
        with sqlite3.connect(tmp_path / "board" / "blackboard.sqlite") as db:
            db.execute("CREATE TABLE events (seq INTEGER PRIMARY KEY, at TEXT, kind TEXT, payload TEXT)")
        request = RunRequest(name="bench-x", topic="t", reader="r", budget=2.5)
        run = DocumentRun(request=request, folder=tmp_path, stage=Stage.SOURCES)
        view = RunView(run=run, spent=0.04, failure="write: no Docker daemon answers")
        case = BenchmarkCase(name="x", topic="t", reader="r", sources=tmp_path, words=2000)
        judge = FakeWorker([])
        measure = await Benchmark(ReaderJudge(judge)).measure(case, view)
        assert measure.unfinished == "write: no Docker daemon answers" and not judge.calls

    def test_the_held_out_cases_load_and_their_sources_are_files_not_the_reading_list(
        self, tmp_path: Path
    ) -> None:
        cases = BenchmarkCases.load()
        assert {c.name for c in cases.cases} == {"scaling", "sleep", "reasoning"}
        for name in ("a.pdf", "b.txt", "papers.md", "papers.json"):
            (tmp_path / name).write_text("x")
        assert [p.name for p in BenchmarkRound.files(tmp_path)] == ["a.pdf", "b.txt"]
