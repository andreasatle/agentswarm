from copy import deepcopy
from datetime import timedelta

from agentswarm.board.blackboard import Blackboard
from agentswarm.board.eventlog import InMemoryEventLog
from agentswarm.core.clock import FakeClock
from agentswarm.core.domain import (
    Artifact,
    ArtifactCreated,
    ArtifactId,
    Claim,
    Claimed,
    DecisionId,
    Escalated,
    Finding,
    FindingAdded,
    FindingResolved,
    Promoted,
    Rejected,
    Released,
    SignedOff,
    Sized,
)
from tests.support.samples import Samples


class Board:
    """A fresh blackboard over an in-memory log, with its clock."""

    def __init__(self) -> None:
        self.clock = FakeClock(Samples.AT)
        self.log = InMemoryEventLog(Samples.PROJECT, self.clock)
        self.board = Blackboard(self.log)

    def claim(self, minutes: int = 10) -> Claimed:
        return Claimed(
            claim=Claim(
                artifact=Samples.A,
                team=Samples.EXECUTOR,
                expires_at=self.clock.now() + timedelta(minutes=minutes),
            )
        )

    @staticmethod
    def promoted() -> Promoted:
        return Promoted(
            artifact=Samples.A,
            from_level=Samples.RAW,
            to_level=Samples.EXPERIMENTAL,
            gate_decision=DecisionId("d-1"),
        )


class TestBlackboardViews:
    def test_artifacts_are_listed_at_their_level(self) -> None:
        b = Board()
        views = b.board.append([Samples.created(), Samples.created(Samples.B)])
        assert [a.id for a in views.artifacts_at(Samples.RAW)] == [Samples.A, Samples.B]
        assert views.artifacts_at(Samples.EXPERIMENTAL) == []

    def test_promotion_moves_the_artifact_and_closes_findings(self) -> None:
        b = Board()
        b.board.append([Samples.created(), FindingAdded(finding=Samples.finding())])
        views = b.board.append([Samples.commit(), b.promoted()])
        assert views.artifact(Samples.A).level == Samples.EXPERIMENTAL
        assert views.open_findings(Samples.A) == []
        assert views.unjudged_commit(Samples.A) is None

    def test_the_first_promotion_records_the_merge_commit(self) -> None:
        b = Board()
        b.board.append([Samples.created(), Samples.commit()])
        views = b.board.append([b.promoted().model_copy(update={"commit": "m1"})])
        assert views.merge_commit(Samples.A) == "m1"

    def test_an_umbrella_has_no_merge_commit(self) -> None:
        b = Board()
        b.board.append([Samples.created(), Sized(artifact=Samples.A, fits=False)])
        views = b.board.append([b.promoted().model_copy(update={"commit": "level-tip"})])
        assert views.merge_commit(Samples.A) is None

    def test_rejection_replaces_open_findings(self) -> None:
        b = Board()
        old = Samples.finding().model_copy(update={"detail": "old"})
        new = Samples.finding().model_copy(update={"detail": "new"})
        b.board.append([Samples.created(), FindingAdded(finding=old), Samples.commit()])
        views = b.board.append(
            [Rejected(artifact=Samples.A, level=Samples.RAW, findings=(new,), gate_decision=None)]
        )
        assert views.open_findings(Samples.A) == [new]

    def test_resolved_findings_are_not_open(self) -> None:
        b = Board()
        kept, fixed = Samples.finding(finding="f-kept"), Samples.finding(finding="f-fixed")
        b.board.append([Samples.created(), FindingAdded(finding=kept), FindingAdded(finding=fixed)])
        views = b.board.append(
            [
                FindingResolved(
                    artifact=Samples.A, finding=fixed.id, resolved_by=Samples.B, reason="fixed in a-0002"
                )
            ]
        )
        assert views.open_findings(Samples.A) == [kept]

    def test_promotion_closes_only_the_findings_of_the_level_it_leaves(self) -> None:
        b = Board()
        at_raw = Samples.finding(finding="f-raw")
        at_experimental = Samples.finding(level=Samples.EXPERIMENTAL, finding="f-exp")
        b.board.append(
            [Samples.created(), FindingAdded(finding=at_raw), FindingAdded(finding=at_experimental)]
        )
        views = b.board.append([b.promoted()])
        assert views.open_findings(Samples.A) == [at_experimental]

    def test_findings_get_unique_ids_without_coordination(self) -> None:
        a = Finding.model_validate(Samples.finding().model_dump(exclude={"id"}))
        b = Finding.model_validate(Samples.finding().model_dump(exclude={"id"}))
        assert a.id != b.id and a.id.startswith("f-")

    def test_a_sign_off_counts_for_the_version_it_saw(self) -> None:
        b = Board()
        views = b.board.append([Samples.created()])
        b.board.append([SignedOff(artifact=Samples.A, team=Samples.TESTER, version=views.version(Samples.A))])
        assert views.signed_off(Samples.A, Samples.TESTER)
        assert not views.signed_off(Samples.A, Samples.EXECUTOR)
        b.board.append([Samples.commit()])  # the artifact changed: the sign-off is for an old version
        assert not views.signed_off(Samples.A, Samples.TESTER)

    def test_a_commit_is_unjudged_until_a_gate_rules(self) -> None:
        b = Board()
        views = b.board.append([Samples.created(), Samples.commit(sha="one")])
        assert views.unjudged_commit(Samples.A) == "one"
        views = b.board.append([Escalated(artifact=Samples.A, reason="unsure")])
        assert views.unjudged_commit(Samples.A) is None
        assert views.last_commit(Samples.A) == "one"

    def test_claims_expire_with_time(self) -> None:
        b = Board()
        views = b.board.append([Samples.created(), b.claim(minutes=10)])
        assert views.claim_on(Samples.A, b.clock.now()) is not None
        b.clock.advance(timedelta(minutes=11))
        assert views.claim_on(Samples.A, b.clock.now()) is None
        assert views.active_claims(b.clock.now()) == []

    def test_release_by_another_team_keeps_the_claim(self) -> None:
        b = Board()
        views = b.board.append(
            [Samples.created(), b.claim(), Released(artifact=Samples.A, team=Samples.TESTER)]
        )
        assert views.claim_on(Samples.A, b.clock.now()) is not None
        views = b.board.append([Released(artifact=Samples.A, team=Samples.EXECUTOR)])
        assert views.claim_on(Samples.A, b.clock.now()) is None

    def test_history_is_per_artifact_and_in_order(self) -> None:
        b = Board()
        views = b.board.append([Samples.created(), Samples.created(Samples.B), Samples.commit()])
        assert views.history(Samples.A) == [Samples.created(), Samples.commit()]
        assert views.history(Samples.B) == [Samples.created(Samples.B)]

    def test_version_changes_only_when_the_artifact_does(self) -> None:
        b = Board()
        views = b.board.append([Samples.created(), Samples.commit()])
        assert views.version(Samples.A) == 2
        views = b.board.append(
            [
                b.claim(),
                Released(artifact=Samples.A, team=Samples.EXECUTOR),
                SignedOff(artifact=Samples.A, team=Samples.TESTER, version=2),
                FindingAdded(finding=Samples.finding()),
            ]
        )
        assert views.version(Samples.A) == 2


class TestSplitOrder:
    def test_children_of_a_split_wait_for_the_earlier_ones_to_merge(self) -> None:
        b = Board()
        parent = Samples.artifact()
        kids = [
            Artifact(
                id=ArtifactId(f"{parent.id}.{i}"),
                task=f"part {i}",
                branch=f"raw/{parent.id}.{i}",
                level=Samples.RAW,
                parent=parent.id,
                created_by=Samples.EXECUTOR,
            )
            for i in (1, 2, 3)
        ]
        views = b.board.append([ArtifactCreated(artifact=a) for a in (parent, *kids)])
        assert [views.waits_for(k.id) for k in kids] == [None, kids[0].id, kids[0].id]
        first_merged = Promoted(
            artifact=kids[0].id,
            from_level=Samples.RAW,
            to_level=Samples.EXPERIMENTAL,
            gate_decision=None,
            commit="m1",
        )
        views = b.board.append([first_merged])
        assert [views.waits_for(k.id) for k in kids] == [None, None, kids[1].id]
        views = b.board.append([Escalated(artifact=kids[1].id, reason="stuck")])
        assert views.waits_for(kids[2].id) is None  # a parked sibling doesn't hold the rest
        assert views.waits_for(parent.id) is None


class TestBlackboardReplay:
    def test_sees_events_appended_by_others(self) -> None:
        b = Board()
        b.log.append([Samples.created()])
        assert [a.id for a in b.board.current().artifacts_at(Samples.RAW)] == [Samples.A]

    def test_rebuild_and_a_fresh_board_reproduce_the_same_views(self) -> None:
        b = Board()
        b.board.append(
            [Samples.created(), b.claim(), Samples.commit(), FindingAdded(finding=Samples.finding())]
        )
        b.board.append([Released(artifact=Samples.A, team=Samples.EXECUTOR), b.promoted()])
        live = b.board.current()
        snapshot = deepcopy(live)  # the views are updated in place
        assert b.board.rebuild() == snapshot
        assert Blackboard(b.log).current() == snapshot
