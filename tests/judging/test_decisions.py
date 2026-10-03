from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path

import pytest

from agentswarm.core.config import JevMode
from agentswarm.core.domain import DecisionId
from agentswarm.judging.decisions import (
    Decision,
    DecisionDraft,
    DecisionStore,
    InMemoryDecisions,
    Outcome,
    Outcomes,
    OutcomeSource,
    SqliteDecisions,
)
from agentswarm.judging.judges import Judge, JudgeState, RawToExperimental
from agentswarm.judging.policy import Policy
from tests.support.projects import Projects
from tests.support.samples import Judged, Samples


class Drafts:
    JUDGE = Judge(RawToExperimental)

    @classmethod
    def make(cls, answers: RawToExperimental | None = None, diff: str = "+x") -> DecisionDraft:
        answers = answers or Judged.passing()
        return DecisionDraft(
            point="gate:raw->experimental",
            artifact=Samples.A,
            commit="c0ffee",
            mode=JevMode.SHADOW,
            judge=cls.JUDGE.name,
            state=JudgeState(north_star="n", task="t", must_fix=(), diff=diff),
            answers=answers.model_dump(mode="json"),
            ruling=Policy(cls.JUDGE).rule(answers),
            model="fake",
            at=Samples.AT,
        )

    @staticmethod
    def outcome(
        decision: Decision,
        field: str = "complete",
        truth: bool = True,
        source: OutcomeSource = OutcomeSource.HIDDEN_TESTS,
    ) -> Outcome:
        return Outcome(decision=decision.id, field=field, truth=truth, source=source, at=Samples.AT)


@pytest.fixture(params=["memory", "sqlite"])
def store(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[DecisionStore]:
    if request.param == "memory":
        yield InMemoryDecisions()
    else:
        with SqliteDecisions(tmp_path / "decisions.sqlite") as sqlite_store:
            yield sqlite_store


class TestDecisionStore:
    def test_records_decisions_with_sequential_ids(self, store: DecisionStore) -> None:
        first, second = store.record(Drafts.make()), store.record(Drafts.make(Judged.incomplete()))
        assert (first.id, second.id) == ("d-0001", "d-0002")
        assert store.all() == [first, second]
        assert second.ruling.verdict == "reject"

    def test_keeps_every_outcome_for_a_question(self, store: DecisionStore) -> None:
        decision = store.record(Drafts.make())
        tests = Drafts.outcome(decision, truth=False)
        human = Drafts.outcome(decision, truth=True, source=OutcomeSource.HUMAN)
        store.add_outcome(tests)
        store.add_outcome(human)
        assert store.outcomes(decision.id) == [tests, human]

    def test_outcomes_must_name_a_question_of_the_judge(self, store: DecisionStore) -> None:
        decision = store.record(Drafts.make())
        with pytest.raises(ValueError, match="no question 'completed'"):
            store.add_outcome(Drafts.outcome(decision, field="completed"))

    def test_outcomes_must_belong_to_a_stored_decision(self, store: DecisionStore) -> None:
        decision = store.record(Drafts.make())
        orphan = Drafts.outcome(decision).model_copy(update={"decision": DecisionId("d-9999")})
        with pytest.raises(KeyError, match="d-9999"):
            store.add_outcome(orphan)


class TestSqliteDecisions:
    def test_survives_reopening(self, tmp_path: Path) -> None:
        path = tmp_path / "decisions.sqlite"
        with SqliteDecisions(path) as first:
            decision = first.record(Drafts.make())
            first.add_outcome(Drafts.outcome(decision))
        with SqliteDecisions(path) as second:
            assert second.all() == [decision]
            assert len(second.outcomes(decision.id)) == 1
            assert second.record(Drafts.make()).id == "d-0002"


class TestOutcomes:
    def test_the_strongest_source_wins_then_the_latest(self) -> None:
        decision = Decision(id=DecisionId("d-0001"), **dict(Drafts.make()))
        mutation = Drafts.outcome(decision, field="tests_relevant", source=OutcomeSource.MUTATION)
        later = mutation.model_copy(update={"truth": False, "at": Samples.AT + timedelta(minutes=1)})
        planted = Drafts.outcome(decision, field="tests_relevant", source=OutcomeSource.PLANTED)
        assert Outcomes.strongest([mutation, later], "tests_relevant") == later
        assert Outcomes.strongest([planted, later], "tests_relevant") == planted
        assert Outcomes.strongest([planted], "complete") is None

    def test_source_strengths(self) -> None:
        order = sorted(OutcomeSource, key=lambda s: s.strength)
        assert order[-1] is OutcomeSource.HUMAN
        assert OutcomeSource.PLANTED.strength > OutcomeSource.HIDDEN_TESTS.strength


class TestDecisionDraft:
    def test_state_hash_identifies_the_input(self) -> None:
        assert Drafts.make().state_hash == Drafts.make().state_hash
        assert Drafts.make(diff="+a").state_hash != Drafts.make(diff="+b").state_hash

    def test_p_yes_per_question(self) -> None:
        draft = Drafts.make(Judged.incomplete())
        assert draft.p_yes("complete") == 0.05
        with pytest.raises(KeyError):
            draft.p_yes("nope")


class TestProjectDecisionLog:
    async def test_a_project_keeps_its_decisions_in_decisions_sqlite(self, tmp_path: Path) -> None:
        project = Projects.open(tmp_path, judged=[Judged.passing()], persistent_decisions=True)
        project.create_artifact("Write the greeting")
        await project.run_until_idle()
        project.close()
        with SqliteDecisions(tmp_path / "decisions.sqlite") as log:
            (decision,) = log.all()
        assert decision.ruling.verdict == "promote"
        assert "+# Write the greeting" in decision.state.diff
