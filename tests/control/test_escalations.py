from pathlib import Path

import pytest

from agentswarm.board.artifacts import ArtifactFactory
from agentswarm.control.escalations import Escalations
from agentswarm.core.domain import (
    ArtifactCreated,
    ArtifactId,
    Escalated,
    Resolution,
    SignedOff,
    Sized,
    Unbuildable,
)
from agentswarm.teams.decomposer import Decomposer
from agentswarm.teams.executor import FakeExecutor
from agentswarm.teams.runner import Relevance
from tests.support.harness import Harness
from tests.support.samples import Judged, Samples


class Queue:
    """A harness whose first artifact has a commit and an open escalation."""

    def __init__(self, tmp_path: Path, **harness: object) -> None:
        self.h = Harness(tmp_path, **harness)  # type: ignore[arg-type]
        self.artifact = self.h.create()
        self.commit = self.h.push(self.artifact, {"a.txt": "a\n"})
        self.h.board.append([Escalated(artifact=self.artifact.id, reason="the judge is unsure")])
        self.queue = Escalations(self.h.board, self.h.decisions)


class TestEscalations:
    def test_lists_open_escalations_with_their_decisions(self, tmp_path: Path) -> None:
        q = Queue(tmp_path)
        (open_,) = q.queue.open()
        assert (open_.artifact, open_.reason, open_.level) == (Samples.A, "the judge is unsure", Samples.RAW)
        assert "reason: the judge is unsure" in open_.render()

    def test_retry_sends_the_artifact_back_to_the_executor(self, tmp_path: Path) -> None:
        q = Queue(tmp_path)
        executor = FakeExecutor(Samples.RAW)
        assert executor.relevant(q.h.contexts.build(q.artifact.id)) is Relevance.NO
        q.queue.resolve(q.artifact.id, Resolution.RETRY, "try again")
        assert q.queue.open() == []
        assert executor.relevant(q.h.contexts.build(q.artifact.id)) is Relevance.YES

    def test_a_person_may_retry_a_step_every_model_failed_instead_of_splitting_it(
        self, tmp_path: Path
    ) -> None:
        h = Harness(tmp_path)
        artifact = h.create()
        queue = Escalations(h.board, h.decisions)
        with pytest.raises(ValueError, match="isn't waiting to be split"):
            queue.resolve(artifact.id, Resolution.RETRY)
        back = Unbuildable(artifact=artifact.id, team=Samples.EXECUTOR, reason="a framework rule")
        h.board.append([Sized(artifact=artifact.id, fits=True), back])
        assert Decomposer.failed(h.contexts.build(artifact.id))
        queue.resolve(artifact.id, Resolution.RETRY, "rejected by a framework rule, not the work")
        assert not Decomposer.failed(h.contexts.build(artifact.id))  # the planner leaves it alone
        assert FakeExecutor(Samples.RAW).relevant(h.contexts.build(artifact.id)) is Relevance.YES

    def test_drop_retires_a_task_and_every_step_split_from_it(self, tmp_path: Path) -> None:
        h = Harness(tmp_path)
        task = h.create()
        factory = ArtifactFactory(h.board, Harness.LADDER)
        parts = [factory.child(task, i, f"part {i}", Samples.EXECUTOR) for i in (1, 2)]
        h.board.append([ArtifactCreated(artifact=p) for p in parts])
        dropped = Escalations(h.board, h.decisions).drop(task.id, "split too eagerly")
        assert sorted(dropped) == sorted([task.id, *(p.id for p in parts)])
        assert not any(h.board.current().active(i) for i in dropped)

    def test_accept_promotes_through_the_gate_without_asking_the_judge(self, tmp_path: Path) -> None:
        q = Queue(tmp_path, answers=[Judged.unsure()])  # the judge would escalate again
        q.queue.resolve(q.artifact.id, Resolution.ACCEPT, "looks right to me")
        assert q.h.promoter.step() == 1
        assert q.h.board.current().artifact(q.artifact.id).level == Samples.EXPERIMENTAL
        assert q.h.decider.calls == []
        assert not q.h.board.current().accepted(q.artifact.id)  # an approval is for one gate

    def test_drop_retires_the_artifact(self, tmp_path: Path) -> None:
        q = Queue(tmp_path)
        q.queue.resolve(q.artifact.id, Resolution.DROP)
        views = q.h.board.current()
        assert views.dropped(q.artifact.id)
        assert views.artifacts_at(Samples.RAW) == []

    def test_only_open_escalations_can_be_resolved_and_only_a_paused_project_resumed(
        self, tmp_path: Path
    ) -> None:
        q = Queue(tmp_path)
        with pytest.raises(ValueError, match="no open escalation"):
            q.queue.resolve(ArtifactId("a-0099"), Resolution.RETRY)
        with pytest.raises(ValueError, match="not paused"):
            q.queue.resume()


class TestHumanGate:
    def test_a_person_accepting_lets_the_train_through(self, tmp_path: Path) -> None:
        h = Harness(tmp_path, ladder=Harness.WITH_CANDIDATE)
        artifact = h.create()
        h.push(artifact, {"a.txt": "a\n"})
        h.promoter.step()
        h.board.append(
            [
                SignedOff(
                    artifact=artifact.id, team=Samples.TESTER, version=h.board.current().version(artifact.id)
                )
            ]
        )
        train = h.train((Samples.TESTER,), requires_human=True)
        assert train.step() == 1  # asks
        assert train.step() == 0  # once
        Escalations(h.board, h.decisions).resolve(artifact.id, Resolution.ACCEPT)
        assert train.step() == 1
        assert h.board.current().artifact(artifact.id).level == Samples.CANDIDATE

    def test_a_dropped_artifact_does_not_hold_the_train(self, tmp_path: Path) -> None:
        h = Harness(tmp_path, ladder=Harness.WITH_CANDIDATE)
        first, second = h.create(Samples.A), h.create(Samples.B)
        for artifact in (first, second):
            h.push(artifact, {f"{artifact.id}.txt": "x\n"})
            h.promoter.step()
        h.board.append([Escalated(artifact=first.id, reason="abandoned")])
        Escalations(h.board, h.decisions).resolve(first.id, Resolution.DROP)
        h.board.append(
            [SignedOff(artifact=second.id, team=Samples.TESTER, version=h.board.current().version(second.id))]
        )
        assert h.train((Samples.TESTER,)).step() == 1
        views = h.board.current()
        assert views.artifact(second.id).level == Samples.CANDIDATE
        assert views.artifact(first.id).level == Samples.EXPERIMENTAL  # rode along, without a promotion
        assert h.repository.tip("candidate") == h.repository.tip("experimental")
