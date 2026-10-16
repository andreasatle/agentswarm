"""Scoring a document by questions no agent sees (docs/design/documents.md).

The counterpart of held-out tests for a document: each question about the sources is put to a reader model
that sees only the document, and a grader model compares its answer with the annotators' answers (QA-based
evaluation, as QAGS and QuestEval do for summaries). A question the reader answers correctly from its own
memory, with no document at all, measures the model rather than the document: it is dropped once, before the
first score, and the dropped ones are kept beside the questions so every later score uses the same set.
"""

import itertools
import json
from collections.abc import Sequence
from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field

from agentswarm.board.blackboard import BoardViews
from agentswarm.core.domain import (
    ArtifactCreated,
    ArtifactId,
    Envelope,
    Level,
    Promoted,
    QuestionOutcome,
    Scored,
    Tally,
)
from agentswarm.evaluation.qasper import HiddenQuestion
from agentswarm.llm.workers import Worker, WorkerError
from agentswarm.workspace.git import Repository


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ReadingRequest(Frozen):
    """The payload a reader receives: a document and one question about what it covers."""

    document: str
    question: str


class MemoryRequest(Frozen):
    """The payload a reader receives to answer from what it already knows: no document."""

    paper: str  # the title of the paper the question is about
    question: str


class ReaderAnswer(Frozen):
    found: bool = Field(description="true if you could answer; false if the answer isn't there")
    answer: str = Field(description="the answer in a sentence or two; empty when not found")


class GradeRequest(Frozen):
    """The payload a grader receives."""

    question: str
    answer: str  # the reader's
    references: tuple[str, ...]  # the annotators' answers, each acceptable


class GradeAnswer(Frozen):
    """Your grade of one answer: first your reasoning, then the verdict it leads to."""

    reason: str = Field(description="what the references say, and whether the answer says the same")
    correct: bool = Field(description="true if the answer agrees with at least one reference in substance")


class QuestionScore:
    """Puts questions to a reader and grades its answers; one reader call and one grader call each."""

    READ = """\
You answer a question about some research papers using only the document in the data. Don't use anything
you know from elsewhere: if the document doesn't answer the question, say it isn't found.

- Answer in a sentence or two, as specifically as the document allows (names, numbers, yes or no)."""
    REMEMBER = """\
You answer a question about a research paper from what you know, without the paper. If you don't know the
answer, say it isn't found rather than guess.

- Answer in a sentence or two, as specifically as you can (names, numbers, yes or no)."""
    GRADE = """\
You grade an answer to a question about a research paper against reference answers written by people who
read the paper. Any one reference may be the right one; they can differ in detail.

- The answer is correct if it agrees in substance with at least one reference: the same facts, names or
  numbers, or the same yes or no. Wording doesn't matter; a vaguer answer that misses what the reference
  states is not correct, and neither is one that adds a claim contradicting it."""

    def __init__(self, reader: Worker, grader: Worker) -> None:
        self._reader = reader
        self._grader = grader

    async def answered(self, document: str, questions: Sequence[HiddenQuestion]) -> list[QuestionOutcome]:
        """Each question, answered by the reader from the document alone, and graded."""
        found: list[QuestionOutcome] = []
        for question in questions:
            request = ReadingRequest(document=document, question=question.question)
            correct, answer = await self.graded(self.READ, request, question)
            found.append(QuestionOutcome(id=question.id, correct=correct, answer=answer))
        return found

    async def remembered(self, titles: dict[str, str], questions: Sequence[HiddenQuestion]) -> list[bool]:
        """Whether the reader answers each question correctly with no document, from memory."""
        found: list[bool] = []
        for question in questions:
            request = MemoryRequest(
                paper=titles.get(question.paper, question.paper), question=question.question
            )
            found.append((await self.graded(self.REMEMBER, request, question))[0])
        return found

    async def graded(
        self, instructions: str, request: BaseModel, question: HiddenQuestion
    ) -> tuple[bool, str]:
        """Whether the answer is correct, and the answer. A failed call counts as not answered: it never
        raises the score."""
        try:
            answer = await self._reader.run(instructions, request, ReaderAnswer)
        except WorkerError:
            return False, ""
        if not answer.found or not answer.answer.strip():
            return False, ""
        grade = GradeRequest(question=question.question, answer=answer.answer, references=question.answers)
        try:
            return (await self._grader.run(self.GRADE, grade, GradeAnswer)).correct, answer.answer
        except WorkerError:
            return False, answer.answer


class HiddenQuestions:
    """The questions file beside a seed, and the ones dropped as answerable from memory."""

    MEMORIZED: ClassVar[str] = "memorized.json"

    def __init__(self, path: Path) -> None:
        self._path = path  # questions.json

    def all(self) -> list[HiddenQuestion]:
        return [HiddenQuestion.model_validate(q) for q in json.loads(self._path.read_text())]

    def memorized(self) -> set[str] | None:
        """The ids answered from memory; None until they have been found."""
        path = self._path.parent / self.MEMORIZED
        return set(json.loads(path.read_text())) if path.is_file() else None

    def remember(self, ids: Sequence[str]) -> None:
        (self._path.parent / self.MEMORIZED).write_text(json.dumps(sorted(ids), indent=1))


class QuestionProgress:
    """The guide on the first shared level, scored by hidden questions; a `Measure`."""

    SECTIONS = "sections"
    SOURCES = "sources"

    def __init__(
        self, questions: HiddenQuestions, score: QuestionScore, repository: Repository, level: Level
    ) -> None:
        self._questions = questions
        self._score = score
        self._repository = repository
        self._level = level

    async def measure(self, views: BoardViews) -> Scored:
        tip = self._repository.tip(self._level)
        worktree = self._repository.checkout_detached(tip, "_questions")
        try:
            files = sorted(
                f for f in worktree.files() if f.startswith(f"{self.SECTIONS}/") and f.endswith(".md")
            )
            document = "\n\n".join(worktree.read(f) for f in files)
            titles = {
                f.removeprefix(f"{self.SOURCES}/").removesuffix(".md"): worktree.read(f).split("\n", 1)[0]
                for f in worktree.files()
                if f.startswith(f"{self.SOURCES}/") and f.endswith(".md")
            }
        finally:
            worktree.remove()
        questions = await self.kept(titles)
        answered = await self._score.answered(document, questions) if document.strip() else []
        return Scored(
            level=self._level,
            commit=tip,
            seen=None,
            held_out=Tally(passed=sum(o.correct for o in answered), total=len(questions)),
            questions=tuple(answered),
            spent_usd=views.spent()[0],
        )

    async def kept(self, titles: dict[str, str]) -> list[HiddenQuestion]:
        """The questions the reader can't answer from memory; found once, then reused."""
        questions = self._questions.all()
        memorized = self._questions.memorized()
        if memorized is None:
            known = await self._score.remembered(
                {id: title.removeprefix("# ") for id, title in titles.items()}, questions
            )
            memorized = {q.id for q, k in zip(questions, known, strict=True) if k}
            self._questions.remember(sorted(memorized))
        return [q for q in questions if q.id not in memorized]


class QuestionTrace:
    """Question by question across a project's scores, and the revisions merged between each two: what each
    stretch of work gained and lost, to see whether revisions add what readers ask about or push it out."""

    TASK = 110  # characters of a revision's task shown

    @classmethod
    def render(cls, envelopes: Sequence[Envelope], questions: Sequence[HiddenQuestion]) -> str:
        asked = {q.id: q for q in questions}
        tasks: dict[ArtifactId, str] = {}
        merged: list[ArtifactId] = []  # merged since the last score
        scores: list[tuple[Scored, list[ArtifactId]]] = []
        for envelope in envelopes:
            match envelope.event:
                case ArtifactCreated(artifact=artifact):
                    tasks[artifact.id] = artifact.task
                case Promoted(artifact=id):
                    merged.append(id)
                case Scored() as scored if scored.questions:
                    scores.append((scored, merged))
                    merged = []
                case _:
                    pass
        if not scores:
            return "no score with questions on this board"
        ids = [o.id for o in scores[0][0].questions]
        lines = [f"{len(scores)} scores, {len(ids)} questions ('x' answered, '.' not), first score first:"]
        for id in ids:
            marks = "".join("x" if cls.correct(s, id) else "." for s, _ in scores)
            question = asked.get(id)
            text = f"{question.paper} {question.question}" if question is not None else id
            lines.append(f"  {marks}  {text[:90]}")
        for n, ((before, _), (after, between)) in enumerate(itertools.pairwise(scores), 2):
            gained = [i for i in ids if cls.correct(after, i) and not cls.correct(before, i)]
            lost = [i for i in ids if cls.correct(before, i) and not cls.correct(after, i)]
            if not gained and not lost:
                continue
            lines.append(f"score {n - 1} -> {n} (${after.spent_usd - before.spent_usd:.2f}):")
            for label, changed, scored in (("gained", gained, after), ("lost", lost, before)):
                for i in changed:
                    question = asked.get(i)
                    answer = next((o.answer for o in scored.questions if o.id == i), "")
                    lines.append(f"  {label}: {question.question if question else i} -> {answer[:80]!r}")
            for id in between:
                lines.append(f"  merged {id}: {tasks.get(id, '')[: cls.TASK]}")
        return "\n".join(lines)

    @staticmethod
    def correct(scored: Scored, id: str) -> bool:
        return any(o.id == id and o.correct for o in scored.questions)
