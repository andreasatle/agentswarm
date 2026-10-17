import asyncio
import json
from pathlib import Path

from pydantic import BaseModel

from agentswarm.board.artifacts import ArtifactFactory
from agentswarm.core.domain import Promoted, QuestionOutcome, Scored, TeamName
from agentswarm.evaluation.qasper import HiddenQuestion
from agentswarm.evaluation.questions import (
    GradeAnswer,
    GradeRequest,
    HiddenQuestions,
    MemoryRequest,
    QuestionProgress,
    QuestionScore,
    QuestionTrace,
    ReaderAnswer,
    ReadingRequest,
)
from agentswarm.llm.workers import Toolbox, Worker
from tests.support.harness import Harness

DATASET = HiddenQuestion(id="q1", paper="p1", question="Which dataset?", answers=("PTB",))
SPEED = HiddenQuestion(id="q2", paper="p1", question="Is it fast?", answers=("No",))
SIZE = HiddenQuestion(id="q3", paper="p1", question="How big?", answers=("1M words",))


class Reader:
    """Answers from what the request holds: the document when there is one, else a fixed memory."""

    def __init__(self, memory: dict[str, str]) -> None:
        self.memory = memory
        self.calls = 0

    async def run[T: BaseModel](
        self, instructions: str, payload: BaseModel, output: type[T], toolbox: Toolbox | None = None
    ) -> T:
        self.calls += 1
        if isinstance(payload, MemoryRequest):
            known = self.memory.get(payload.question, "")
            answer = ReaderAnswer(found=bool(known), answer=known)
        else:
            assert isinstance(payload, ReadingRequest)
            line = next((ln for ln in payload.document.splitlines() if payload.question in ln), "")
            answer = ReaderAnswer(found=bool(line), answer=line.split(":", 1)[-1].strip())
        assert isinstance(answer, output)
        return answer


class Grader:
    """Correct when a reference appears in the answer."""

    async def run[T: BaseModel](
        self, instructions: str, payload: BaseModel, output: type[T], toolbox: Toolbox | None = None
    ) -> T:
        assert isinstance(payload, GradeRequest)
        correct = any(r.lower() in payload.answer.lower() for r in payload.references)
        grade = GradeAnswer(reason="compared", correct=correct)
        assert isinstance(grade, output)
        return grade


def score(reader: Worker) -> QuestionScore:
    return QuestionScore(reader, Grader())


class TestQuestionScore:
    def test_only_answers_the_grader_accepts_count(self) -> None:
        document = "Which dataset?: the PTB treebank\nIs it fast?: yes, very\n"
        answered = asyncio.run(score(Reader({})).answered(document, [DATASET, SPEED, SIZE]))
        assert [(o.id, o.correct, o.answer) for o in answered] == [
            ("q1", True, "the PTB treebank"),
            ("q2", False, "yes, very"),  # wrong
            ("q3", False, ""),  # not in the document
        ]

    def test_memory_is_asked_without_the_document(self) -> None:
        reader = Reader({"Is it fast?": "No"})
        assert asyncio.run(score(reader).remembered({"p1": "Tagging"}, [DATASET, SPEED])) == [False, True]


class TestQuestionProgress:
    def test_the_guide_on_the_base_level_is_scored_without_the_questions_memory_answers(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "h").mkdir()
        h = Harness(tmp_path / "h")
        level = h.repository.checkout_level(Harness.LADDER.base)
        level.write("sources/p1.md", "# Tagging with Little Data\n")
        level.write("sections/01-intro.md", "## Intro\n\nWhich dataset?: PTB\nIs it fast?: No\n")
        level.commit("a guide")
        level.remove()
        path = tmp_path / "held_out" / "questions.json"
        path.parent.mkdir()
        path.write_text(json.dumps([q.model_dump() for q in (DATASET, SPEED, SIZE)]))
        reader = Reader({"Is it fast?": "No"})
        progress = QuestionProgress(HiddenQuestions(path), score(reader), h.repository, Harness.LADDER.base)
        scored = asyncio.run(progress.measure(h.board.current()))
        assert scored.seen is None
        assert scored.held_out is not None and (scored.held_out.passed, scored.held_out.total) == (1, 2)
        assert [(o.id, o.correct) for o in scored.questions] == [("q1", True), ("q3", False)]
        assert HiddenQuestions(path).memorized() == {"q2"}  # found once, kept beside the questions
        calls = reader.calls
        asyncio.run(progress.measure(h.board.current()))
        assert reader.calls - calls == 2  # the next score asks only the two kept questions


class TestQuestionTrace:
    def test_flips_between_scores_are_shown_with_the_revisions_merged_between(self, tmp_path: Path) -> None:
        (tmp_path / "h").mkdir()
        h = Harness(tmp_path / "h")
        level, lowest, base = Harness.LADDER, Harness.LADDER.lowest, Harness.LADDER.base

        def scored(*correct: bool) -> Scored:
            outcomes = tuple(
                QuestionOutcome(id=q.id, correct=c, answer="PTB" if c else "")
                for q, c in zip((DATASET, SPEED), correct, strict=True)
            )
            return Scored(level=base, commit="c", seen=None, held_out=None, spent_usd=0.0, questions=outcomes)

        h.board.append([scored(False, True)])
        task = ArtifactFactory(h.board, level).create(
            "Revise sections/01.md: name the dataset", TeamName("r")
        )
        h.board.append([Promoted(artifact=task.id, from_level=lowest, to_level=base, gate_decision=None)])
        h.board.append([scored(True, False)])
        text = QuestionTrace.render(h.log.read(), [DATASET, SPEED])
        assert "  .x  p1 Which dataset?" in text and "  x.  p1 Is it fast?" in text
        assert "gained: Which dataset? -> 'PTB'" in text and "lost: Is it fast?" in text
        assert f"merged {task.id}: Revise sections/01.md: name the dataset" in text


class TestVotes:
    def test_a_question_is_decided_by_the_majority_of_its_readings(self) -> None:
        class Flaky:
            """Answers right, then wrong, then right."""

            def __init__(self) -> None:
                self.calls = 0

            async def run[T: BaseModel](
                self, instructions: str, payload: BaseModel, output: type[T], toolbox: Toolbox | None = None
            ) -> T:
                self.calls += 1
                answer = ReaderAnswer(found=True, answer="PTB" if self.calls != 2 else "WSJ")
                assert isinstance(answer, output)
                return answer

        reader = Flaky()
        outcome = asyncio.run(QuestionScore(reader, Grader(), votes=3).answered("doc", [DATASET]))[0]
        assert (outcome.correct, outcome.answer, reader.calls) == (True, "PTB", 3)
        steady = Reader({})
        asyncio.run(QuestionScore(steady, Grader(), votes=3).answered("Which dataset?: PTB", [DATASET]))
        assert steady.calls == 2  # two agreeing readings decide: the third isn't asked
