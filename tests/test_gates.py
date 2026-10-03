from pathlib import Path

from agentswarm.config import CheckConfig
from agentswarm.domain import FindingKind, Severity
from agentswarm.gates import CommandCheck, FakeDecider, Gate, Verdict
from agentswarm.sandbox import LocalSandbox
from tests.harness import Checks, Harness
from tests.samples import Samples


class TestCommandCheck:
    def test_exit_code_decides(self, tmp_path: Path) -> None:
        assert Checks.python("ok", "print('fine')").run(tmp_path).passed
        failed = Checks.python("bad", "import sys; print('broken'); sys.exit(3)").run(tmp_path)
        assert not failed.passed
        assert "broken" in failed.output

    def test_runs_in_the_given_directory(self, tmp_path: Path) -> None:
        (tmp_path / "marker").write_text("")
        assert not Checks.no_file_named("marker").run(tmp_path).passed

    def test_timeout_fails(self, tmp_path: Path) -> None:
        check = CommandCheck("slow", CheckConfig(command=("sleep", "5"), timeout_s=0.1), LocalSandbox())
        result = check.run(tmp_path)
        assert not result.passed
        assert "timed out" in result.output

    def test_missing_program_fails(self, tmp_path: Path) -> None:
        check = CommandCheck("missing", CheckConfig(command=("no-such-program-xyz",)), LocalSandbox())
        assert not check.run(tmp_path).passed


class TestGate:
    def test_hard_check_failure_rejects_without_asking_the_decider(self, tmp_path: Path) -> None:
        h = Harness(tmp_path, checks=[Checks.no_file_named("bad.txt")])
        artifact = h.create()
        sha = h.push(artifact, {"bad.txt": "x"})
        worktree = h.repository.checkout_detached(sha, "check")
        outcome = h.gate.evaluate(h.contexts.build(artifact.id), worktree)
        assert outcome.verdict is Verdict.REJECT
        assert outcome.decision is None
        (finding,) = outcome.findings
        assert finding.kind is FindingKind.TEST_FAILURE
        assert finding.severity is Severity.BLOCKER
        assert finding.team == "gate:raw->experimental"
        assert h.decider.calls == []

    def test_checks_stop_at_the_first_failure(self, tmp_path: Path) -> None:
        h = Harness(tmp_path)
        gate = Gate(
            Samples.RAW,
            Samples.EXPERIMENTAL,
            [
                Checks.python("first", "import sys; sys.exit(1)"),
                Checks.python("second", "import sys; sys.exit(1)"),
            ],
            FakeDecider(),
        )
        artifact = h.create()
        findings = gate.run_checks(tmp_path, h.contexts.build(artifact.id))
        assert [f.detail.splitlines()[0] for f in findings] == ["first failed:"]

    def test_decider_verdicts_map_to_outcomes(self, tmp_path: Path) -> None:
        h = Harness(tmp_path, verdicts=[Verdict.PROMOTE, Verdict.REJECT, Verdict.ESCALATE])
        artifact = h.create()
        worktree = h.repository.checkout_detached(h.push(artifact, {"a.txt": "a"}), "check")
        ctx = h.contexts.build(artifact.id)
        promoted, rejected, escalated = (h.gate.evaluate(ctx, worktree) for _ in range(3))
        assert promoted.verdict is Verdict.PROMOTE and promoted.decision == "d-0001"
        assert rejected.verdict is Verdict.REJECT and len(rejected.findings) == 1
        assert escalated.verdict is Verdict.ESCALATE and escalated.reason
        assert [point for point, _ in h.decider.calls] == ["gate:raw->experimental"] * 3

    def test_requires_human_turns_a_pass_into_an_escalation(self, tmp_path: Path) -> None:
        h = Harness(tmp_path)
        gate = Gate(Samples.RAW, Samples.EXPERIMENTAL, [], FakeDecider(), requires_human=True)
        artifact = h.create()
        worktree = h.repository.checkout_detached(h.push(artifact, {"a.txt": "a"}), "check")
        outcome = gate.evaluate(h.contexts.build(artifact.id), worktree)
        assert outcome.verdict is Verdict.ESCALATE
        assert outcome.reason == "requires human approval"
