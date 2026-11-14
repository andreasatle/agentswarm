from pathlib import Path

from pydantic import BaseModel

from agentswarm.core.domain import Instruction, OutlineEntry, SectionAssigned
from agentswarm.llm.workers import FakeWorker, Toolbox
from agentswarm.teams.documents import (
    Bullets,
    Cite,
    Equation,
    Overlap,
    Paragraph,
    SectionDraft,
    SectionLayout,
    SectionRequest,
    SectionWriting,
    Sentence,
    Writer,
)
from agentswarm.teams.executor import WorktreeToolbox
from tests.support.harness import Harness


def said(text: str, *sources: str) -> Sentence:
    return Sentence(text=text, cites=[Cite(source=s) for s in sources])


def draft(*sentences: Sentence) -> SectionDraft:
    return SectionDraft(blocks=[Paragraph(sentences=list(sentences))])


def section(toolbox: Toolbox | None) -> BaseModel:
    return draft(said("The joint failed in the cold.", "rogers-07"))


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
        assert report.complete
        assert (
            worktree.read("sections/03-cause.md")
            == "## Cause\n\nThe joint failed in the cold [@rogers-07].\n"
        )
        sent = worker.calls[0][1]
        assert isinstance(sent, SectionRequest)
        assert [p.name for p in sent.sources] == ["rogers-07"]  # a source that isn't there is left out
        assert "below 53 F" in sent.sources[0].text and sent.brief.startswith("# The brief")
        assert sent.task == "Write sections/03-cause.md"
        assert len(worker.calls) == 1  # one answer, no tool loop
        worktree.remove()

    async def test_a_section_far_over_its_budget_is_rewritten_until_it_fits(self, tmp_path: Path) -> None:
        h = Harness(tmp_path)
        artifact = h.create(task="Write sections/03-cause.md")
        worktree = h.repository.checkout_artifact(artifact)
        worktree.write("sources/rogers-07.md", "# Rogers, part 7\n\nThe O-rings lost resilience.\n")
        long = draft(said("word " * 20 + "end.", "rogers-07"))
        short = draft(said("The joint failed.", "rogers-07"))
        worker = FakeWorker([lambda _: long, lambda _: long, lambda _: short])  # the first rewrite ignores it
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
        assert "Cut about 11 words" in again.must_fix[-1] and len(worker.calls) == 3
        assert worktree.read("sections/03-cause.md") == "## Cause\n\nThe joint failed [@rogers-07].\n"
        worktree.remove()


class TestUnknownSources:
    async def test_a_citation_of_no_source_is_sent_back_once_with_the_names_to_use(
        self, tmp_path: Path
    ) -> None:
        h = Harness(tmp_path)
        artifact = h.create(task="Write sections/03-cause.md")
        worktree = h.repository.checkout_artifact(artifact)
        worktree.write("sources/rogers-07.md", "# Rogers, part 7\n\nThe O-rings lost resilience.\n")
        wrong = draft(said("The joint failed.", "rogers-p123"))
        worker = FakeWorker([lambda _: wrong, section])
        entry = OutlineEntry(title="Cause", covers="the joint", sources=("rogers-07",), words=300)
        request = SectionWriting(
            north_star="a report",
            instruction=Instruction(goal="Write sections/03-cause.md", must_fix=(), constraints=()),
            files=(),
            diff_summary="",
            assignment=SectionAssigned(artifact=artifact.id, path="sections/03-cause.md", section=entry),
        )
        await Writer(worker, [], Harness.LADDER.lowest).work(worker, request, WorktreeToolbox(worktree, []))
        again = worker.calls[1][1]
        assert isinstance(again, SectionRequest)
        assert "cites rogers-p123, which are not sources; cite only these: rogers-07" in again.must_fix[-1]
        assert "[@rogers-p123]" in again.current  # the draft it is asked to fix
        assert "[@rogers-07]" in worktree.read("sections/03-cause.md")
        worktree.remove()


class TestSectionLayout:
    def test_blocks_are_laid_out_with_each_citation_inside_its_sentence(self) -> None:
        typed = SectionDraft(
            blocks=[
                Paragraph(
                    sentences=[
                        said(
                            "Architecture.",
                        ),
                        Sentence(text="The encoder is a ViT (see Fig. 2).", cites=[Cite(source="p", page=3)]),
                        said('It was "a surprise."', "p", "q"),
                    ]
                ),
                Bullets(items=[said("A first point.", "q"), said("No fact here")]),
                Equation(latex="$$ E = mc^2 $$"),
            ]
        )
        assert SectionLayout.markdown("I-JEPA", typed) == (
            "## I-JEPA\n\nArchitecture.\nThe encoder is a ViT (see Fig. 2) [@p, page 3].\n"
            'It was "a surprise [@p] [@q]."\n\n- A first point [@q].\n- No fact here\n\n$$\nE = mc^2\n$$\n'
        )
        assert SectionLayout.words(typed) == 1 + 8 + 4 + 3 + 3
        assert SectionLayout.unknown(typed, ["p"]) == ["q"]


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
