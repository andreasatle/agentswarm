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

from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field

from agentswarm.board.blackboard import Blackboard
from agentswarm.core.config import LevelLadder, SpecifyConfig
from agentswarm.core.domain import Specified
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
