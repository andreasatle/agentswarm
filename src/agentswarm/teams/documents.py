"""The document domain's teams (proposal §10.3): the same classes as for software, with document instructions.

G2: domains are configuration, not separate code. A writer is an executor, an outliner a decomposer, and
the three reviewers are reviewing analyzers, whose evidence is a verbatim quote instead of a failing test.
"""

import re
from collections.abc import Mapping, Sequence
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


class Citations:
    """Mends citations that name a page as if it were a source: [@report-p123] becomes
    [@report-p118, page 123] when the part report-p118 holds pages 118 to 135 (2026-10-18: a writer cited
    pages that way three times in a row; the page is right, only the form is wrong)."""

    PAGED: ClassVar[re.Pattern[str]] = re.compile(r"\[@([^\],\s]+)-p(\d{3})(?:,\s*page\s+(\d+))?\]")

    GROUP: ClassVar[re.Pattern[str]] = re.compile(r"\[(@[^\]]*?;?\s*@[^\]]*)\]")

    @classmethod
    def separate(cls, text: str) -> str:
        """[@a, @b] or [@a; @b, page 3] as one citation each: [@a] [@b, page 3]."""

        def apart(match: re.Match[str]) -> str:
            pieces = [p.strip() for p in re.split(r"[;,]\s*(?=@)", match.group(1))]
            return " ".join(f"[{p}]" for p in pieces if p)

        return cls.GROUP.sub(apart, text)

    @classmethod
    def mend(cls, text: str, names: Sequence[str]) -> str:
        text = cls.separate(text)
        known = set(names)
        starts: dict[str, list[int]] = {}
        for name in names:
            if (paged := re.fullmatch(r"(.+)-p(\d{3})", name)) is not None:
                starts.setdefault(paged.group(1), []).append(int(paged.group(2)))

        def fixed(match: re.Match[str]) -> str:
            whole = f"{match.group(1)}-p{match.group(2)}"
            if whole in known or match.group(1) not in starts:
                return match.group(0)
            page = int(match.group(3) or match.group(2))
            first = max((p for p in starts[match.group(1)] if p <= page), default=None)
            if first is None:
                return match.group(0)
            return f"[@{match.group(1)}-p{first:03d}, page {page}]"

        return cls.PAGED.sub(fixed, text)

    SPOKEN: ClassVar[re.Pattern[str]] = re.compile(r"\(([^()\n]{3,200})\)")

    @classmethod
    def spoken(cls, text: str, headings: Mapping[str, str]) -> str:
        """Citations written the way a reader sees them, by a source's heading, as tags: "(Wikipedia: Maritime
        republics, part 1 of 3)" becomes [@wikipedia-maritime-republics-01] when that is the heading of the
        part given (2026-10-19: a writer cited a whole Venice section that way three times and failed the
        check each time). `headings` maps each part's heading to its name; a parenthesis that isn't one, or
        several separated by semicolons, is left alone."""
        known = {cls.letters(h): name for h, name in headings.items()}
        for heading, name in headings.items():  # "Maritime republics" for its first part as well
            whole = re.sub(r",\s*(?:part 1 of \d+|pages 1-\d+)$", "", heading)
            known.setdefault(cls.letters(whole), name)

        def tagged(match: re.Match[str]) -> str:
            names: list[str] = []
            for piece in match.group(1).split(";"):
                cited = piece.strip()
                name = known.get(cls.letters(cited)) or known.get(cls.letters(cited.split(":", 1)[-1]))
                if name is None:
                    return match.group(0)
                names.append(name)
            return " ".join(f"[@{n}]" for n in names)

        return cls.SPOKEN.sub(tagged, text)

    @staticmethod
    def letters(text: str) -> str:
        return re.sub(r"[^a-z0-9]", "", text.lower())


class Sentences:
    """Puts each sentence on its own line, by the document checker's own rule (tools/check_doc.py): writers
    run sentences together ("Architecture. The context encoder ..."), and the checker then fails a correct
    section (2026-10-18: three of eleven tutorial sections). Lists and tables are left as they are."""

    END: ClassVar[re.Pattern[str]] = re.compile(r"([.!?]) +(?=[A-Z])")
    ABBREVIATION: ClassVar[re.Pattern[str]] = re.compile(
        r"(?:\b(?:[A-Za-z]\.){2,}|\b(?:Mr|Mrs|Ms|Dr|St|Jr|Sr|No|Nos|Fig|Vol|vs|cf|al|Inc|Co|Corp|Ltd|Eq|Sec)\.)$"
    )

    @classmethod
    def lines(cls, text: str) -> str:
        out: list[str] = []
        display = False  # inside a $$ ... $$ block: mathematics, left as it is
        for line in text.splitlines():
            if line.strip().startswith("$$"):
                display = not display if line.strip() == "$$" or not line.strip().endswith("$$") else display
                out.append(line)
                continue
            listed = (
                re.match(r"\s*(?:[-*|>#]|\d+[.)]\s)", line) is not None
            )  # lists, tables, quotes, headings
            if display or listed or not line.strip():
                out.append(line)
                continue
            out += cls.split(line)
        return "\n".join(out)

    @classmethod
    def split(cls, line: str) -> list[str]:
        parts: list[str] = []
        start = 0
        for match in cls.END.finditer(line):
            before = line[start : match.start() + 1]
            inside = before.count("[") > before.count("]") or line[: match.start()].count("$") % 2 == 1
            if cls.ABBREVIATION.search(before) or inside:
                continue  # "p.m. EST", "Fig. 3", or inside a citation or an inline formula
            parts.append(before.strip())
            start = match.end()
        parts.append(line[start:].strip())
        return [p for p in parts if p]


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
its rules), what this section covers and its length, and the full text of the sources it draws on: the only
source of facts.

- Write the whole section file: a '## ' heading with the section's title, then the text.
- Serve the brief's reader: what the section covers, specifically, with the facts and numbers that matter.
- Cite every factual statement with the source it comes from, like [@name], using the names in the data
  exactly. Where a source marks its pages ([page 121]), name the page too: [@name, page 121]. State only
  what a source says.
- Write mathematics in LaTeX: inline as $...$, a displayed formula as $$ on a line of its own, the formula,
  and $$ again. The sources come from PDFs, so their formulas may be garbled: reconstruct the formula in the
  paper's own notation, define every symbol, and cite where it appears.
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

    OVER: ClassVar[float] = 1.15  # a section this far over its word budget is rewritten once
    CITATION: ClassVar[re.Pattern[str]] = re.compile(r"\[@[^\],\s]+(?:,[^\]]*)?\]")

    @classmethod
    def words(cls, text: str) -> int:
        """Words of text under the heading, citations left out: as the document checker counts them."""
        body = [line for line in text.splitlines()[1:] if line.strip() and not line.startswith("#")]
        return sum(len(cls.CITATION.sub("", line).split()) for line in body)

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
            file=assigned.path,
            title=assigned.section.title,
            covers=assigned.section.covers,
            words=assigned.section.words,
            sources=tuple(sources),
            current=toolbox.text(assigned.path) or "",
            must_fix=tuple(f.detail for f in request.instruction.must_fix),
        )
        answer = await worker.run(self.SECTION, section, SectionText)
        words = self.words(answer.text)
        if words > assigned.section.words * self.OVER:  # one rewrite told the numbers: writers ran 40% long
            too_long = (
                f"The section has {words} words; it may have at most {assigned.section.words}. Shorten it."
            )
            again = section.model_copy(
                update={"current": answer.text, "must_fix": (*section.must_fix, too_long)}
            )
            answer = await worker.run(self.SECTION, again, SectionText)
        headings = {
            (toolbox.text(f"sources/{n}.md") or "").split("\n", 1)[0].lstrip("# ").strip(): n for n in names
        }
        headings.pop("", None)
        text = Sentences.lines(Citations.mend(Citations.spoken(answer.text.strip(), headings), names))
        result = toolbox.write_file(assigned.path, text + "\n")
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
