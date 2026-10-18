"""The specifier: drafts a project's specification from its goal, before anything is built
(docs/design/goals.md).

Until now every project came with a written specification. The specifier writes one from the goal: sections,
each rule stated precisely with examples, and a list of open questions, each with the default the draft
assumes (ClarifyGPT: detect what is ambiguous and ask, rather than guess silently). The questions are
answered, or not: by nobody (the defaults stand), or by a simulated user who has read a reference text and
answers only from it, quoting it word for word, which plain code checks (TiCoder and ClarifyGPT simulate their
users from a reference the same way). The draft is revised with the answers and committed on the base level,
and only then does the building start: the planner, check writer, builders and red teams take it as spec.
"""

import json
import re
from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field

from agentswarm.board.artifacts import ArtifactFactory
from agentswarm.board.blackboard import Blackboard
from agentswarm.core.config import BriefConfig, LevelLadder, SpecifyConfig
from agentswarm.core.domain import (
    Briefed,
    Event,
    OutlineEntry,
    Paused,
    Reference,
    SectionAssigned,
    Sized,
    Specified,
    TeamName,
)
from agentswarm.llm.workers import Worker, WorkerError
from agentswarm.teams.referee import Referee
from agentswarm.workspace.git import Repository


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class DraftRequest(Frozen):
    """The payload a specifier receives."""

    goal: str
    north_star: str
    max_questions: int


class SpecPart(Frozen):
    title: str = Field(description="the section's heading, without '#'")
    text: str = Field(description="the section: each rule stated precisely, each with an example or two")


class OpenQuestion(Frozen):
    question: str = Field(description="what the goal leaves open, as a question a user could answer briefly")
    default: str = Field(description="what the draft assumes until it is answered")


class SpecDraft(Frozen):
    """The specification, in sections, and what it had to assume."""

    title: str
    sections: tuple[SpecPart, ...] = Field(min_length=1)
    questions: tuple[OpenQuestion, ...] = ()


class Answer(Frozen):
    question: str
    answer: str  # "not specified" when nobody answered, or the reference is silent


class ReviseRequest(Frozen):
    """The payload a specifier receives to revise its draft with the answers."""

    goal: str
    draft: str  # the draft as Markdown
    answers: tuple[Answer, ...]


class AnswerRequest(Frozen):
    """The payload a simulated user receives: one question, and the reference they have read."""

    question: str
    reference: str


class UserAnswer(Frozen):
    """Your answer, from the reference alone."""

    answer: str = Field(description="a brief answer; 'not specified' if the reference doesn't say")
    quote: str = Field(description="the reference's exact words the answer rests on; empty if not specified")


class Specifier:
    """Plain code around the specifier's calls: draft, questions, answers, revision, commit."""

    NAME = "specifier"
    UNSPECIFIED = "not specified"
    REFERENCE_LIMIT = 400_000  # characters of the reference given to the simulated user
    INSTRUCTIONS = """\
You write the specification for a software project from its goal, before anything is built. Builders will
implement it, another team will derive tests from it, and a referee will settle disputes by quoting it, so it
must be precise enough to decide what is right for any input.

- Write it in sections, in the order a reader needs them: the interface first, then the rules, then errors.
- State each rule exactly, with an example or two (input and the expected result). Say what must be rejected,
  and how.
- Where the goal leaves something open, decide it, write it into the specification, and list it as an open
  question with the default you chose. List the questions whose answers would change the most, at most the
  number in the data."""
    REVISE = """\
You revise a software project's specification with the answers to the questions it left open. Change what the
answers decide; where an answer is "not specified", keep the default. Keep everything else, and keep it as
precise: each rule stated exactly, with examples. Give the whole revised specification, with no questions."""
    ANSWER = """\
You are the user who set a software project's goal, and you have read the reference in the data. Answer the
question from the reference alone, briefly, and quote its exact words. If the reference doesn't decide it,
answer "not specified" and leave the quote empty: don't guess, and don't add anything the reference doesn't
say."""

    def __init__(
        self,
        config: SpecifyConfig,
        north_star: str,
        board: Blackboard,
        repository: Repository,
        ladder: LevelLadder,
        worker: Worker,
        answerer: Worker | None = None,
        reference: str = "",
    ) -> None:
        self._config = config
        self._north_star = north_star
        self._board = board
        self._repository = repository
        self._ladder = ladder
        self._worker = worker
        self._answerer = answerer
        self._reference = reference

    def done(self) -> bool:
        """Whether the specification is on the base level: then the building may start."""
        return self._config.path in self._repository.files_at(self._ladder.base)

    async def step(self) -> int:
        """Drafts, asks, revises and commits the specification, once. Returns the actions taken."""
        if self.done():
            return 0
        request = DraftRequest(
            goal=self._config.goal, north_star=self._north_star, max_questions=self._config.max_questions
        )
        try:
            draft = await self._worker.run(self.INSTRUCTIONS, request, SpecDraft)
        except WorkerError as error:
            raise SpecifierError(f"the specifier failed: {error}") from error
        questions = draft.questions[: self._config.max_questions]
        answers = await self.answers(questions)
        final = draft
        if any(a.answer != self.UNSPECIFIED for a in answers):
            revise = ReviseRequest(goal=self._config.goal, draft=self.markdown(draft), answers=tuple(answers))
            try:
                final = await self._worker.run(self.REVISE, revise, SpecDraft)
            except WorkerError as error:
                raise SpecifierError(f"the specifier's revision failed: {error}") from error
        text = self.markdown(final, answers)
        level = self._repository.checkout_level(self._ladder.base)
        try:
            level.write(self._config.path, text)
            commit = level.commit(f"specifier: {self._config.path} drafted from the goal")
        finally:
            level.remove()
        answered = sum(a.answer != self.UNSPECIFIED for a in answers)
        self._board.append(
            [
                Specified(
                    path=self._config.path,
                    commit=commit,
                    model=self._config.model,
                    sections=len(final.sections),
                    questions=tuple(q.question for q in questions),
                    answered=answered,
                    characters=len(text),
                )
            ]
        )
        return 1

    async def answers(self, questions: Sequence[OpenQuestion]) -> list[Answer]:
        """Each question answered by the simulated user from the reference, or left to its default. An
        answer whose quote isn't in the reference word for word doesn't count: it may be the user's guess."""
        if self._answerer is None or not self._reference:
            return [Answer(question=q.question, answer=self.UNSPECIFIED) for q in questions]
        found: list[Answer] = []
        for question in questions:
            request = AnswerRequest(
                question=question.question, reference=self._reference[: self.REFERENCE_LIMIT]
            )
            try:
                reply = await self._answerer.run(self.ANSWER, request, UserAnswer)
            except WorkerError:
                found.append(Answer(question=question.question, answer=self.UNSPECIFIED))
                continue
            quoted = bool(reply.quote.strip()) and Referee.around(self._reference, reply.quote, 0) is not None
            answer = reply.answer if quoted else self.UNSPECIFIED
            found.append(Answer(question=question.question, answer=answer))
        return found

    @classmethod
    def markdown(cls, draft: SpecDraft, answers: Sequence[Answer] = ()) -> str:
        """The specification as Markdown: a section per `## ` heading, then what was asked and decided."""
        parts = [f"# {draft.title}", ""]
        for section in draft.sections:
            parts += [f"## {section.title}", "", section.text.strip(), ""]
        if answers:
            parts += ["## Questions asked while drafting", ""]
            parts += [f"- {a.question} Answer: {a.answer}." for a in answers]
            parts.append("")
        return "\n".join(parts)


class SpecifierError(RuntimeError):
    """No specification could be drafted: the project can't start without one."""


class SourceSummary(Frozen):
    name: str  # the source's name: its file in the sources folder, without the extension
    title: str  # its first line: the document, and which part or pages
    words: int
    opening: str = ""  # its first words, to tell what the part holds


class BriefRequest(Frozen):
    """The payload a briefer receives."""

    goal: str
    sources: tuple[SourceSummary, ...]
    words: int  # the document's length cap


class OutlinePart(Frozen):
    title: str = Field(description="the section's heading")
    covers: str = Field(description="what the section must cover, specifically, for the audience")
    sources: tuple[str, ...] = Field(
        description="the names of the sources it draws on, as listed in the data"
    )
    words: int = Field(gt=0, description="its share of the length cap")


class SourceReference(Frozen):
    document: str = Field(description="the source name the document's parts share, without -p001 or -01")
    authors: str = Field(description="as on the first page: 'LeCun, Y., Chopra, S., Hadsell, R., et al.'")
    title: str
    year: str
    venue: str = Field(description="journal, conference, book or report; 'arXiv preprint <id>' if that's all")
    short: str = Field(
        description="the in-text citation: the first author's surname, 'et al.' for three or more "
        "authors, and the year: 'LeCun et al., 2006'; never the paper's name"
    )


class BriefDraft(Frozen):
    """The brief: who the document is for and what it must do, its outline, and its sources' references."""

    title: str
    audience: str = Field(description="who reads it, what they know already, and what they will use it for")
    purpose: str = Field(description="what the reader must be able to do or understand afterwards")
    style: str = Field(description="tone, level of detail, terms to explain or avoid")
    sections: tuple[OutlinePart, ...] = Field(min_length=1)
    references: tuple[SourceReference, ...] = Field(
        default=(), description="one per source document (not per part), from its first part's opening"
    )


class Briefer:
    """A document's specifier: drafts its brief and outline from the user's goal and the sources, commits the
    brief, pauses for the user to read and edit it (if `approve`), then starts one writing task per section
    of the outline. Sections of a known size, each from named sources, rather than one task for the whole
    document: one writer reading ten papers ran out of steps twice (2026-10-17)."""

    NAME = TeamName("briefer")
    OPENING = 60  # words of each source shown to the briefer, after its title
    TITLE_PAGE = 250  # words of a document's first part: its title page, for the reference
    REFERENCES = "docs/references.json"  # the references, beside the brief, for assembling the document
    INSTRUCTIONS = """\
You plan a document from the user's goal and the sources listed in the data, before anything is written.
Writers will write one section each from your outline, using only the sources; reviewers will judge the
document against your brief.

- Decide the audience (who reads it, what they know, what they will use it for), the purpose (what the
  reader must understand or be able to do afterwards) and the style, from the goal. Where the goal doesn't
  say, choose what serves the reader best.
- Outline the document in sections, in reading order. For each, say specifically what it covers and which
  sources it draws on (by name, as listed), and give it a share of the length cap; the shares add up to at
  most the cap.
- Long documents come as parts (pages or chunks of one document, each listed with its opening words). A
  section's writer reads only the parts you name, and must hold them all in mind at once, so name the ones
  that hold what it covers, at most six (some 24,000 words), not every part of a long document.
- Every document should be used somewhere; leave one out only if the goal makes it irrelevant.
- Give each source document's reference (authors, title, year, venue, and the short in-text form), from the
  opening of its first part, where the title page is. Only what that page shows: leave out what it doesn't."""
    RULES = """\
## Rules

- The document lives in `sections/`, one Markdown file per section of the outline, numbered in its order \
(`sections/01-...md`), each starting with a `## ` heading.
- At most {words} words in all.
- Every factual statement cites the source it comes from, like `[@{example}]`, naming a file in `sources/` \
without its extension. The sources are the only source of facts.
- One sentence per line.
"""

    def __init__(
        self,
        config: BriefConfig,
        board: Blackboard,
        repository: Repository,
        ladder: LevelLadder,
        artifacts: ArtifactFactory,
        worker: Worker,
    ) -> None:
        self._config = config
        self._board = board
        self._repository = repository
        self._ladder = ladder
        self._artifacts = artifacts
        self._worker = worker

    def done(self) -> bool:
        """The brief is drafted and its writing tasks exist: the rest of the project may run."""
        return any(a.created_by == self.NAME for a in self._board.current().artifacts())

    async def step(self) -> int:
        views = self._board.current()
        briefs = views.briefs()
        if not briefs:
            return await self.draft()
        if views.paused:
            return 0  # the user reads the brief; `agentswarm resume` starts the writing
        brief = briefs[-1]
        for number, part in enumerate(brief.outline, 1):
            artifact = self._artifacts.create(self.task(number, part), self.NAME)
            assigned = SectionAssigned(artifact=artifact.id, path=self.path(number, part), section=part)
            self._board.append([assigned, Sized(artifact=artifact.id, fits=True)])  # tried whole
        return 1

    async def draft(self) -> int:
        level = self._repository.checkout_level(self._ladder.base)
        try:
            folder = f"{self._config.sources}/"
            sources = tuple(
                self.summary(path.removeprefix(folder).removesuffix(".md"), level.read(path))
                for path in sorted(level.files())
                if path.startswith(folder) and path.endswith(".md")
            )
            request = BriefRequest(goal=self._config.goal, sources=sources, words=self._config.words)
            try:
                draft = await self._worker.run(self.INSTRUCTIONS, request, BriefDraft)
            except WorkerError as error:
                raise SpecifierError(f"the briefer failed: {error}") from error
            drafted = tuple(
                Reference(
                    document=r.document,
                    authors=r.authors,
                    title=r.title,
                    year=r.year,
                    venue=r.venue,
                    short=r.short,
                )
                for r in draft.references
            )
            given = self.given(level.read(self.REFERENCES) if self.REFERENCES in level.files() else "")
            references = self.merged(drafted, given)
            level.write(self._config.path, self.markdown(draft, sources, self._config.words))
            level.write(self.REFERENCES, json.dumps([r.model_dump() for r in references], indent=1) + "\n")
            commit = level.commit(f"briefer: {self._config.path} drafted from the goal")
        finally:
            level.remove()
        outline = tuple(
            OutlineEntry(title=p.title, covers=p.covers, sources=p.sources, words=p.words)
            for p in draft.sections
        )
        events: list[Event] = [
            Briefed(
                path=self._config.path,
                commit=commit,
                model=self._config.model,
                outline=outline,
                references=references,
            )
        ]
        if self._config.approve:
            events.append(
                Paused(
                    reason=f"the brief is drafted: read {self._config.path} in the levels folder, edit "
                    "it if you like, then `agentswarm resume` to start the writing"
                )
            )
        self._board.append(events)
        return 1

    @staticmethod
    def given(text: str) -> tuple[Reference, ...]:
        """References that came with the sources (an arXiv record, from `find-papers`)."""
        return tuple(Reference.model_validate(r) for r in json.loads(text)) if text.strip() else ()

    @staticmethod
    def merged(drafted: Sequence[Reference], given: Sequence[Reference]) -> tuple[Reference, ...]:
        """A given reference replaces the drafted one for its document: a record beats a model's reading of
        a title page (the PDF of DeepSeek-R1's second version is dated 2026, the paper 2025)."""
        known = {r.document: r for r in given}
        kept = [known.pop(r.document, r) for r in drafted]
        return tuple(kept + list(known.values()))

    @classmethod
    def summary(cls, name: str, text: str) -> SourceSummary:
        """A source as the briefer sees it: its title line, its size and its first words (more of a
        document's first part, where its title page is)."""
        title, _, body = text.partition("\n")
        first = (
            re.search(r"-(?:p0*1|0*1)$", name) is not None or re.search(r"-(?:p\d{3}|\d{2})$", name) is None
        )
        return SourceSummary(
            name=name,
            title=title.lstrip("# ").strip(),
            words=len(text.split()),
            opening=" ".join(body.split()[: cls.TITLE_PAGE if first else cls.OPENING]),
        )

    @staticmethod
    def path(number: int, part: OutlineEntry) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", part.title.lower()).strip("-")[:40] or "section"
        return f"sections/{number:02d}-{slug}.md"

    def task(self, number: int, part: OutlineEntry) -> str:
        sources = ", ".join(f"sources/{s}.md" for s in part.sources) or "the sources"
        return (
            f'Write {self.path(number, part)}, the section "{part.title}" of the document '
            f"{self._config.path} describes: {part.covers} Draw on {sources}. At most {part.words} words; "
            "cite every fact; follow the brief's rules."
        )

    @classmethod
    def markdown(cls, draft: BriefDraft, sources: Sequence[SourceSummary], words: int) -> str:
        outline = "\n".join(
            f"{n}. **{p.title}** (at most {p.words} words; from {', '.join(p.sources) or 'the sources'}): "
            f"{p.covers}"
            for n, p in enumerate(draft.sections, 1)
        )
        cited = {r.document: r for r in draft.references}
        documents = [
            f"- {r.short}: {r.authors} ({r.year}). {r.title}. {r.venue}".rstrip(". ") for r in cited.values()
        ]
        listed = "\n".join([*documents, "", "Their parts, cited by name:", ""] if documents else [])
        listed += "\n".join(f"- `{s.name}`: {s.title}" for s in sources)
        example = sources[0].name if sources else "name"
        return (
            f"# The brief: {draft.title}\n\n## Audience\n\n{draft.audience}\n\n"
            f"## Purpose\n\n{draft.purpose}\n\n"
            f"## Style\n\n{draft.style}\n\n## Outline\n\n{outline}\n\n## Sources\n\n{listed}\n\n"
            + cls.RULES.format(words=words, example=example)
        )
