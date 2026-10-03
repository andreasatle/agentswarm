import pytest

from agentswarm.core.config import Threshold
from agentswarm.judging.calibration import CalibrationReport, Labelled, QuestionCalibration
from agentswarm.judging.decisions import InMemoryDecisions, Outcome, OutcomeSource
from tests.support.drafts import Drafts
from tests.support.samples import Judged, Samples


class Rows:
    @staticmethod
    def of(*pairs: tuple[float, bool], field: str = "complete") -> list[Labelled]:
        return [
            Labelled(
                point="gate:raw->experimental",
                judge_version="v1",
                field=field,
                p_yes=p,
                truth=t,
                source=OutcomeSource.HIDDEN_TESTS,
            )
            for p, t in pairs
        ]


DEFAULT = Threshold(pass_at=0.9, fail_at=0.2)


class TestQuestionCalibration:
    def test_a_perfectly_calibrated_judge_has_zero_ece(self) -> None:
        rows = Rows.of((0.95, True), (0.95, True), (0.05, False), (0.05, False))
        report = QuestionCalibration.of(rows, DEFAULT)
        # Bins: 0.0-0.1 holds p=0.05 (never yes), 0.9-1.0 holds p=0.95 (always yes): off by 0.05 each.
        assert report.ece == pytest.approx(0.05)
        assert [(b.low, b.n, b.fraction_yes) for b in report.bins] == [(0.0, 2, 0.0), (0.9, 2, 1.0)]
        assert report.brier == pytest.approx(0.05**2)

    def test_an_overconfident_judge_has_a_large_ece(self) -> None:
        rows = Rows.of((0.99, True), (0.99, False), (0.99, False), (0.99, False))
        report = QuestionCalibration.of(rows, DEFAULT)
        assert report.ece == pytest.approx(0.99 - 0.25)

    def test_coverage_and_accuracy_at_the_thresholds(self) -> None:
        rows = Rows.of((0.95, True), (0.95, False), (0.10, False), (0.50, True))
        report = QuestionCalibration.of(rows, DEFAULT)
        assert report.coverage == pytest.approx(3 / 4)  # 0.50 is unsure
        assert report.decided_accuracy == pytest.approx(2 / 3)  # the second 0.95 was wrong

    def test_p_of_one_falls_in_the_top_bin(self) -> None:
        assert QuestionCalibration.bin_of(1.0) == 9
        assert QuestionCalibration.bin_of(0.0) == 0

    def test_render_warns_about_small_and_one_sided_samples(self) -> None:
        text = QuestionCalibration.of(Rows.of((0.9, True), (0.8, True)), DEFAULT).render()
        assert "every truth is the same" in text
        assert "only 2 answers" in text


class TestCalibrationReport:
    def test_uses_the_strongest_outcome_per_question_and_counts_the_unlabelled(self) -> None:
        store = InMemoryDecisions()
        decision = store.record(Drafts.make(Judged.incomplete()))
        for source, truth in ((OutcomeSource.MUTATION, True), (OutcomeSource.PLANTED, False)):
            store.add_outcome(
                Outcome(
                    decision=decision.id, field="tests_relevant", truth=truth, source=source, at=Samples.AT
                )
            )
        store.add_outcome(
            Outcome(
                decision=decision.id,
                field="complete",
                truth=False,
                source=OutcomeSource.HIDDEN_TESTS,
                at=Samples.AT,
            )
        )
        report = CalibrationReport.of(store)
        assert [(q.field, q.n) for q in report.questions] == [("complete", 1), ("tests_relevant", 1)]
        relevant = report.questions[1]
        assert relevant.sources == {OutcomeSource.PLANTED: 1}
        assert relevant.bins[0].fraction_yes == 0.0
        assert report.unlabelled == 1  # in_scope has no outcome

    def test_llm_fallback_decisions_are_left_out(self) -> None:
        store = InMemoryDecisions()
        decision = store.record(Drafts.make(Judged.passing()).model_copy(update={"model": "llm:ollama:m"}))
        store.add_outcome(
            Outcome(
                decision=decision.id,
                field="complete",
                truth=True,
                source=OutcomeSource.HIDDEN_TESTS,
                at=Samples.AT,
            )
        )
        assert CalibrationReport.of(store).questions == ()

    def test_judge_versions_are_kept_apart(self) -> None:
        store = InMemoryDecisions()
        for version in ("old", "new"):
            decision = store.record(Drafts.make().model_copy(update={"judge_version": version}))
            store.add_outcome(
                Outcome(
                    decision=decision.id,
                    field="complete",
                    truth=True,
                    source=OutcomeSource.HIDDEN_TESTS,
                    at=Samples.AT,
                )
            )
        report = CalibrationReport.of(store)
        assert sorted(q.judge_version for q in report.questions) == ["new", "old"]

    def test_an_empty_log_says_so(self) -> None:
        assert CalibrationReport.of(InMemoryDecisions()).render().startswith("No labelled answers yet")
