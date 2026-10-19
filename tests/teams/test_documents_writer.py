from pathlib import Path

from pydantic import BaseModel

from agentswarm.core.domain import Instruction, OutlineEntry, SectionAssigned
from agentswarm.llm.workers import FakeWorker, Toolbox
from agentswarm.teams.documents import (
    Citations,
    Overlap,
    SectionRequest,
    SectionText,
    SectionWriting,
    Sentences,
    Writer,
)
from agentswarm.teams.executor import WorktreeToolbox
from tests.support.harness import Harness


def section(toolbox: Toolbox | None) -> BaseModel:
    return SectionText(text="## Cause\n\nThe joint failed in the cold [@rogers-07].")


class TestSectionWriter:
    async def test_a_section_is_written_in_one_answer_from_its_sources(self, tmp_path: Path) -> None:
        h = Harness(tmp_path)
        artifact = h.create(task="Write sections/03-cause.md")
        worktree = h.repository.checkout_artifact(artifact)
        worktree.write("docs/brief.md", "# The brief\n\nFor engineers.\n")
        worktree.write(
            "sources/rogers-07.md", "# Rogers, part 7\n\nThe O-rings lost resilience below 53 F.\n"
        )
        worker = FakeWorker([section])
        writer = Writer(worker, [], Harness.LADDER.lowest)
        entry = OutlineEntry(title="Cause", covers="the joint", sources=("rogers-07", "missing"), words=300)
        request = SectionWriting(
            north_star="a report",
            instruction=Instruction(goal="Write sections/03-cause.md", must_fix=(), constraints=()),
            files=(),
            diff_summary="",
            assignment=SectionAssigned(artifact=artifact.id, path="sections/03-cause.md", section=entry),
        )
        report = await writer.work(worker, request, WorktreeToolbox(worktree, []))
        assert report.complete and worktree.read("sections/03-cause.md").startswith("## Cause\n")
        sent = worker.calls[0][1]
        assert isinstance(sent, SectionRequest)
        assert [p.name for p in sent.sources] == ["rogers-07"]  # a source that isn't there is left out
        assert "below 53 F" in sent.sources[0].text and sent.brief.startswith("# The brief")
        assert len(worker.calls) == 1  # one answer, no tool loop
        worktree.remove()

    async def test_a_section_far_over_its_budget_is_rewritten_once(self, tmp_path: Path) -> None:
        h = Harness(tmp_path)
        artifact = h.create(task="Write sections/03-cause.md")
        worktree = h.repository.checkout_artifact(artifact)
        long = SectionText(text="## Cause\n\n" + "word " * 20 + "[@rogers-07].")
        short = SectionText(text="## Cause\n\nThe joint failed [@rogers-07].")
        worker = FakeWorker([lambda _: long, lambda _: short])
        entry = OutlineEntry(title="Cause", covers="the joint", sources=("rogers-07",), words=10)
        request = SectionWriting(
            north_star="a report",
            instruction=Instruction(goal="Write sections/03-cause.md", must_fix=(), constraints=()),
            files=(),
            diff_summary="",
            assignment=SectionAssigned(artifact=artifact.id, path="sections/03-cause.md", section=entry),
        )
        await Writer(worker, [], Harness.LADDER.lowest).work(worker, request, WorktreeToolbox(worktree, []))
        again = worker.calls[1][1]
        assert (
            isinstance(again, SectionRequest) and "has 21 words; it may have at most 10" in again.must_fix[-1]
        )
        assert worktree.read("sections/03-cause.md") == "## Cause\n\nThe joint failed [@rogers-07].\n"
        worktree.remove()


class TestCitations:
    def test_a_citation_written_as_the_reader_sees_it_becomes_a_tag(self) -> None:
        headings = {
            "Venetian Lagoon": "wikipedia-venetian-lagoon",
            "Maritime republics, part 1 of 3": "wikipedia-maritime-republics-01",
        }
        text = (
            "It is a bay (Wikipedia: Venetian Lagoon).\n"
            "They traded (Wikipedia: Maritime republics, part 1 of 3; Wikipedia: Venetian Lagoon).\n"
            "Four cities (Wikipedia: Maritime republics) (about 1000 AD) (Wikipedia: Rome)."
        )
        assert Citations.spoken(text, headings) == (
            "It is a bay [@wikipedia-venetian-lagoon].\n"
            "They traded [@wikipedia-maritime-republics-01] [@wikipedia-venetian-lagoon].\n"
            "Four cities [@wikipedia-maritime-republics-01] (about 1000 AD) (Wikipedia: Rome)."
        )

    def test_a_page_cited_as_a_source_points_to_the_part_that_holds_it(self) -> None:
        names = ["report-p001", "report-p118", "report-p136", "memo"]
        text = (
            "Failed [@report-p123]. Also [@report-p125, page 125]. See [@report-p118], [@memo]. "
            "[@other-p005]."
        )
        assert Citations.mend(text, names) == (
            "Failed [@report-p118, page 123]. Also [@report-p118, page 125]. See [@report-p118], [@memo]. "
            "[@other-p005]."
        )


class TestSentences:
    def test_each_sentence_on_its_own_line_lists_and_abbreviations_left(self) -> None:
        text = (
            "## I-JEPA\n\nArchitecture. The encoder is a ViT [@p, page 3]. See Fig. 2 at 5 p.m. Today.\n"
            "- A. B."
        )
        assert Sentences.lines(text) == (
            "## I-JEPA\n\nArchitecture.\nThe encoder is a ViT [@p, page 3].\n"
            "See Fig. 2 at 5 p.m. Today.\n- A. B."
        )

    def test_mathematics_is_left_as_it_is(self) -> None:
        text = (
            "## Loss\n\nThe loss is $L = \\|s_y - \\hat{s}_y\\|_2^2$ over blocks. It is averaged.\n"
            "$$\nE(x, y). F(y)\n$$"
        )
        assert Sentences.lines(text) == (
            "## Loss\n\nThe loss is $L = \\|s_y - \\hat{s}_y\\|_2^2$ over blocks.\nIt is averaged.\n"
            "$$\nE(x, y). F(y)\n$$"
        )


class TestOverlap:
    def test_neighbours_within_the_same_document(self) -> None:
        names = ["rep-p001", "rep-p010", "rep-p020", "memo", "notes-01", "notes-02"]
        assert Overlap.neighbours("rep-p010", names) == ("rep-p001", "rep-p020")
        assert Overlap.neighbours("notes-01", names) == (None, "notes-02")
        assert Overlap.neighbours("memo", names) == (None, None)

    def test_the_edges_of_the_neighbours_frame_the_part(self) -> None:
        text = Overlap.text(
            "# Rep, pages 10-19\n\nmiddle", "a " * 400 + "end", "# Rep, pages 20-29\nstart " + "b " * 400
        )
        assert text.startswith("[... the end of the part before, for context]\n")
        assert (
            "end\n\n# Rep, pages 10-19" in text
            and "\n[the start of the part after, for context ...]\nstart" in text
        )
        assert len(text.split()) < 700

    def test_several_sources_in_one_bracket_are_cited_one_by_one(self) -> None:
        names = ["rep-p001", "rep-p008"]
        assert Citations.mend("A [@rep-p001, @rep-p012]. B [@rep-p001; @rep-p008, page 9].", names) == (
            "A [@rep-p001] [@rep-p008, page 12]. B [@rep-p001] [@rep-p008, page 9]."
        )


class TestNumberedLists:
    def test_a_numbered_question_stays_on_one_line(self) -> None:
        text = "## Self-check\n\n1. Why predict in latent space? See the loss.\n2) How is collapse avoided?"
        assert Sentences.lines(text) == text
