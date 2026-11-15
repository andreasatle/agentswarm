"""The document domain's teams (proposal §10.3): the same classes as for software, with document instructions.

G2: domains are configuration, not separate code. A writer is an executor, an outliner a decomposer, and
the three reviewers are reviewing analyzers, whose evidence is a verbatim quote instead of a failing test.
"""

import re
from collections.abc import Sequence
from typing import Annotated, ClassVar, Literal

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
    task: str  # what to do: write the section of the outline, or revise it for a reviewer's finding
    file: str  # the section's file
    title: str
    covers: str
    words: int  # at most
    sources: tuple[SourcePart, ...]  # the only facts to use
    current: str  # the section as it is now; empty when it is first written
    must_fix: tuple[str, ...]  # what failed last time: the checks' report, a reviewer's finding


class Cite(Frozen):
    source: str = Field(description="a source's name, exactly as given in `sources`")
    page: int | None = Field(
        default=None, description="the page, where the source marks its pages ([page 121])"
    )


class Sentence(Frozen):
    text: str = Field(description="one whole sentence with its final punctuation, and no citation in it")
    cites: list[Cite] = Field(
        default_factory=list[Cite],
        description="the sources its facts come from; empty only for a sentence that states no fact",
    )


class Paragraph(Frozen):
    kind: Literal["paragraph"] = "paragraph"
    sentences: list[Sentence] = Field(min_length=1)


class Bullets(Frozen):
    kind: Literal["list"] = "list"
    items: list[Sentence] = Field(min_length=1)


class Equation(Frozen):
    kind: Literal["equation"] = "equation"
    latex: str = Field(
        description="a displayed formula in LaTeX, without the $$; cite it in the sentence before"
    )


Block = Annotated[Paragraph | Bullets | Equation, Field(discriminator="kind")]


class SectionDraft(Frozen):
    """The section's text as blocks in reading order, under its heading (which plain code writes)."""

    blocks: list[Block] = Field(min_length=1)


class SectionLayout:
    """A typed section laid out as Markdown by plain code: its heading, one sentence per line, a paragraph's
    sentences together, a list's items as bullets, a formula between $$ lines, each sentence's citations
    inside it before its final punctuation, as [@name] or [@name, page 121].

    The writer used to return the Markdown itself, and plain code guessed where its sentences ended and
    moved citations around (abbreviation lists, citations after a full stop or at a line's start): every
    patch improved the guess, none removed it (docs/design/self-correction.md, 2026-11-14)."""

    END: ClassVar[re.Pattern[str]] = re.compile(r"^(.*?)([.!?]+[\"')\]\u201d\u2019]*)?$", re.S)

    @classmethod
    def markdown(cls, title: str, draft: SectionDraft) -> str:
        parts = [f"## {title}"]
        for block in draft.blocks:
            match block:
                case Paragraph():
                    parts.append("\n".join(cls.sentence(s) for s in block.sentences))
                case Bullets():
                    parts.append("\n".join(f"- {cls.sentence(s)}" for s in block.items))
                case Equation():
                    parts.append(f"$$\n{block.latex.strip().strip('$').strip()}\n$$")
        return "\n\n".join(parts) + "\n"

    @classmethod
    def sentence(cls, sentence: Sentence) -> str:
        text = " ".join(sentence.text.split())
        tags = " ".join(
            f"[@{c.source.strip()}, page {c.page}]" if c.page is not None else f"[@{c.source.strip()}]"
            for c in dict.fromkeys(sentence.cites)
        )
        if not tags:
            return text
        found = cls.END.match(text)
        body, end = (found.group(1), found.group(2) or "") if found else (text, "")
        return f"{body.rstrip()} {tags}{end}"

    @staticmethod
    def sentences(draft: SectionDraft) -> list[Sentence]:
        return [
            s
            for b in draft.blocks
            for s in (b.sentences if isinstance(b, Paragraph) else b.items if isinstance(b, Bullets) else [])
        ]

    # A citation as the document checker (evaluation/qasper.py CHECK) finds it, to leave it out of the count.
    CITATION: ClassVar[re.Pattern[str]] = re.compile(r"\[@([^\],\s]+)(?:,[^\]]*)?\]")

    @classmethod
    def words(cls, draft: SectionDraft) -> int:
        """The section's words exactly as the document checker counts them in its Markdown: the heading,
        formulas and citations left out, but a bullet's dash and the full stop a citation leaves apart
        counted. Counting the sentences' own words instead ran 2-4% short of the checker (benchmark r1,
        2026-11-15): sections written to their budget overran the document's cap, and the writer could not
        see why."""
        prose = [b for b in draft.blocks if not isinstance(b, Equation)]
        lines = cls.markdown("", draft.model_copy(update={"blocks": prose})).splitlines()[1:]
        return sum(len(cls.CITATION.sub("", line).split()) for line in lines)

    @classmethod
    def unknown(cls, draft: SectionDraft, names: Sequence[str]) -> list[str]:
        """The sources cited that aren't among the names given."""
        known = set(names)
        return list(
            dict.fromkeys(c.source for s in cls.sentences(draft) for c in s.cites if c.source not in known)
        )


class Overlap:
    """A part's text with the edges of its neighbours in the same document: the last words of the part before
    and the first of the part after, when the writer doesn't have them anyway. A passage that runs across a
    cut between parts is then whole on both sides (parts are cut every few thousand words)."""

    WORDS: ClassVar[int] = 300
    PART: ClassVar[re.Pattern[str]] = re.compile(r"^(.*)-(?:p\d{3}|\d{2})$")

    @classmethod
    def neighbours(cls, name: str, names: Sequence[str]) -> tuple[str | None, str | None]:
        match = cls.PART.match(name)
        if match is None:
            return None, None
        siblings = sorted(
            n for n in names if (m := cls.PART.match(n)) is not None and m.group(1) == match.group(1)
        )
        at = siblings.index(name)
        return (siblings[at - 1] if at > 0 else None), (siblings[at + 1] if at + 1 < len(siblings) else None)

    @classmethod
    def text(cls, own: str, before: str | None, after: str | None) -> str:
        parts: list[str] = []
        if before:
            tail = " ".join(before.split()[-cls.WORDS :])
            parts.append(f"[... the end of the part before, for context]\n{tail}\n")
        parts.append(own)
        if after:
            head = " ".join(after.split("\n", 1)[-1].split()[: cls.WORDS])
            parts.append(f"\n[the start of the part after, for context ...]\n{head}")
        return "\n".join(parts)


class Writer(ExecutorTeam):
    SECTION = """\
You write one section of a document. The data holds the document's brief (who it is for, what it must do,
its rules), your task (write the section, or revise it), what the section covers and its length, and the
full text of the sources it draws on: the only source of facts.

- Give the section's text as blocks in reading order: paragraphs of sentences, a list where the content is a
  list, a displayed formula where one is needed. The heading is written for you.
- Serve the brief's reader: what the section covers, specifically, with the facts and numbers that matter.
- Each sentence is one whole sentence. Give the sources its facts come from in its `cites`, by their names
  in the data exactly, with the page where a source marks its pages ([page 121]); never write a citation into
  the text. State only what a source says.
- Write mathematics in LaTeX: inline as $...$ within a sentence, a displayed formula as an equation block.
  The sources come from PDFs, so their formulas may be garbled: reconstruct the formula in the paper's own
  notation, define every symbol, and cite where it appears in the sentence that introduces it.
- Stay within the word count.
- If `current` holds the section already, revise it as the task says and fix everything in `must_fix`;
  keep what is right."""
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

    OVER: ClassVar[float] = 1.15  # a section this far over its word budget is rewritten
    # Rewrites to shorten, at most: one wasn't enough, Haiku's sections stayed 23-49% over after it and the
    # whole document over its cap, which a strong model was then paid to fix (2026-11-13, $0.40 of $1.31)
    SHORTEN: ClassVar[int] = 3

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
        names = [
            f.removeprefix("sources/").removesuffix(".md")
            for f in toolbox.list_files().splitlines()
            if f.startswith("sources/") and f.endswith(".md")
        ]
        given = set(assigned.section.sources)
        sources: list[SourcePart] = []
        for name in assigned.section.sources:
            own = toolbox.text(f"sources/{name}.md")
            if own is None:
                continue
            before, after = Overlap.neighbours(name, names)
            sources.append(
                SourcePart(
                    name=name,
                    text=Overlap.text(
                        own,
                        toolbox.text(f"sources/{before}.md") if before and before not in given else None,
                        toolbox.text(f"sources/{after}.md") if after and after not in given else None,
                    ),
                )
            )
        section = SectionRequest(
            brief=toolbox.text("docs/brief.md") or "",
            task=request.instruction.goal,
            file=assigned.path,
            title=assigned.section.title,
            covers=assigned.section.covers,
            words=assigned.section.words,
            sources=tuple(sources),
            current=toolbox.text(assigned.path) or "",
            must_fix=tuple(f.detail for f in request.instruction.must_fix),
        )
        draft = await worker.run(self.SECTION, section, SectionDraft)
        unknown = SectionLayout.unknown(draft, names)
        if unknown:  # once: a citation naming no source fails the document check anyway
            wrong = (
                f"The section cites {', '.join(unknown)}, which are not sources; cite only these: "
                f"{', '.join(p.name for p in sources)}."
            )
            draft = await worker.run(
                self.SECTION, self.again(section, draft, assigned.section.title, wrong), SectionDraft
            )
        words = SectionLayout.words(draft)
        for _ in range(self.SHORTEN):  # rewrites told the numbers: writers ran 40% long
            if words <= assigned.section.words * self.OVER:
                break
            too_long = (
                f"The section has {words} words; it may have at most {assigned.section.words}. "
                f"Cut about {words - assigned.section.words} words: "
                "drop the least important sentences, shorten the rest."
            )
            draft = await worker.run(
                self.SECTION, self.again(section, draft, assigned.section.title, too_long), SectionDraft
            )
            words = SectionLayout.words(draft)
        result = toolbox.write_file(assigned.path, SectionLayout.markdown(assigned.section.title, draft))
        if result.startswith("error:"):
            return ExecutorReport(summary=f"could not write {assigned.path}: {result}", complete=False)
        return ExecutorReport(summary=f"{assigned.path}: {assigned.section.title}", complete=True)

    @staticmethod
    def again(section: SectionRequest, draft: SectionDraft, title: str, problem: str) -> SectionRequest:
        """The request once more, with the draft as the current section and what is wrong with it."""
        return section.model_copy(
            update={"current": SectionLayout.markdown(title, draft), "must_fix": (*section.must_fix, problem)}
        )


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
