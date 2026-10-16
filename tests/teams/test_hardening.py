import sys
from pathlib import Path

from pydantic import BaseModel

from agentswarm.board.artifacts import ArtifactFactory
from agentswarm.core.config import CheckConfig, HardeningConfig
from agentswarm.core.domain import (
    AcceptanceWritten,
    ArtifactCreated,
    Attacked,
    AttackFocus,
    EscalationResolved,
    Event,
    Reconsidered,
    Resolution,
    Sized,
    TeamName,
    Unbuildable,
)
from agentswarm.gating.checks import CommandCheck, RatchetCheck
from agentswarm.llm.workers import FakeWorker, Step, Toolbox
from agentswarm.teams.hardening import (
    AttackProblem,
    AttackReport,
    AttackToolbox,
    Hardener,
    Leads,
    SpecSections,
)
from agentswarm.teams.referee import ReconsiderAnswer, Referee, ReportAnswer
from agentswarm.workspace.sandbox import LocalSandbox
from tests.support.harness import Harness

PYTEST = CheckConfig(
    command=(sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"), failure_exit_codes=(1,)
)
SPEC = (
    "# Greeting\n\n## Hello\n\nhello(name) returns 'Hello, <name>!'.\n\n"
    "## Goodbye\n\ngoodbye(name) returns 'Goodbye, <name>!' for any name.\n"
)
PRODUCT = "def hello(name):\n    return f'Hello, {name}!'\n\n\ndef goodbye(name):\n    return 'Bye'\n"
FAILING = (
    "from greeting import goodbye\n\n\ndef test_goodbye():\n    assert goodbye('Ada') == 'Goodbye, Ada!'\n"
)
PASSING = "from greeting import hello\n\n\ndef test_hello():\n    assert hello('Ada') == 'Hello, Ada!'\n"


def attack(test: str, quote: str) -> Step[BaseModel]:
    def step(toolbox: Toolbox | None) -> BaseModel:
        assert isinstance(toolbox, AttackToolbox)
        toolbox.run_test("test_red.py", test)
        problem = AttackProblem(
            title="goodbye", claim="goodbye ignores the name", test="test_red.py", quote=quote
        )
        return AttackReport(summary="attacked", problems=(problem,))

    return step


def ruling(confirmed: bool) -> Step[BaseModel]:
    def step(toolbox: Toolbox | None) -> BaseModel:
        return ReportAnswer(reason="the spec names the greeting", confirmed=confirmed)

    return step


def second_look(withdrawn: bool) -> Step[BaseModel]:
    def step(toolbox: Toolbox | None) -> BaseModel:
        return ReconsiderAnswer(reason="the existing test shows what the spec means", withdrawn=withdrawn)

    return step


EXISTING = "from greeting import goodbye\n\n\ndef test_short():\n    assert goodbye('Ada') == 'Bye'\n"
FIXED = PRODUCT.replace("return 'Bye'", "return f'Goodbye, {name}!'")


class Campaign:
    """A product on the base level with its spec, and a campaign over one section and one focus."""

    def __init__(
        self,
        tmp_path: Path,
        red_team: list[Step[BaseModel]],
        referee: list[Step[BaseModel]],
        existing: str | None = None,  # a protected test already in the product
    ) -> None:
        tmp_path.mkdir(parents=True, exist_ok=True)
        self.h = Harness(tmp_path)
        level = self.h.repository.checkout_level(Harness.LADDER.base)
        level.write("greeting.py", PRODUCT)
        level.write("docs/spec.md", SPEC)
        if existing is not None:
            level.write("tests/acceptance/test_existing.py", existing)
        level.commit("the product and its spec")
        level.remove()
        config = HardeningConfig(
            model="red", referee="ref", spec="docs/spec.md", sections=("Goodbye",), check="spec",
            focuses=(AttackFocus.SECTION,),
        )  # fmt: skip
        check = CommandCheck("spec", PYTEST, LocalSandbox())
        guarded = PYTEST.model_copy(update={"protected": ("tests/acceptance/",)})
        review = RatchetCheck("spec", guarded, Harness.LADDER.base, LocalSandbox())
        self.hardener = Hardener(
            config,
            Harness.NORTH_STAR,
            self.h.board,
            self.h.repository,
            Harness.LADDER,
            ArtifactFactory(self.h.board, Harness.LADDER),
            FakeWorker(red_team),
            Referee(FakeWorker(referee), "deepseek", ["docs/spec.md"]),
            check,
            review=review,
        )

    def events(self) -> list[Event]:
        return [e.event for e in self.h.log.read()]

    def builders_fail(self, task_id: str, product: str) -> None:
        """The last attempt's change on the task's branch, then every builder model failed the task."""
        task = next(a for a in self.h.board.current().artifacts() if a.id == task_id)
        branch = self.h.repository.checkout_artifact(task)
        branch.write("greeting.py", product)
        branch.commit("the last attempt")
        branch.remove()
        self.h.board.append([Unbuildable(artifact=task.id, team=TeamName("executor"), reason="checks fail")])


class TestSpecSections:
    def test_sections_open_at_their_heading_and_deeper_headings_stay_inside(self) -> None:
        sections = SpecSections.split("intro\n## A\none\n### A.1\ntwo\n## B\nthree\n", "## ")
        assert [(s.title, s.text) for s in sections] == [
            ("A", "## A\none\n### A.1\ntwo"),
            ("B", "## B\nthree"),
        ]

    def test_a_heading_inside_a_code_block_is_code(self) -> None:
        sections = SpecSections.split("# A\n```\n# When n>=0\nx\n```\n# B\n", "# ")
        assert [s.title for s in sections] == ["A", "B"]

    def test_a_long_section_is_cut_at_its_deeper_headings(self) -> None:
        text = (
            "## Builtins\nintro\n### a\n" + "x" * 50 + "\n### b\n" + "y" * 50 + "\n### c\n" + "z" * 50 + "\n"
        )
        sections = SpecSections.split(text, "## ", size=120)
        assert [s.title for s in sections] == ["Builtins (1/2)", "Builtins (2/2)"]
        assert sections[0].text.startswith("## Builtins") and "### a" in sections[0].text
        assert "### c" in sections[1].text


class TestRounds:
    @staticmethod
    def attacked(round: int, section: str, confirmed: int = 0) -> Attacked:
        return Attacked(
            round=round, section=section, focus=AttackFocus.SECTION, commit="c", reported=confirmed,
            confirmed=confirmed,
        )  # fmt: skip

    def test_a_round_covers_every_pair_before_the_next_begins(self) -> None:
        pairs = [("A", AttackFocus.SECTION), ("B", AttackFocus.SECTION)]
        assert Hardener.next_attack([], pairs) == (1, pairs[0])
        assert Hardener.next_attack([self.attacked(1, "A")], pairs) == (1, pairs[1])
        assert Hardener.next_attack([self.attacked(1, "A"), self.attacked(1, "B")], pairs) == (2, pairs[0])

    def test_done_only_after_two_whole_quiet_rounds(self, tmp_path: Path) -> None:
        hardener = Campaign(tmp_path, [], []).hardener
        one = [self.attacked(1, "A", confirmed=1), self.attacked(1, "B")]
        two = [self.attacked(2, "A"), self.attacked(2, "B")]
        three = [self.attacked(3, "A"), self.attacked(3, "B")]
        assert not hardener.done(one + two, 2)  # one quiet round is weak evidence
        assert hardener.done(one + two + three, 2)
        assert not hardener.done(one + two + three[:1], 2)  # the third round isn't whole yet


class TestFocuses:
    def test_every_focus_tells_the_red_team_what_to_attack(self) -> None:
        assert set(Hardener.FOCUS) == set(AttackFocus)


class TestHardener:
    async def test_a_confirmed_report_becomes_a_fix_task_with_the_failing_test(self, tmp_path: Path) -> None:
        c = Campaign(tmp_path, [attack(FAILING, "returns 'Goodbye, <name>!'")], [ruling(True), ruling(True)])
        assert await c.hardener.step() == 1
        events = c.events()
        attacked = [e for e in events if isinstance(e, Attacked)]
        assert [(a.reported, a.confirmed, len(a.tasks)) for a in attacked] == [(1, 1, 1)]
        written = next(e for e in events if isinstance(e, AcceptanceWritten))
        task = next(a for a in c.h.board.current().artifacts() if a.id == attacked[0].tasks[0])
        assert task.level == Harness.LADDER.lowest and "Goodbye, <name>!" in task.task
        branch = c.h.repository.checkout_artifact(task)
        assert branch.read(written.files[0].path) == FAILING  # its acceptance test, protected from now
        assert any(isinstance(e, Sized) and e.fits and e.artifact == task.id for e in events)  # try it first
        assert await c.hardener.step() == 0  # the builders have work: no attack until it's done

    async def test_a_split_task_waits_on_its_steps_not_on_itself(self, tmp_path: Path) -> None:
        c = Campaign(tmp_path, [], [])
        factory = ArtifactFactory(c.h.board, Harness.LADDER)
        parent = factory.create("the whole parser", TeamName("human"))
        child = factory.child(parent, 1, "one step", TeamName("planner"))
        c.h.board.append([ArtifactCreated(artifact=child)])
        assert c.hardener.building(c.h.board.current())
        c.h.board.append([EscalationResolved(artifact=child.id, resolution=Resolution.DROP)])
        assert not c.hardener.building(c.h.board.current())  # the parent is open, but has no work of its own

    async def test_reports_without_a_failing_test_a_real_quote_or_a_ruling_are_dropped(
        self, tmp_path: Path
    ) -> None:
        passing = Campaign(tmp_path / "p", [attack(PASSING, "returns 'Goodbye, <name>!'")], [])
        await passing.hardener.step()
        quote = Campaign(tmp_path / "q", [attack(FAILING, "goodbye returns 'Bye'")], [])
        await quote.hardener.step()
        refused = Campaign(
            tmp_path / "r", [attack(FAILING, "returns 'Goodbye, <name>!'")], [ruling(False)] * 2
        )
        await refused.hardener.step()
        reasons = [
            next(e for e in c.events() if isinstance(e, Attacked)).dropped[0]
            for c in (passing, quote, refused)
        ]
        assert "passed, or never ran" in reasons[0]
        assert "not in the spec" in reasons[1]
        assert "not confirmed" in reasons[2]
        assert not [a for c in (passing, quote, refused) for a in c.h.board.current().artifacts()]


class TestLeads:
    def test_planted_bugs_no_check_notices_become_leads(self, tmp_path: Path) -> None:
        c = Campaign(tmp_path, [], [])
        level = c.h.repository.checkout_level(Harness.LADDER.base)
        level.write("tests/test_hello.py", PASSING)  # hello is tested, goodbye isn't
        level.commit("a test for hello")
        leads = Leads(CommandCheck("spec", PYTEST, LocalSandbox()), count=40).find(level, "seed")
        level.remove()
        assert leads and all(lead.startswith("greeting.py:") for lead in leads)
        assert any(":6:" in lead for lead in leads)  # goodbye's line: nothing notices a change there


class TestReconsider:
    async def test_a_fix_that_breaks_a_protected_test_goes_back_to_the_referee(self, tmp_path: Path) -> None:
        referee = [ruling(True), ruling(True), second_look(True), second_look(True)]
        c = Campaign(tmp_path, [attack(FAILING, "returns 'Goodbye, <name>!'")], referee, existing=EXISTING)
        await c.hardener.step()
        task = next(e for e in c.events() if isinstance(e, Attacked)).tasks[0]
        c.builders_fail(task, FIXED)
        assert await c.hardener.step() == 1
        reconsidered = next(e for e in c.events() if isinstance(e, Reconsidered))
        assert reconsidered.withdrawn and reconsidered.referee == "deepseek"
        assert reconsidered.broken == ("tests/acceptance/test_existing.py::test_short",)
        assert c.h.board.current().dropped(task)  # the planner never splits it
        assert "withdrawn" in c.hardener.withdrawn(c.h.board.current())[0]  # later red teams are told

    async def test_without_a_broken_protected_test_the_report_stands_with_no_model_call(
        self, tmp_path: Path
    ) -> None:
        c = Campaign(tmp_path, [attack(FAILING, "returns 'Goodbye, <name>!'")], [ruling(True), ruling(True)])
        await c.hardener.step()
        task = next(e for e in c.events() if isinstance(e, Attacked)).tasks[0]
        c.builders_fail(task, FIXED)
        assert await c.hardener.step() == 1
        reconsidered = next(e for e in c.events() if isinstance(e, Reconsidered))
        assert not reconsidered.withdrawn and reconsidered.referee is None and not reconsidered.broken
        assert not c.h.board.current().dropped(task)
        assert not c.hardener.unreviewed(c.h.board.current())  # looked at once
