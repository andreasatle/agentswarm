from pathlib import Path

from pydantic import BaseModel

from agentswarm.board.artifacts import ArtifactFactory
from agentswarm.core.config import BriefConfig, SpecifyConfig
from agentswarm.core.domain import Resumed, Specified
from agentswarm.llm.workers import FakeWorker, Step, Toolbox
from agentswarm.teams.specifier import (
    AnswerRequest,
    BriefDraft,
    Briefer,
    BriefRequest,
    OpenQuestion,
    OutlinePart,
    ReviseRequest,
    SpecDraft,
    Specifier,
    SpecPart,
    UserAnswer,
)
from tests.support.harness import Harness

REFERENCE = "Greetings. hello(name) returns 'Hello, ' followed by the name and '!'. Names may be empty.\n"


def draft(*questions: OpenQuestion) -> Step[BaseModel]:
    def step(toolbox: Toolbox | None) -> BaseModel:
        return SpecDraft(
            title="Greeter",
            sections=(SpecPart(title="Interface", text="hello(name) returns a greeting, e.g. 'Hi, Ada'."),),
            questions=questions,
        )

    return step


def revised(toolbox: Toolbox | None) -> BaseModel:
    return SpecDraft(
        title="Greeter",
        sections=(SpecPart(title="Interface", text="hello('Ada') returns 'Hello, Ada!'."),),
    )


def reply(answer: str, quote: str) -> Step[BaseModel]:
    def step(toolbox: Toolbox | None) -> BaseModel:
        return UserAnswer(answer=answer, quote=quote)

    return step


FORM = OpenQuestion(question="What exactly does the greeting say?", default="'Hi, <name>'")
EMPTY = OpenQuestion(question="Are empty names allowed?", default="no: ValueError")


class Draft:
    def __init__(
        self, tmp_path: Path, specifier: list[Step[BaseModel]], user: list[Step[BaseModel]] | None
    ) -> None:
        tmp_path.mkdir(parents=True, exist_ok=True)
        self.h = Harness(tmp_path)
        self.worker = FakeWorker(specifier)
        self.user = FakeWorker(user) if user is not None else None
        config = SpecifyConfig(
            goal="a greeter",
            model="spec",
            answerer="user" if user is not None else None,
            reference=Path("ref.txt") if user is not None else None,
        )
        self.specifier = Specifier(
            config,
            Harness.NORTH_STAR,
            self.h.board,
            self.h.repository,
            Harness.LADDER,
            self.worker,
            self.user,
            REFERENCE if user is not None else "",
        )

    def spec(self) -> str:
        level = self.h.repository.checkout_level(Harness.LADDER.base)
        try:
            return level.read("docs/spec.md")
        finally:
            level.remove()


class TestSpecifier:
    async def test_without_a_user_the_draft_and_its_defaults_are_committed_once(self, tmp_path: Path) -> None:
        d = Draft(tmp_path, [draft(FORM)], None)
        assert not d.specifier.done()
        assert await d.specifier.step() == 1
        assert d.specifier.done() and await d.specifier.step() == 0  # drafted once
        spec = d.spec()
        assert spec.startswith("# Greeter\n\n## Interface\n") and "Answer: not specified." in spec
        specified = next(e.event for e in d.h.log.read() if isinstance(e.event, Specified))
        assert (specified.sections, specified.questions, specified.answered) == (1, (FORM.question,), 0)

    async def test_answers_quoted_from_the_reference_revise_the_draft(self, tmp_path: Path) -> None:
        user = [
            reply("'Hello, <name>!'", "returns 'Hello, ' followed by the name and '!'"),
            reply("yes", "names must be nonempty"),  # a quote the reference doesn't hold: a guess
        ]
        d = Draft(tmp_path, [draft(FORM, EMPTY), revised], user)
        await d.specifier.step()
        asked = d.user.calls[0][1] if d.user is not None else None
        assert isinstance(asked, AnswerRequest) and "Names may be empty" in asked.reference
        revision = d.worker.calls[1][1]
        assert isinstance(revision, ReviseRequest)
        assert [a.answer for a in revision.answers] == ["'Hello, <name>!'", "not specified"]
        assert "hello('Ada') returns 'Hello, Ada!'" in d.spec()
        specified = next(e.event for e in d.h.log.read() if isinstance(e.event, Specified))
        assert specified.answered == 1


def brief(toolbox: Toolbox | None) -> BaseModel:
    return BriefDraft(
        title="Q3 for managers",
        audience="managers",
        purpose="decide the budget",
        style="plain",
        sections=(
            OutlinePart(title="Revenue", covers="the numbers", sources=("q3",), words=300),
            OutlinePart(title="Hiring plan", covers="who and when", sources=("plan",), words=200),
        ),
    )


class TestBriefer:
    async def test_the_brief_comes_first_then_a_pause_then_a_task_per_section(self, tmp_path: Path) -> None:
        h = Harness(tmp_path)
        level = h.repository.checkout_level(Harness.LADDER.base)
        level.write("sources/q3.md", "# Q3 Report\n\nRevenue rose 4%.\n")
        level.write("sources/plan.md", "# Plan\n\nHire two engineers.\n")
        level.commit("sources")
        level.remove()
        worker = FakeWorker([brief])
        briefer = Briefer(
            BriefConfig(goal="A summary for managers.", model="planner", words=500),
            h.board,
            h.repository,
            Harness.LADDER,
            ArtifactFactory(h.board, Harness.LADDER),
            worker,
        )
        assert await briefer.step() == 1 and not briefer.done()
        request = worker.calls[0][1]
        assert isinstance(request, BriefRequest) and [s.name for s in request.sources] == ["plan", "q3"]
        assert h.board.current().paused  # the user reads the brief first
        assert await briefer.step() == 0
        level = h.repository.checkout_level(Harness.LADDER.base)
        text = level.read("docs/brief.md")
        level.remove()
        assert "## Audience\n\nmanagers" in text and "At most 500 words in all." in text
        h.board.append([Resumed(reason="read it")])
        assert await briefer.step() == 1 and briefer.done()
        tasks = [a.task for a in h.board.current().artifacts()]
        assert tasks[0].startswith('Write sections/01-revenue.md, the section "Revenue"')
        assert "sources/plan.md" in tasks[1] and "At most 200 words" in tasks[1]
