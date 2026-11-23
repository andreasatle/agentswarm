from pathlib import Path

from agentswarm.board.artifacts import ArtifactFactory
from agentswarm.core.domain import OutlineEntry, SectionAssigned, Sized, TeamName
from agentswarm.evaluation.redteam import Finding, FlawKind
from agentswarm.teams.revisions import Placed, UserRevisions
from tests.support.harness import Harness

SECTIONS = {
    "sections/01-why.md": "## 1. Why batching helps\n\nThe weights are read once for every request.\n",
    "sections/02-experts.md": "## 2. Experts under load\n\nEach expert receives **Bk/E** tokens.\n",
}


def finding(evidence: str) -> Finding:
    return Finding(where="x", evidence=evidence, flaw="Wrong.", fix="Fix it.", kind=FlawKind.REVISE)


class TestUserRevisions:
    def test_an_instruction_goes_where_it_quotes_or_names_and_otherwise_everywhere(self) -> None:
        placement = UserRevisions.place(
            (
                'Say why "read once for every request" matters',  # a quote: that section
                "In Experts under load, add an example",  # a section's title
                "No 'first', 'second' in bullets",  # neither: the whole document
            ),
            (finding("Each expert receives Bk/E tokens"), finding("[^1]: plato.stanford.edu (n.d.).")),
            SECTIONS,
        )
        why, experts = placement.sections
        assert why.instructions == (
            'Say why "read once for every request" matters',
            "No 'first', 'second' in bullets",
        )
        assert experts.instructions == (
            "In Experts under load, add an example",
            "No 'first', 'second' in bullets",
        )
        assert experts.findings == (finding("Each expert receives Bk/E tokens"),) and why.findings == ()
        assert placement.unplaced == (finding("[^1]: plato.stanford.edu (n.d.)."),)  # the references

    def test_a_section_nothing_asks_about_is_left_alone(self) -> None:
        placement = UserRevisions.place(('Cut "read once for every request"',), (), SECTIONS)
        assert [p.section for p in placement.sections] == ["sections/01-why.md"]

    def test_a_quote_the_document_doesnt_hold_is_named(self) -> None:
        document = "\n".join(SECTIONS.values())
        assert UserRevisions.missing('Cut "the experts sleep most of the day"', document) == (
            "the experts sleep most of the day"
        )
        assert UserRevisions.missing("Cut “read once for every request”", document) is None
        assert UserRevisions.missing('Say "why" more often', document) is None  # too short to be a passage

    def test_each_section_gets_one_revision_its_writer_makes_as_it_wrote_it(self, tmp_path: Path) -> None:
        h = Harness(tmp_path)
        artifacts = ArtifactFactory(h.board, Harness.LADDER)
        written = artifacts.create("write sections/02-experts.md", TeamName("writer"))
        entry = OutlineEntry(title="Experts under load", covers="load", sources=("a",), words=300)
        h.board.append([SectionAssigned(artifact=written.id, path="sections/02-experts.md", section=entry)])
        revisions = UserRevisions(h.board, artifacts.create)
        placed = Placed(
            section="sections/02-experts.md",
            instructions=("No 'first', 'second' in bullets",),
            findings=(finding("Each expert receives Bk/E tokens"),),
        )
        (task,) = revisions.create(UserRevisions.place((), (), {}).model_copy(update={"sections": (placed,)}))
        views = h.board.current()
        history = views.history(task)
        assert any(isinstance(e, SectionAssigned) and e.section == entry for e in history)
        assert any(isinstance(e, Sized) and e.fits for e in history)
        text = views.artifact(task).task
        assert "(1) (the user)" in text and "(2) (a red-team finding the user agreed with) Wrong." in text
        assert "The user's word settles" in text
