from pathlib import Path

from pydantic import BaseModel

from agentswarm.core.domain import Instruction, OutlineEntry, SectionAssigned
from agentswarm.llm.workers import FakeWorker, Toolbox
from agentswarm.teams.documents import (
    Citations,
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
