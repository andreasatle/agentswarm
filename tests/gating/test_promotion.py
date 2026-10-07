from datetime import timedelta
from pathlib import Path

from agentswarm.core.domain import (
    DecisionId,
    FindingKind,
    Promoted,
    PromotionSettled,
    PromotionStarted,
    Rejected,
)
from agentswarm.gating.merge_queue import MergeQueue, MergeRequest
from agentswarm.gating.publication import LevelPublisher
from tests.support.harness import Checks, Harness
from tests.support.samples import Judged, Samples


class TestMergeQueue:
    def test_merges_and_promotes(self, tmp_path: Path) -> None:
        h = Harness(tmp_path)
        artifact = h.create()
        sha = h.push(artifact, {"a.txt": "a\n"})
        queue = MergeQueue(h.repository, h.gate, LevelPublisher(h.board, h.repository))
        queue.enqueue(
            MergeRequest(context=h.contexts.build(artifact.id), commit=sha, decision=DecisionId("d-1"))
        )
        (event,) = queue.process()
        assert isinstance(event, Promoted)
        assert "a.txt" in h.repository.files_at("experimental")
        assert len(queue) == 0

    def test_conflict_rejects_as_a_conflict_and_leaves_the_level_untouched(self, tmp_path: Path) -> None:
        h = Harness(tmp_path)
        a, b = h.create(Samples.A), h.create(Samples.B)
        sha_a = h.push(a, {"shared.txt": "a\n"})
        sha_b = h.push(b, {"shared.txt": "b\n"})
        queue = MergeQueue(h.repository, h.gate, LevelPublisher(h.board, h.repository))
        queue.enqueue(MergeRequest(context=h.contexts.build(a.id), commit=sha_a, decision=DecisionId("d-1")))
        queue.enqueue(MergeRequest(context=h.contexts.build(b.id), commit=sha_b, decision=DecisionId("d-2")))
        first, second = queue.process()
        assert isinstance(first, Promoted)
        assert isinstance(second, Rejected)
        assert second.findings[0].kind is FindingKind.CONFLICT
        assert second.gate_decision == "d-2"

    def test_checks_rerun_on_the_merged_result(self, tmp_path: Path) -> None:
        # Each branch passes alone, but together they contain both files, which the check forbids.
        h = Harness(
            tmp_path,
            checks=[
                Checks.python(
                    "not-both",
                    "import pathlib, sys; "
                    "sys.exit(1 if all(pathlib.Path(f).exists() for f in ('a', 'b')) else 0)",
                )
            ],
        )
        a, b = h.create(Samples.A), h.create(Samples.B)
        sha_a, sha_b = h.push(a, {"a": ""}), h.push(b, {"b": ""})
        tip_before = h.repository.tip("experimental")
        queue = MergeQueue(h.repository, h.gate, LevelPublisher(h.board, h.repository))
        queue.enqueue(MergeRequest(context=h.contexts.build(a.id), commit=sha_a, decision=DecisionId("d-1")))
        queue.enqueue(MergeRequest(context=h.contexts.build(b.id), commit=sha_b, decision=DecisionId("d-2")))
        first, second = queue.process()
        assert isinstance(first, Promoted)
        assert isinstance(second, Rejected)
        assert second.findings[0].kind is FindingKind.TEST_FAILURE
        assert h.repository.tip("experimental") != tip_before
        assert h.repository.files_at("experimental") == ["a"]


class TestLevelPublisher:
    """A branch move is recorded before and after, and a crash in between is repaired (CODEX_AUDIT.md F03)."""

    @staticmethod
    def promoted(commit: str) -> Promoted:
        return Promoted(
            artifact=Samples.A,
            from_level=Samples.RAW,
            to_level=Samples.EXPERIMENTAL,
            gate_decision=None,
            commit=commit,
        )

    def test_publish_moves_the_branch_and_records_it(self, tmp_path: Path) -> None:
        h = Harness(tmp_path)
        sha = h.push(h.create(), {"a.txt": "a\n"})
        views = LevelPublisher(h.board, h.repository).publish(Samples.EXPERIMENTAL, sha, [self.promoted(sha)])
        assert h.repository.tip("experimental") == sha
        assert views.artifact(Samples.A).level == Samples.EXPERIMENTAL
        assert views.moving() == []

    def test_a_crash_after_the_branch_moved_is_settled_as_promoted(self, tmp_path: Path) -> None:
        h = Harness(tmp_path)
        sha = h.push(h.create(), {"a.txt": "a\n"})
        before = h.repository.tip("experimental")
        h.board.append(
            [
                PromotionStarted(
                    branch=Samples.EXPERIMENTAL, previous=before, target=sha, promotions=(self.promoted(sha),)
                )
            ]
        )
        h.repository.fast_forward("experimental", sha, before)  # ... and the process died here
        assert LevelPublisher(h.board, h.repository).recover() == 1
        views = h.board.current()
        assert views.artifact(Samples.A).level == Samples.EXPERIMENTAL
        assert views.merge_commit(Samples.A) == sha
        assert views.moving() == []

    def test_a_crash_before_the_branch_moved_is_settled_as_not_promoted(self, tmp_path: Path) -> None:
        h = Harness(tmp_path)
        sha = h.push(h.create(), {"a.txt": "a\n"})
        before = h.repository.tip("experimental")
        h.board.append(
            [
                PromotionStarted(
                    branch=Samples.EXPERIMENTAL, previous=before, target=sha, promotions=(self.promoted(sha),)
                )
            ]
        )
        assert LevelPublisher(h.board, h.repository).recover() == 1
        views = h.board.current()
        assert views.artifact(Samples.A).level == Samples.RAW
        assert views.unjudged_commit(Samples.A) == sha  # judged again on the next step
        assert h.board.current().moving() == []

    def test_the_merge_queue_moves_the_level_only_after_checks_pass(self, tmp_path: Path) -> None:
        h = Harness(tmp_path, checks=[Checks.python("fails", "import sys; sys.exit(1)")])
        artifact = h.create()
        sha = h.push(artifact, {"a.txt": "a\n"})
        before = h.repository.tip("experimental")
        queue = MergeQueue(h.repository, h.gate, LevelPublisher(h.board, h.repository))
        queue.enqueue(MergeRequest(context=h.contexts.build(artifact.id), commit=sha, decision=None))
        (event,) = queue.process()
        assert isinstance(event, Rejected)
        assert h.repository.tip("experimental") == before
        assert not any(
            isinstance(e, PromotionStarted | PromotionSettled)
            for e in (envelope.event for envelope in h.log.read(after=0))
        )


class TestPromoter:
    def test_promotes_an_artifact_with_an_unjudged_commit(self, tmp_path: Path) -> None:
        h = Harness(tmp_path)
        artifact = h.create()
        h.push(artifact, {"a.txt": "a\n"})
        assert h.promoter.step() == 1
        views = h.board.current()
        assert views.artifact(artifact.id).level == Samples.EXPERIMENTAL
        assert views.active_claims(h.clock.now()) == []
        assert "a.txt" in h.repository.files_at("experimental")

    def test_ignores_artifacts_without_commits(self, tmp_path: Path) -> None:
        h = Harness(tmp_path)
        h.create()
        assert h.promoter.step() == 0
        assert h.decider.calls == []

    def test_judges_each_commit_only_once(self, tmp_path: Path) -> None:
        h = Harness(tmp_path, answers=[Judged.incomplete()])
        artifact = h.create()
        h.push(artifact, {"a.txt": "a\n"})
        assert h.promoter.step() == 1
        assert h.promoter.step() == 0
        views = h.board.current()
        assert views.artifact(artifact.id).level == Samples.RAW
        assert len(views.open_findings(artifact.id)) == 1
        h.push(artifact, {"a.txt": "fixed\n"})
        assert h.promoter.step() == 1

    def test_skips_artifacts_claimed_by_a_team(self, tmp_path: Path) -> None:
        h = Harness(tmp_path)
        artifact = h.create()
        h.push(artifact, {"a.txt": "a\n"})
        h.claims.claim(artifact.id, Samples.EXECUTOR, timedelta(minutes=10))
        assert h.promoter.step() == 0

    def test_judges_the_announced_commit_not_later_unannounced_ones(self, tmp_path: Path) -> None:
        h = Harness(tmp_path, checks=[Checks.no_file_named("unannounced.txt")])
        artifact = h.create()
        h.push(artifact, {"a.txt": "a\n"})
        unannounced = h.push(artifact, {"unannounced.txt": "x"}, announce=False)
        assert h.promoter.step() == 1
        assert h.board.current().artifact(artifact.id).level == Samples.EXPERIMENTAL
        assert h.repository.files_at("experimental") == ["a.txt"]
        assert h.repository.tip(artifact.branch) == unannounced  # the branch was not moved

    def test_rejection_is_recorded_with_findings(self, tmp_path: Path) -> None:
        h = Harness(tmp_path, checks=[Checks.no_file_named("bad.txt")])
        artifact = h.create()
        h.push(artifact, {"bad.txt": "x"})
        h.promoter.step()
        last = h.board.current().history(artifact.id)[-2]  # followed by the gate's Released
        assert isinstance(last, Rejected)
        assert last.gate_decision is None

    def test_after_promotion_the_artifact_still_shows_its_own_change(self, tmp_path: Path) -> None:
        h = Harness(tmp_path)
        artifact = h.create()
        h.push(artifact, {"a.txt": "a\n"})
        h.promoter.step()
        views = h.board.current()
        assert views.merge_commit(artifact.id) == h.repository.tip("experimental")
        ctx = h.contexts.build(artifact.id)
        assert ctx.merge_commit == h.repository.tip("experimental")
        assert "a.txt" in ctx.diff_summary  # not empty, although the branch is now inside the level
