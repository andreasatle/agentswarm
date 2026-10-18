from pathlib import Path

from pydantic import BaseModel

from agentswarm.board.artifacts import ArtifactFactory
from agentswarm.core.config import ReviewConfig
from agentswarm.core.domain import Attacked, Escalated, Event, Perspective, Scored, Sized, TeamName
from agentswarm.llm.workers import FakeWorker, Step, Toolbox
from agentswarm.teams.referee import FindingAnswer, FindingRequest, Referee
from agentswarm.teams.review import Evidence, ReviewCampaign, ReviewFinding, ReviewFindings, ReviewRequest
from tests.support.harness import Harness

SECTION = "## Tagging\n\nThe tagger reaches 99% accuracy [@p1].\nIt was trained on one million words [@p1].\n"
SOURCE = "# Tagging with Little Data\n\n## Results\n\nThe tagger reaches 97.1% accuracy on the test set.\n"
BRIEF = "# The brief\n\nA guide for a newcomer, at most 3000 words.\n"
WRONG = Evidence(file="sections/01-tagging.md", quote="The tagger reaches 99% accuracy")
SAYS = Evidence(file="sources/p1.md", quote="reaches 97.1% accuracy on the test set")


def review(*evidence: Evidence, title: str = "wrong accuracy") -> Step[BaseModel]:
    def step(toolbox: Toolbox | None) -> BaseModel:
        finding = ReviewFinding(
            title=title, problem="the paper reports 97.1%, not 99%", fix="say 97.1%", evidence=evidence
        )
        return ReviewFindings(summary="reviewed", findings=(finding,))

    return step


def ruling(confirmed: bool) -> Step[BaseModel]:
    def step(toolbox: Toolbox | None) -> BaseModel:
        return FindingAnswer(reason="the source says 97.1%", shown=confirmed, matters=True, fits=True)

    return step


class Review:
    """A finished guide on the base level, and a review campaign over its one section."""

    def __init__(
        self,
        tmp_path: Path,
        reviewer: list[Step[BaseModel]],
        referee: list[Step[BaseModel]],
        perspectives: tuple[Perspective, ...] = (Perspective.FACTS,),
    ) -> None:
        tmp_path.mkdir(parents=True, exist_ok=True)
        self.h = Harness(tmp_path)
        level = self.h.repository.checkout_level(Harness.LADDER.base)
        level.write("sections/01-tagging.md", SECTION)
        level.write("sections/.gitkeep", "")
        level.write("sources/p1.md", SOURCE)
        level.write("docs/brief.md", BRIEF)
        level.commit("a guide")
        level.remove()
        self.reviewer = FakeWorker(reviewer)
        self.referee = FakeWorker(referee)
        config = ReviewConfig(model="rev", referee="ref", perspectives=perspectives)
        self.campaign = ReviewCampaign(
            config,
            Harness.NORTH_STAR,
            self.h.board,
            self.h.repository,
            Harness.LADDER,
            ArtifactFactory(self.h.board, Harness.LADDER),
            self.reviewer,
            Referee(self.referee, "deepseek", ()),
        )

    def events(self) -> list[Event]:
        return [e.event for e in self.h.log.read()]

    def attacked(self) -> Attacked:
        return next(e for e in self.events() if isinstance(e, Attacked))


class TestFindingAnswer:
    def test_confirmed_only_when_shown_matters_and_fits(self) -> None:
        def answer(shown: bool, matters: bool, fits: bool) -> bool:
            return FindingAnswer(reason="r", shown=shown, matters=matters, fits=fits).confirmed

        assert answer(True, True, True)
        assert (
            not answer(False, True, True) and not answer(True, False, True) and not answer(True, True, False)
        )


class TestReviewCampaign:
    async def test_a_confirmed_finding_becomes_a_revision_the_writer_tries_whole(
        self, tmp_path: Path
    ) -> None:
        r = Review(tmp_path, [review(WRONG, SAYS)], [ruling(True), ruling(True)])
        assert await r.campaign.step() == 1
        attacked = r.attacked()
        assert (attacked.section, attacked.focus, attacked.confirmed) == (
            "sections/01-tagging.md",
            "facts",
            1,
        )
        task = next(a for a in r.h.board.current().artifacts() if a.id == attacked.tasks[0])
        assert task.level == Harness.LADDER.lowest
        assert "Revise sections/01-tagging.md" in task.task and "97.1%" in task.task
        assert any(isinstance(e, Sized) and e.fits and e.artifact == task.id for e in r.events())
        ruled = r.referee.calls[0][1]
        assert isinstance(ruled, FindingRequest)
        assert [e.file for e in ruled.evidence] == ["sections/01-tagging.md", "sources/p1.md"]
        assert "97.1% accuracy" in ruled.evidence[1].context
        assert "<sections/01-tagging.md>" in ruled.document  # the whole guide: is it missing everywhere?

    async def test_evidence_that_isnt_there_or_doesnt_fit_the_perspective_is_dropped_unruled(
        self, tmp_path: Path
    ) -> None:
        made_up = Evidence(file="sources/p1.md", quote="reaches 99% accuracy on the test set")
        invented = Review(tmp_path / "i", [review(WRONG, made_up)], [])
        await invented.campaign.step()
        coverage = Review(tmp_path / "c", [review(WRONG)], [], perspectives=(Perspective.COVERAGE,))
        await coverage.campaign.step()  # a coverage finding must quote the source with the missing content
        assert "not in the file it names" in invented.attacked().dropped[0]
        assert "1 of the sources" in coverage.attacked().dropped[0]
        assert not invented.referee.calls and not coverage.referee.calls

    async def test_a_rejected_finding_is_shown_to_the_next_review(self, tmp_path: Path) -> None:
        r = Review(tmp_path, [review(WRONG, SAYS)] * 2, [ruling(False), ruling(False)])
        await r.campaign.step()  # round 1: rejected
        await r.campaign.step()  # round 2: the same section, told what was rejected and why
        request = r.reviewer.calls[1][1]
        assert isinstance(request, ReviewRequest)
        assert request.rejected == (
            "wrong accuracy: the paper reports 97.1%, not 99% (rejected: the source says 97.1%)",
        )

    async def test_every_section_file_is_a_part_in_reading_order(self, tmp_path: Path) -> None:
        r = Review(tmp_path, [], [])
        level = r.h.repository.checkout_level(Harness.LADDER.base)
        level.write("sections/02-parsing.md", "## Parsing\n")
        level.commit("a second section")
        parts = [p.title for p in r.campaign.parts(level)]
        level.remove()
        assert parts == ["sections/01-tagging.md", "sections/02-parsing.md"]  # .gitkeep is no section


class TestFailures:
    async def test_refused_calls_stop_the_run_with_a_notice_and_never_count_as_quiet(
        self, tmp_path: Path
    ) -> None:
        r = Review(tmp_path, [], [])  # every review call fails: no credits, say
        assert [await r.campaign.step() for _ in range(5)] == [1, 1, 1, 0, 0]
        views = r.h.board.current()
        assert all(a.failed for a in views.attacks()) and not r.campaign.mine(views)
        assert not r.campaign.done(r.campaign.mine(views), 1)  # no quiet round was had
        notices = [e for e in views.project_escalations() if "failed 3 times in a row" in e.reason]
        assert len(notices) == 1
        r.h.board.append(
            [Scored(level=Harness.LADDER.base, commit="c", seen=None, held_out=None, spent_usd=0.0)]
        )
        assert await r.campaign.step() == 1  # a new run (it starts with a score) tries again


class TestWaiting:
    async def test_no_review_while_a_section_is_unwritten_even_one_waiting_for_a_person(
        self, tmp_path: Path
    ) -> None:
        r = Review(tmp_path, [review(WRONG, SAYS)], [ruling(True), ruling(True)])
        task = ArtifactFactory(r.h.board, Harness.LADDER).create("Write sections/02.md", TeamName("briefer"))
        r.h.board.append([Escalated(artifact=task.id, reason="every model failed")])
        assert await r.campaign.step() == 0 and not r.reviewer.calls


class TestMaterial:
    async def test_the_reviewer_gets_the_cited_sources_in_one_call(self, tmp_path: Path) -> None:
        r = Review(tmp_path, [review(WRONG, SAYS)], [ruling(True), ruling(True)])
        await r.campaign.step()
        request = r.reviewer.calls[0][1]
        assert isinstance(request, ReviewRequest)
        assert [m.name for m in request.material] == ["p1"] and "97.1% accuracy" in request.material[0].text
        assert request.document == ""  # the whole document only for the consistency perspective
