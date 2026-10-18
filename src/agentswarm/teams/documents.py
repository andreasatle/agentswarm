"""The document domain's teams (proposal §10.3): the same classes as for software, with document instructions.

G2: domains are configuration, not separate code. A writer is an executor, an outliner a decomposer, and
the three reviewers are reviewing analyzers, whose evidence is a verbatim quote instead of a failing test.
"""

from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field

from agentswarm.core.domain import ArtifactContext, FindingKind, SectionAssigned, TeamName
from agentswarm.llm.workers import Worker
from agentswarm.teams.analyzer import ReviewingAnalyzer
from agentswarm.teams.decomposer import Decomposer
from agentswarm.teams.executor import (
    CheckReport,
    ExecutorReport,
    ExecutorRequest,
    ExecutorTeam,
    WorktreeToolbox,
)
from agentswarm.workspace.git import Worktree


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class SectionWriting(ExecutorRequest):
    """An executor request that carries its section of the outline: then the writer answers in one call."""

    assignment: SectionAssigned | None = None


class SourcePart(Frozen):
    name: str  # as cited: [@name]
    text: str


class SectionRequest(Frozen):
    """The payload a section writer receives: everything it needs, read by plain code."""

    brief: str  # who the document is for, what it must do, its rules
    file: str  # the section's file
    title: str
    covers: str
    words: int  # at most
    sources: tuple[SourcePart, ...]  # the only facts to use
    current: str  # the section as it is now; empty when it is first written
    must_fix: tuple[str, ...]  # what failed last time: the checks' report, a reviewer's finding


class SectionText(Frozen):
    """The whole section file."""

    text: str = Field(description="the section file: a '## ' heading, then the text, one sentence per line")


class Writer(ExecutorTeam):
    SECTION = """\
You write one section of a document. The data holds the document's brief (who it is for, what it must do,
its rules), what this section covers and its length, and the full text of the sources it draws on: the only
source of facts.

- Write the whole section file: a '## ' heading with the section's title, then the text.
- Serve the brief's reader: what the section covers, specifically, with the facts and numbers that matter.
- Cite every factual statement with the source it comes from, like [@name], using the names in the data.
  State only what a source says.
- One sentence per line. Stay within the word count.
- If `current` holds the section already, revise it to fix everything in `must_fix`; keep what is right."""
    INSTRUCTIONS = """\
You are the writer of a document. Write or revise what `instruction.goal` asks for.

- Use list_files and read_file to see the document as it is: its sections live in sections/, one file
  per section.
- Write in Markdown. Start each section file with a '## ' heading, then the text.
- Put exactly one sentence per line, so changes are easy to review.
- Stay on the topic of your task; don't rewrite other sections unless the task asks.
- Fix every problem in `instruction.must_fix`; follow every rule in `instruction.constraints`.
- Run run_checks and fix what it reports until every check passes.
- When you are done, give your final answer in the required structured format: a one-line summary of the
  change, and whether the task is complete. Do not finish with a plain-text reply."""

    def request(
        self,
        ctx: ArtifactContext,
        worktree: Worktree,
        nudged: bool = False,
        failing: CheckReport | None = None,
        carried: str | None = None,
    ) -> ExecutorRequest:
        """The executor's request, carrying the task's section of the outline when it has one."""
        base = super().request(ctx, worktree, nudged, failing, carried)
        assigned = next((e for e in reversed(ctx.history) if isinstance(e, SectionAssigned)), None)
        if assigned is None:
            return base
        return SectionWriting(
            **{name: getattr(base, name) for name in ExecutorRequest.model_fields}, assignment=assigned
        )

    async def work(
        self, worker: Worker, request: ExecutorRequest, toolbox: WorktreeToolbox
    ) -> ExecutorReport:
        """A section of the outline in one answer, from its sources read by plain code (2026-10-18: writers
        that read with tools re-read their sources until their calls ran out: $4 for one section of ten).
        Other tasks, such as a reviewer's revision, go to the agent that uses the tools."""
        if not isinstance(request, SectionWriting) or request.assignment is None:
            return await super().work(worker, request, toolbox)
        assigned = request.assignment
        sources = tuple(
            SourcePart(name=name, text=text)
            for name in assigned.section.sources
            if (text := toolbox.text(f"sources/{name}.md")) is not None
        )
        section = SectionRequest(
            brief=toolbox.text("docs/brief.md") or "",
            file=assigned.path,
            title=assigned.section.title,
            covers=assigned.section.covers,
            words=assigned.section.words,
            sources=sources,
            current=toolbox.text(assigned.path) or "",
            must_fix=tuple(f.detail for f in request.instruction.must_fix),
        )
        answer = await worker.run(self.SECTION, section, SectionText)
        result = toolbox.write_file(assigned.path, answer.text.strip() + "\n")
        if result.startswith("error:"):
            return ExecutorReport(summary=f"could not write {assigned.path}: {result}", complete=False)
        return ExecutorReport(summary=f"{assigned.path}: {assigned.section.title}", complete=True)


class Outliner(Decomposer):
    NAME = TeamName("outliner")
    INSTRUCTIONS = """\
You are the editor planning a document. The task in the data is too large for one section. Split it into
2 to 6 sections that together accomplish it.

- Each child task describes one section: its topic, what it must cover, and the file to write it in
  (sections/NN-short-name.md, numbered in reading order).
- Each child task must be self-contained: the writer of a section sees only that text.
- Sections must not overlap; together they cover the whole task and nothing else."""


class ConsistencyChecker(ReviewingAnalyzer):
    KIND: ClassVar[FindingKind] = FindingKind.INCONSISTENT
    INSTRUCTIONS: ClassVar[str] = """\
You check a document for consistency. The change in the data has been merged into the shared document.

- Read the changed text and the other sections it relates to.
- Report places where the change contradicts another section, or uses a term, name or number differently.
- Quote the exact text (a few words or more, copied verbatim) and name its file, for every problem.
- Report nothing about style. If everything agrees, report no problems."""


class FactChecker(ReviewingAnalyzer):
    KIND: ClassVar[FindingKind] = FindingKind.UNSUPPORTED
    INSTRUCTIONS: ClassVar[str] = """\
You check a document's claims. The change in the data has been merged into the shared document.

- Report claims in the changed text that are wrong, or so specific that they need a source the text
  doesn't give.
- Quote the exact claim (copied verbatim) and name its file, for every problem.
- Ordinary advice and opinions are not claims. If every claim holds, report no problems."""


class AudienceReviewer(ReviewingAnalyzer):
    KIND: ClassVar[FindingKind] = FindingKind.UNCLEAR
    INSTRUCTIONS: ClassVar[str] = """\
You read a document as its intended reader, someone new to the subject. The change in the data has been
merged into the shared document.

- Report passages a newcomer would not understand: unexplained jargon, missing steps, confusing order.
- Quote the exact passage (copied verbatim) and name its file, for every problem.
- If the text reads well, report no problems."""
