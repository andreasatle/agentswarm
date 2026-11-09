from pathlib import Path

from pydantic import BaseModel

from agentswarm.evaluation.citations import CitationAnswer, Support
from agentswarm.evaluation.review_comparison import Arm, ArmResult, ReviewComparison
from agentswarm.llm.workers import FakeWorker, Step, Toolbox
from agentswarm.teams.claims import ClaimChecker, ClaimVerdict, WholeParts
from agentswarm.teams.review import Evidence, ReviewFinding, ReviewFindings

SECTION = (
    "## Tagging\n\nThe tagger reaches 99% accuracy [@p1].\nIt was trained on one million words [@p1].\n"
    "It is the best tagger ever made.\n"
)
SOURCE = "# Tagging\n\nThe tagger reaches 97.1% accuracy. It was trained on one million words.\n"
WRONG = "The tagger reaches 99% accuracy [@p1]."
RIGHT = "It was trained on one million words [@p1]."


def answer(model: BaseModel) -> Step[BaseModel]:
    def step(toolbox: Toolbox | None) -> BaseModel:
        return model

    return step


def level(tmp_path: Path) -> Path:
    (tmp_path / "sections").mkdir()
    (tmp_path / "sources").mkdir()
    (tmp_path / "sections" / "01-tagging.md").write_text(SECTION)
    (tmp_path / "sources" / "p1.md").write_text(SOURCE)
    return tmp_path


class TestReviewComparison:
    async def test_each_arm_is_scored_against_the_reference_audit(self, tmp_path: Path) -> None:
        comparison = ReviewComparison(level(tmp_path), "a guide")
        statements = comparison.statements()
        assert [s.text for s in statements] == [WRONG, RIGHT]
        reference = await comparison.reference(
            FakeWorker(
                [
                    answer(CitationAnswer(reason="says 97.1%", support=Support.UNSUPPORTED)),
                    answer(CitationAnswer(reason="says so", support=Support.SUPPORTED)),
                ]
            ),
            statements,
        )
        findings = ReviewFindings(
            summary="reviewed",
            findings=(
                ReviewFinding(
                    title="wrong accuracy",
                    problem="97.1%",
                    fix="fix",
                    evidence=(
                        Evidence(file="sections/01-tagging.md", quote="The tagger reaches 99% accuracy"),
                    ),
                ),
                ReviewFinding(
                    title="uncited",
                    problem="no source",
                    fix="cite",
                    evidence=(
                        Evidence(file="sections/01-tagging.md", quote="It is the best tagger ever made."),
                    ),
                ),
            ),
        )
        reviewer = await comparison.facts_reviewer(FakeWorker([answer(findings)]), statements)
        assert (reviewer.flagged, reviewer.unmatched) == ((WRONG,), 1)
        checker = ClaimChecker(
            FakeWorker(
                [
                    answer(ClaimVerdict(reason="r", support=Support.SUPPORTED)),  # misses the wrong one
                    answer(ClaimVerdict(reason="r", support=Support.PARTLY)),  # and flags the right one
                ]
            ),
            WholeParts(),
        )
        claims = await comparison.claims(Arm.CLAIMS_WHOLE, checker, statements)
        assert claims.flagged == (RIGHT,)
        table = ReviewComparison.render(reference, [reviewer, claims.model_copy(update={"usd": 0.01})])
        assert "reference: 2 cited statements, 1 not fully supported (1 unsupported), 1 supported" in table
        assert "| facts reviewer | 1 of 1 | 0 | 0 | 1 | 0 | $0.000 |" in table
        assert "| claims, whole part | 0 of 1 | 1 | 1 | 0 | 0 | $0.010 |" in table
        assert isinstance(claims, ArmResult)
