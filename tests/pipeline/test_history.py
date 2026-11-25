from pathlib import Path

import pytest

from agentswarm.pipeline.history import RunFiles
from agentswarm.pipeline.run import Answer, Choice, Stage
from tests.pipeline.test_graph import FakeProject, Ledger, begin, pipeline, request


class TestHistory:
    def test_every_pause_is_a_moment_with_its_files_and_what_was_answered(self, tmp_path: Path) -> None:
        ledger = Ledger()
        runs = pipeline(FakeProject(ledger, shares=[12, 17]), ledger)
        begin(runs, request(), tmp_path)
        runs.answer("sleep", Answer(choice=Choice.YES))
        runs.answer("sleep", Answer(choice=Choice.YES))
        moments = runs.history("sleep")
        assert [m.title for m in moments] == [
            "Approve the questions",
            "Approve the sources",
            "Approve publishing",
            "Published",
        ]
        assert [m.answered for m in moments] == ["approved", "approved", "published", None]
        assert all(m.kept and m.version == 1 for m in moments) and moments[-1].current
        assert (
            moments[1].facts == "1 source"
            and moments[0].facts == "3 questions"
            and "citations 85%" in moments[2].facts
        )
        assert not moments[-1].pause

    def test_continuing_from_the_sources_starts_a_version_with_its_files_and_keeps_the_old(
        self, tmp_path: Path
    ) -> None:
        ledger = Ledger()
        project = FakeProject(ledger, shares=[12, 17, 18, 19])
        runs = pipeline(project, ledger)
        begin(runs, request(budget=10), tmp_path)
        (tmp_path / "board").mkdir()  # as the writers leave it
        (tmp_path / "board" / "document.md").write_text("version 1")
        runs.answer("sleep", Answer(choice=Choice.YES))
        (tmp_path / "page.html").write_text("published")
        runs.answer("sleep", Answer(choice=Choice.YES))
        (tmp_path / "NOTE.md").write_text("the user's own")
        sources = runs.history("sleep")[1]
        run = runs.continue_from("sleep", sources.id)
        assert run.stage is Stage.SOURCES and runs.waiting("sleep") is not None
        assert not (tmp_path / "page.html").exists() and not (tmp_path / "board").exists()
        assert (tmp_path / "NOTE.md").read_text() == "the user's own"  # not the run's: left alone
        (tmp_path / "sources" / "added.txt").write_text("A paper the user added.")
        runs.answer("sleep", Answer(choice=Choice.YES))
        assert project.writes == 2
        moments = runs.history("sleep")
        assert [(m.version, m.title) for m in moments] == [
            (1, "Approve the questions"),
            (1, "Approve the sources"),
            (1, "Approve publishing"),
            (1, "Published"),
            (2, "Approve the sources"),
            (2, "Approve publishing"),
        ]
        assert moments[-1].current and moments[-1].sources == 2  # the paper added at the pause
        timeline = runs.timeline("sleep")  # a straight line, the other version one line beside it
        assert [(m.version, m.title) for m in timeline.path] == [
            (1, "Approve the questions"),
            (2, "Approve the sources"),
            (2, "Approve publishing"),
        ]
        assert [(m.version, m.title) for m in timeline.others] == [(1, "Published")]
        old = next(m for m in moments if m.title == "Published")
        assert (
            RunFiles(tmp_path).locate(old.id, tmp_path / "board" / "document.md").read_text() == "version 1"
        )
        # and back to the first version's publishing pause: its files return
        first = next(m for m in moments if m.title == "Approve publishing" and m.version == 1)
        runs.continue_from("sleep", first.id)
        assert (tmp_path / "board" / "document.md").read_text() == "version 1"
        assert runs.history("sleep")[-1].version == 3
        timeline = runs.timeline("sleep")
        assert [m.title for m in timeline.path][-2:] == ["Approve the sources", "Approve publishing"]
        assert [m.version for m in timeline.others] == [2, 1]

    def test_a_moment_that_cant_be_continued_from_is_refused(self, tmp_path: Path) -> None:
        ledger = Ledger()
        runs = pipeline(FakeProject(ledger, shares=[12, 17]), ledger)
        begin(runs, request(), tmp_path)
        runs.answer("sleep", Answer(choice=Choice.YES))
        runs.answer("sleep", Answer(choice=Choice.YES))
        moments = runs.history("sleep")
        with pytest.raises(ValueError, match="ended there"):
            runs.continue_from("sleep", moments[-1].id)
        RunFiles.remove(RunFiles(tmp_path).at(moments[0].id))
        with pytest.raises(ValueError, match="before the history"):
            runs.continue_from("sleep", moments[0].id)
        runs.continue_from("sleep", moments[1].id)
        with pytest.raises(ValueError, match="at that moment now"):
            runs.continue_from("sleep", runs.history("sleep")[-1].id)
