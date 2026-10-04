from datetime import timedelta
from pathlib import Path
from typing import Self

from agentswarm.core.domain import (
    Artifact,
    ArtifactId,
    Escalated,
    Finding,
    FindingAdded,
    FindingKind,
    Promoted,
    Severity,
    SignedOff,
)
from agentswarm.gating.checks import HardCheck
from agentswarm.gating.train import TrainPromoter
from tests.support.harness import Checks, Harness
from tests.support.samples import Samples

REQUIRED = (Samples.TESTER,)


class Train:
    """Artifacts merged into experimental one at a time by the real promoter, ready to ride the train."""

    def __init__(self, tmp_path: Path, files: list[dict[str, str]]) -> None:
        self.h = Harness(tmp_path, ladder=Harness.WITH_CANDIDATE)
        self.artifacts: list[Artifact] = []
        for i, content in enumerate(files):
            artifact = self.h.create(ArtifactId(f"a-{i + 1:04d}"), task=f"task {i + 1}")
            self.h.push(artifact, content)
            assert self.h.promoter.step() == 1
            self.artifacts.append(artifact)

    @classmethod
    def of(cls, tmp_path: Path, count: int) -> Self:
        return cls(tmp_path, [{f"f{i}.txt": f"{i}\n"} for i in range(count)])

    def promoter(self, checks: tuple[HardCheck, ...] = (), requires_human: bool = False) -> TrainPromoter:
        return self.h.train(REQUIRED, checks, requires_human)

    def sign_off(self, *indexes: int) -> None:
        views = self.h.board.current()
        self.h.board.append(
            [
                SignedOff(
                    artifact=self.artifacts[i].id,
                    team=Samples.TESTER,
                    version=views.version(self.artifacts[i].id),
                )
                for i in indexes
            ]
        )

    def promoted(self) -> list[ArtifactId]:
        views = self.h.board.current()
        return [a.id for a in self.artifacts if views.artifact(a.id).level == Samples.CANDIDATE]

    def finding(self, index: int, severity: Severity) -> Finding:
        finding = Finding(
            artifact=self.artifacts[index].id,
            level=Samples.EXPERIMENTAL,
            team=Samples.TESTER,
            kind=FindingKind.EDGE_CASE,
            severity=severity,
            detail="breaks",
        )
        self.h.board.append([FindingAdded(finding=finding)])
        return finding


class TestTrainPromoter:
    def test_an_open_escalation_holds_the_train(self, tmp_path: Path) -> None:
        t = Train.of(tmp_path, 2)
        t.sign_off(0, 1)
        t.h.board.append([Escalated(artifact=t.artifacts[0].id, reason="a person decides")])
        assert t.promoter().step() == 0
        assert t.promoted() == []

    def test_promotes_every_ready_artifact_in_one_fast_forward(self, tmp_path: Path) -> None:
        t = Train.of(tmp_path, 3)
        t.sign_off(0, 1, 2)
        assert t.promoter().step() == 3
        assert t.promoted() == [a.id for a in t.artifacts]
        assert t.h.repository.tip("candidate") == t.h.repository.tip("experimental")
        promotions = [
            e for a in t.artifacts for e in t.h.board.current().history(a.id) if isinstance(e, Promoted)
        ]
        assert {p.commit for p in promotions if p.to_level == Samples.CANDIDATE} == {
            t.h.repository.tip("candidate")
        }

    def test_stops_at_the_first_artifact_that_is_not_ready(self, tmp_path: Path) -> None:
        t = Train.of(tmp_path, 3)
        t.sign_off(0, 2)  # the middle one has no sign-off yet
        assert t.promoter().step() == 1
        assert t.promoted() == [t.artifacts[0].id]
        assert t.h.repository.tip("candidate") == t.h.board.current().merge_commit(t.artifacts[0].id)
        t.sign_off(1)
        assert t.promoter().step() == 2
        assert t.promoted() == [a.id for a in t.artifacts]

    def test_a_sign_off_for_an_older_version_does_not_count(self, tmp_path: Path) -> None:
        t = Train.of(tmp_path, 1)
        t.h.board.append([SignedOff(artifact=t.artifacts[0].id, team=Samples.TESTER, version=1)])
        assert t.promoter().step() == 0

    def test_blocking_findings_hold_the_train_and_minor_ones_do_not(self, tmp_path: Path) -> None:
        t = Train.of(tmp_path, 2)
        t.sign_off(0, 1)
        t.finding(0, Severity.MINOR)
        t.finding(1, Severity.MAJOR)
        assert t.promoter().step() == 1
        assert t.promoted() == [t.artifacts[0].id]

    def test_a_commit_failing_the_checks_gets_a_blocker_and_the_ones_before_it_move(
        self, tmp_path: Path
    ) -> None:
        t = Train(tmp_path, [{"a.txt": "a\n"}, {"bad.txt": "x\n"}, {"c.txt": "c\n"}])
        t.sign_off(0, 1, 2)
        promoter = t.promoter((Checks.no_file_named("bad.txt"),))
        assert promoter.step() == 2  # one promoted, one finding
        assert t.promoted() == [t.artifacts[0].id]
        (blocker,) = t.h.board.current().open_findings(t.artifacts[1].id)
        assert (blocker.severity, blocker.team) == (Severity.BLOCKER, "gate:experimental->candidate")
        assert promoter.step() == 0  # the blocker holds the rest; nothing is checked twice

    def test_a_human_gate_escalates_once_instead_of_promoting(self, tmp_path: Path) -> None:
        t = Train.of(tmp_path, 2)
        t.sign_off(0, 1)
        promoter = t.promoter(requires_human=True)
        assert promoter.step() == 1
        assert promoter.step() == 0
        assert t.promoted() == []
        (escalated,) = [e for e in t.h.board.current().history(t.artifacts[1].id) if isinstance(e, Escalated)]
        assert "needs human approval" in escalated.reason

    def test_a_claimed_artifact_holds_the_batch(self, tmp_path: Path) -> None:
        t = Train.of(tmp_path, 1)
        t.sign_off(0)
        t.h.claims.claim(t.artifacts[0].id, Samples.TESTER, timedelta(minutes=5))
        assert t.promoter().step() == 0

    def test_nothing_new_means_nothing_to_do(self, tmp_path: Path) -> None:
        t = Train.of(tmp_path, 1)
        t.sign_off(0)
        promoter = t.promoter()
        assert promoter.step() == 1
        assert promoter.step() == 0
