from pathlib import Path

from pydantic import BaseModel

from agentswarm.core.domain import Instruction, OutlineEntry, SectionAssigned
from agentswarm.llm.workers import FakeWorker, Toolbox
from agentswarm.teams.documents import SectionRequest, SectionText, SectionWriting, Writer
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
