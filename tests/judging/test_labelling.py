from collections.abc import Iterator

from agentswarm.core.clock import FakeClock
from agentswarm.judging.decisions import Decision, InMemoryDecisions, Outcome, OutcomeSource
from agentswarm.judging.judges import JudgeCatalog
from agentswarm.judging.labelling import SpotCheck
from tests.support.drafts import Drafts
from tests.support.samples import Samples


class Session:
    """A spot check driven by scripted answers, capturing everything it shows."""

    def __init__(self, answers: list[str]) -> None:
        self.store = InMemoryDecisions()
        self.shown: list[str] = []
        self._answers: Iterator[str] = iter(answers)

    def decision_with_outcome(self, field: str = "complete", truth: bool = True) -> Decision:
        decision = self.store.record(Drafts.make())
        self.store.add_outcome(
            Outcome(
                decision=decision.id,
                field=field,
                truth=truth,
                source=OutcomeSource.HIDDEN_TESTS,
                at=Samples.AT,
            )
        )
        return decision

    def check(self) -> SpotCheck:
        return SpotCheck(
            self.store,
            JudgeCatalog.standard(),
            lambda prompt: next(self._answers),
            self.shown.append,
            FakeClock(),
        )


class TestSpotCheck:
    def test_records_human_labels_and_reports_agreement(self) -> None:
        s = Session(["y", "y", "n"])  # in_scope, tests_relevant, complete
        decision = s.decision_with_outcome("complete", truth=True)
        summary = s.check().run(limit=5)
        humans = [o for o in s.store.outcomes(decision.id) if o.source is OutcomeSource.HUMAN]
        assert [(o.field, o.truth) for o in humans] == [
            ("in_scope", True),
            ("tests_relevant", True),
            ("complete", False),
        ]
        assert (summary.labelled, summary.compared) == (3, 1)
        (disagreement,) = summary.disagreements
        assert (disagreement.field, disagreement.human, disagreement.automatic) == ("complete", False, True)

    def test_jev_and_the_automatic_truth_are_shown_only_after_answering(self) -> None:
        s = Session(["s", "s", "y"])
        s.decision_with_outcome()
        shown_before_answers = len(s.shown)
        s.check().run(limit=1)
        decision_text = s.shown[shown_before_answers]
        assert "p(yes)" not in decision_text and "hidden_tests" not in decision_text
        assert any("Jev: p(yes) = 0.95; hidden_tests: yes" in line for line in s.shown)

    def test_skip_quit_and_invalid_answers(self) -> None:
        s = Session(["maybe", "s", "q"])
        s.decision_with_outcome()
        summary = s.check().run(limit=5)
        assert summary.labelled == 0
        assert any("please answer" in line for line in s.shown)

    def test_only_decisions_with_automatic_outcomes_and_no_human_labels(self) -> None:
        s = Session([])
        s.store.record(Drafts.make())  # no outcomes at all
        labelled = s.decision_with_outcome()
        s.store.add_outcome(
            Outcome(
                decision=labelled.id, field="in_scope", truth=True, source=OutcomeSource.HUMAN, at=Samples.AT
            )
        )
        assert s.check().candidates(limit=10) == []
