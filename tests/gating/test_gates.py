from pathlib import Path

import pytest

from agentswarm.core.config import CheckConfig, JevMode
from agentswarm.core.domain import FindingKind, Severity
from agentswarm.gating.checks import CommandCheck
from agentswarm.gating.gate import Gate, GateOutcome
from agentswarm.judging.judges import ChangeReadiness, Judge, JudgeState
from agentswarm.judging.policy import Verdict
from agentswarm.workspace.sandbox import LocalSandbox
from tests.support.harness import Checks, Harness
from tests.support.samples import Judged, Samples


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


class Gated:
    """A harness with one artifact pushed, and a detached worktree of its commit to evaluate."""

    def __init__(self, h: Harness, files: dict[str, str] | None = None) -> None:
        self.h = h
        self.artifact = h.create(task="Add a farewell function")
        self.commit = h.push(self.artifact, files or {"greeting.py": "def farewell(): ...\n"})
        self.worktree = h.repository.checkout_detached(self.commit, "check")

    def evaluate(self, gate: Gate | None = None) -> GateOutcome:
        return (gate or self.h.gate).evaluate(self.h.contexts.build(self.artifact.id), self.worktree)


class TestGate:
    def test_hard_check_failure_rejects_without_asking_the_judge(self, tmp_path: Path) -> None:
        g = Gated(Harness(tmp_path, [Checks.no_file_named("bad.txt")], [Judged.passing()]), {"bad.txt": "x"})
        outcome = g.evaluate()
        assert outcome.verdict is Verdict.REJECT
        assert outcome.decision is None
        (finding,) = outcome.findings
        assert finding.kind is FindingKind.TEST_FAILURE
        assert finding.severity is Severity.BLOCKER
        assert finding.team == "gate:raw->experimental"
        assert g.h.decider.calls == []

    def test_checks_stop_at_the_first_failure(self, tmp_path: Path) -> None:
        h = Harness(tmp_path)
        gate = Gate(
            Samples.RAW,
            Samples.EXPERIMENTAL,
            [
                Checks.python("first", "import sys; sys.exit(1)"),
                Checks.python("second", "import sys; sys.exit(1)"),
            ],
        )
        artifact = h.create()
        findings = gate.run_checks(tmp_path, h.contexts.build(artifact.id))
        assert [f.detail.splitlines()[0] for f in findings] == ["first failed:"]

    def test_without_a_judge_passing_checks_promote(self, tmp_path: Path) -> None:
        outcome = Gated(Harness(tmp_path)).evaluate()
        assert outcome.verdict is Verdict.PROMOTE
        assert outcome.decision is None

    def test_requires_human_turns_a_pass_into_an_escalation(self, tmp_path: Path) -> None:
        g = Gated(Harness(tmp_path))
        outcome = g.evaluate(Gate(Samples.RAW, Samples.EXPERIMENTAL, [], requires_human=True))
        assert outcome.verdict is Verdict.ESCALATE
        assert outcome.reason == "requires human approval"


class TestGateJudgingActive:
    def test_a_passing_ruling_promotes_and_links_the_decision(self, tmp_path: Path) -> None:
        g = Gated(Harness(tmp_path, answers=[Judged.passing()]))
        outcome = g.evaluate()
        assert outcome.verdict is Verdict.PROMOTE
        (decision,) = g.h.decisions.all()
        assert outcome.decision == decision.id
        assert decision.commit == g.commit
        assert decision.base == g.h.repository.tip("experimental")  # where the change forked
        assert decision.mode is JevMode.ACTIVE
        assert decision.point == "gate:raw->experimental"
        assert decision.answers["complete"] == {"noul": 0.95}
        assert decision.judge_version == Judge(ChangeReadiness).version

    def test_a_failing_ruling_rejects_with_major_findings(self, tmp_path: Path) -> None:
        g = Gated(Harness(tmp_path, answers=[Judged.incomplete()]))
        outcome = g.evaluate()
        assert outcome.verdict is Verdict.REJECT
        (finding,) = outcome.findings
        assert finding.kind is FindingKind.INCOMPLETE
        assert finding.severity is Severity.MAJOR
        assert finding.detail.startswith("The change does not address every part of the task")

    def test_an_unsure_ruling_escalates_naming_the_question(self, tmp_path: Path) -> None:
        outcome = Gated(Harness(tmp_path, answers=[Judged.unsure()])).evaluate()
        assert outcome.verdict is Verdict.ESCALATE
        assert outcome.reason == "gate:raw->experimental: judge unsure on tests_relevant (p=0.60)"

    def test_an_unavailable_judge_escalates(self, tmp_path: Path) -> None:
        g = Gated(Harness(tmp_path, answers=[]))  # a judge whose decider has nothing to say
        outcome = g.evaluate()
        assert outcome.verdict is Verdict.ESCALATE
        assert "judge unavailable" in outcome.reason
        assert g.h.decisions.all() == []

    def test_the_judge_sees_the_task_and_the_real_diff(self, tmp_path: Path) -> None:
        g = Gated(Harness(tmp_path, answers=[Judged.passing()]))
        g.evaluate()
        ((state, spec),) = g.h.decider.calls
        assert spec is ChangeReadiness
        assert isinstance(state, JudgeState)
        assert state.task == "Add a farewell function"
        assert "+def farewell(): ..." in state.diff
        assert state.north_star == Harness.NORTH_STAR


class TestGateJudgingShadow:
    def test_a_failing_ruling_is_recorded_but_the_gate_still_promotes(self, tmp_path: Path) -> None:
        g = Gated(Harness(tmp_path, answers=[Judged.incomplete()], mode=JevMode.SHADOW))
        outcome = g.evaluate()
        assert outcome.verdict is Verdict.PROMOTE
        (decision,) = g.h.decisions.all()
        assert outcome.decision == decision.id
        assert decision.mode is JevMode.SHADOW
        assert decision.ruling.verdict is Verdict.REJECT

    def test_an_unavailable_judge_does_not_block_but_is_logged(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        outcome = Gated(Harness(tmp_path, answers=[], mode=JevMode.SHADOW)).evaluate()
        assert outcome.verdict is Verdict.PROMOTE
        assert outcome.decision is None
        assert "judge unavailable for a-0001, continuing unjudged" in caplog.text


class TestJudgeState:
    def test_long_diffs_are_truncated_with_a_note(self) -> None:
        diff = "x" * (JudgeState.DIFF_LIMIT + 123)
        truncated = JudgeState.truncate(diff)
        assert truncated.startswith("x" * JudgeState.DIFF_LIMIT)
        assert truncated.endswith("[diff truncated: 123 more characters]")
        assert JudgeState.truncate("short") == "short"
