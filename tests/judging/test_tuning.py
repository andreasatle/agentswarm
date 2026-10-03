import pytest

from agentswarm.judging.calibration import Labelled
from agentswarm.judging.decisions import InMemoryDecisions, Outcome, OutcomeSource
from agentswarm.judging.judges import ChangeReadiness, Judge, JudgeCatalog
from agentswarm.judging.tuning import ThresholdTuner, TuningReport, Wilson
from tests.support.drafts import Drafts
from tests.support.samples import Judged, Samples


def rows(*pairs: tuple[float, bool]) -> list[Labelled]:
    return [
        Labelled(
            point="gate:x",
            judge_version="v",
            field="complete",
            p_yes=p,
            truth=t,
            source=OutcomeSource.PLANTED,
        )
        for p, t in pairs
    ]


class TestThresholdTuner:
    def test_picks_the_lowest_pass_and_highest_fail_threshold_meeting_the_precision(self) -> None:
        data = rows(*[(0.95, True)] * 10, (0.8, True), (0.8, False), *[(0.1, False)] * 10, (0.3, True))
        tuner = ThresholdTuner(precision=0.95, min_decided=5)
        passing, failing = tuner.passing(data), tuner.failing(data)
        # At 0.3 and 0.8, 12/13 and 11/12 are yes (92%): too low. At 0.95, 10/10.
        assert passing is not None and passing.threshold == 0.95
        # At 0.3, 10/11 are no (91%): too low. At 0.1, 10/10.
        assert failing is not None and failing.threshold == 0.1
        assert passing.coverage == pytest.approx(10 / 23)

    def test_thresholds_snap_to_the_nearest_answer_of_their_kind(self) -> None:
        # One "yes" at 0.62 sits among the "no"s: a greedy fail threshold of 0.62 would reject it for nothing.
        data = rows(*[(0.2, False)] * 20, (0.62, True), *[(0.9, True)] * 20)
        tuner = ThresholdTuner(precision=0.95, min_decided=5)
        failing, passing = tuner.failing(data), tuner.passing(data)
        assert failing is not None and failing.threshold == 0.2 and failing.precision == 1.0
        assert passing is not None and passing.threshold == 0.62  # snapped to the lowest yes it passes

    def test_no_threshold_when_the_target_is_out_of_reach(self) -> None:
        data = rows(*[(0.9, True), (0.9, False)] * 5)
        assert ThresholdTuner(precision=0.95).passing(data) is None

    def test_the_pass_threshold_stays_above_the_fail_threshold(self) -> None:
        data = rows(*[(0.4, False)] * 6, *[(0.9, True)] * 6)
        tuner = ThresholdTuner(precision=0.9, min_decided=5)
        failing = tuner.failing(data)
        assert failing is not None and failing.threshold == 0.4
        passing = tuner.passing(data, above=failing.threshold)
        assert passing is not None and passing.threshold == 0.9

    def test_old_wording_is_never_ready_and_current_wording_needs_support(self) -> None:
        store = InMemoryDecisions()
        current = Judge(ChangeReadiness).version
        for version in ("old", current):
            for answers, truth in [(Judged.passing(), True)] * 12 + [
                (Judged.answers(complete=0.05), False)
            ] * 12:
                decision = store.record(Drafts.make(answers).model_copy(update={"judge_version": version}))
                store.add_outcome(
                    Outcome(
                        decision=decision.id,
                        field="complete",
                        truth=truth,
                        source=OutcomeSource.PLANTED,
                        at=Samples.AT,
                    )
                )
        tunings = ThresholdTuner(min_support=20).tune(store, JudgeCatalog.standard())
        ready = {(t.judge_version == current, t.field): t.ready for t in tunings}
        assert ready == {(False, "complete"): False, (True, "complete"): True}
        assert "complete: {pass: 0.95, fail: 0.05}" in TuningReport.of(tunings).thresholds_yaml()


class TestWilson:
    def test_lower_bound_is_honest_about_small_samples(self) -> None:
        assert Wilson.lower(10, 10) == pytest.approx(0.722, abs=0.001)
        assert Wilson.lower(100, 100) > 0.96
        assert Wilson.lower(0, 0) == 0.0
        assert Wilson.upper(0, 10) == pytest.approx(0.278, abs=0.001)
