from agentswarm.board.artifacts import ArtifactFactory
from agentswarm.board.blackboard import Blackboard
from agentswarm.board.eventlog import InMemoryEventLog
from agentswarm.control.router import FindingRouter
from agentswarm.core.clock import FakeClock
from agentswarm.core.config import LevelLadder
from agentswarm.core.domain import (
    ArtifactId,
    DecisionId,
    Escalated,
    EscalationResolved,
    Finding,
    FindingAdded,
    FindingKind,
    Level,
    Promoted,
    Resolution,
    Severity,
)
from tests.support.samples import Samples

LADDER = LevelLadder(levels=(Samples.RAW, Samples.EXPERIMENTAL, Level("candidate")))


class Routing:
    """A board with artifact a-0001 promoted into experimental, and a router over it."""

    def __init__(self) -> None:
        self.board = Blackboard(InMemoryEventLog(Samples.PROJECT, FakeClock(Samples.AT)))
        self.artifacts = ArtifactFactory(self.board, LADDER)
        self.router = FindingRouter(self.board, self.artifacts, LADDER)
        self.original = self.artifacts.create("Add farewell", Samples.EXECUTOR)
        self.board.append([self.promote(self.original.id)])

    @staticmethod
    def promote(id: ArtifactId, to: Level = Samples.EXPERIMENTAL) -> Promoted:
        return Promoted(artifact=id, from_level=Samples.RAW, to_level=to, gate_decision=DecisionId("d-1"))

    def finding(self, severity: Severity = Severity.MAJOR, level: Level = Samples.EXPERIMENTAL) -> Finding:
        finding = Finding(
            artifact=self.original.id,
            level=level,
            team=Samples.TESTER,
            kind=FindingKind.EDGE_CASE,
            severity=severity,
            detail="Empty names: farewell('') says 'Goodbye, !'",
        )
        self.board.append([FindingAdded(finding=finding)])
        return finding


class TestFindingRouter:
    def test_a_blocking_finding_becomes_one_fix_artifact(self) -> None:
        r = Routing()
        finding = r.finding()
        assert r.router.step() == 1
        assert r.router.step() == 0  # never twice
        views = r.board.current()
        fix_id = views.fix_for(finding.id)
        assert fix_id is not None
        fix = views.artifact(fix_id)
        assert (fix.level, fix.parent, fix.fixes, fix.created_by) == (
            Samples.RAW,
            r.original.id,
            finding.id,
            "router",
        )
        assert "Add farewell" in fix.task and "Goodbye, !" in fix.task

    def test_parked_or_dropped_artifacts_get_no_fixes(self) -> None:
        r = Routing()
        r.finding()
        r.board.append([Escalated(artifact=r.original.id, reason="a person decides")])
        assert r.router.step() == 0
        r.board.append([EscalationResolved(artifact=r.original.id, resolution=Resolution.DROP)])
        assert r.router.step() == 0

    def test_minor_findings_are_not_routed(self) -> None:
        r = Routing()
        r.finding(Severity.MINOR)
        assert r.router.step() == 0

    def test_findings_at_the_lowest_level_are_left_to_the_executor(self) -> None:
        r = Routing()
        raw = r.artifacts.create("Still on its branch", Samples.EXECUTOR)
        r.board.append(
            [
                FindingAdded(
                    finding=Finding(
                        artifact=raw.id,
                        level=Samples.RAW,
                        team=Samples.TESTER,
                        kind=FindingKind.INCOMPLETE,
                        severity=Severity.BLOCKER,
                        detail="x",
                    )
                )
            ]
        )
        assert r.router.step() == 0

    def test_the_finding_is_resolved_when_its_fix_reaches_the_findings_level(self) -> None:
        r = Routing()
        finding = r.finding()
        r.router.step()
        fix = r.board.current().fix_for(finding.id)
        assert fix is not None
        assert r.router.step() == 0  # the fix is still at raw
        assert r.board.current().open_findings(r.original.id) == [finding]
        r.board.append([r.promote(fix)])
        assert r.router.step() == 1
        assert r.board.current().open_findings(r.original.id) == []
