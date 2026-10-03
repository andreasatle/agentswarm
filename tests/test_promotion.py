from datetime import timedelta
from pathlib import Path

from agentswarm.domain import DecisionId, FindingKind, Promoted, Rejected
from agentswarm.gates import Verdict
from agentswarm.merge_queue import MergeQueue, MergeRequest
from tests.harness import Checks, Harness
from tests.samples import Samples


class TestMergeQueue:
    def test_merges_and_promotes(self, tmp_path: Path) -> None:
        h = Harness(tmp_path)
        artifact = h.create()
        sha = h.push(artifact, {"a.txt": "a\n"})
        queue = MergeQueue(h.repository, h.gate)
        queue.enqueue(
            MergeRequest(context=h.contexts.build(artifact.id), commit=sha, decision=DecisionId("d-1"))
        )
        (event,) = queue.process()
        assert isinstance(event, Promoted)
        assert "a.txt" in h.repository.files_at("experimental")
        assert len(queue) == 0

    def test_conflict_rejects_as_inconsistent_and_leaves_the_level_untouched(self, tmp_path: Path) -> None:
        h = Harness(tmp_path)
        a, b = h.create(Samples.A), h.create(Samples.B)
        sha_a = h.push(a, {"shared.txt": "a\n"})
        sha_b = h.push(b, {"shared.txt": "b\n"})
        queue = MergeQueue(h.repository, h.gate)
        queue.enqueue(MergeRequest(context=h.contexts.build(a.id), commit=sha_a, decision=DecisionId("d-1")))
        queue.enqueue(MergeRequest(context=h.contexts.build(b.id), commit=sha_b, decision=DecisionId("d-2")))
        first, second = queue.process()
        assert isinstance(first, Promoted)
        assert isinstance(second, Rejected)
        assert second.findings[0].kind is FindingKind.INCONSISTENT
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
        queue = MergeQueue(h.repository, h.gate)
        queue.enqueue(MergeRequest(context=h.contexts.build(a.id), commit=sha_a, decision=DecisionId("d-1")))
        queue.enqueue(MergeRequest(context=h.contexts.build(b.id), commit=sha_b, decision=DecisionId("d-2")))
        first, second = queue.process()
        assert isinstance(first, Promoted)
        assert isinstance(second, Rejected)
        assert second.findings[0].kind is FindingKind.TEST_FAILURE
        assert h.repository.tip("experimental") != tip_before
        assert h.repository.files_at("experimental") == ["a"]


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
        h = Harness(tmp_path, verdicts=[Verdict.REJECT])
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
