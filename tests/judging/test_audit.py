from agentswarm.core.config import JevMode
from agentswarm.judging.audit import GateAudit, Rate
from agentswarm.judging.decisions import InMemoryDecisions, Outcome, OutcomeSource
from agentswarm.judging.judges import ChangeReadiness
from tests.support.drafts import Drafts
from tests.support.samples import Judged, Samples


class Log:
    """Active gate decisions with chosen answers and truths."""

    def __init__(self) -> None:
        self.store = InMemoryDecisions()

    def add(self, answers: ChangeReadiness, truths: dict[str, bool], mode: JevMode = JevMode.ACTIVE) -> None:
        decision = self.store.record(Drafts.make(answers).model_copy(update={"mode": mode}))
        for field, truth in truths.items():
            self.store.add_outcome(
                Outcome(
                    decision=decision.id,
                    field=field,
                    truth=truth,
                    source=OutcomeSource.PLANTED,
                    at=Samples.AT,
                )
            )


ALL_YES = {"in_scope": True, "tests_relevant": True, "complete": True}


class TestGateAudit:
    def test_each_model_and_wording_is_audited_on_its_own(self) -> None:
        log = Log()
        log.add(Judged.passing(), ALL_YES)
        log.store.record(
            Drafts.make(Judged.passing()).model_copy(update={"mode": JevMode.ACTIVE, "model": "jev-2"})
        )
        audits = GateAudit.cohorts(log.store)
        assert [(a.model, a.decisions) for a in audits] == [(Drafts.make().model, 1), ("jev-2", 1)]
        assert GateAudit.of(log.store).decisions == 2  # pooled only when asked for explicitly
        assert "pooled" in GateAudit.of(log.store).render()

    def test_classifies_promotions_rejections_and_escalations(self) -> None:
        log = Log()
        log.add(Judged.passing(), ALL_YES)  # right promotion
        log.add(Judged.passing(), {"complete": False})  # false promotion: one known "no" is enough
        log.add(Judged.passing(), {"complete": True})  # promotion not settled: two questions unknown
        log.add(Judged.incomplete(), ALL_YES)  # false rejection
        log.add(Judged.incomplete(), {"complete": False})  # right rejection
        log.add(Judged.unsure(), ALL_YES)  # escalation
        log.add(Judged.incomplete(), ALL_YES, mode=JevMode.SHADOW)  # shadow decisions are not audited
        audit = GateAudit.of(log.store)
        assert audit.decisions == 6
        assert (audit.false_promotions.errors, audit.false_promotions.of) == (1, 2)
        assert (audit.false_rejections.errors, audit.false_rejections.of) == (1, 2)
        assert (audit.escalations.errors, audit.escalations.of) == (1, 6)
        assert audit.unknown == 1
        assert not audit.within

    def test_the_bound_is_compared_with_the_upper_confidence_limit(self) -> None:
        assert not Rate(name="r", errors=0, of=10, bound=0.05).within  # 0/10 could still be 28%
        assert Rate(name="r", errors=0, of=100, bound=0.05).within  # 0/100: at most about 4%
        assert not Rate(name="r", errors=0, of=0, bound=0.05).within  # no data proves nothing
