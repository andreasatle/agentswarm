"""Gates: hard checks first, then a judgement, then a verdict (proposal §11.2).

1. hard checks fail                        → REJECT, without asking the judge
2. the judge answers, the policy rules     → recorded as a Decision
3. shadow mode: act as if there were no judge (promote); active mode: act on the ruling
4. requires_human turns a promotion into an escalation
"""

import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol, Self

from pydantic import BaseModel, ConfigDict

from agentswarm.clock import Clock
from agentswarm.config import CheckConfig, GateConfig, JevMode
from agentswarm.deciders import Decider, DeciderError
from agentswarm.decisions import Decision, DecisionDraft, DecisionStore
from agentswarm.domain import (
    ArtifactContext,
    DecisionId,
    Finding,
    FindingKind,
    Level,
    Severity,
    TeamName,
)
from agentswarm.git import Worktree
from agentswarm.judges import Judge, JudgeState
from agentswarm.policy import Policy, QuestionVerdict, Verdict
from agentswarm.sandbox import Sandbox


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class CheckResult(Frozen):
    name: str
    passed: bool
    output: str  # the tail of stdout and stderr


class HardCheck(Protocol):
    """A deterministic check run in a directory holding the artifact's files."""

    @property
    def name(self) -> str: ...

    @property
    def kind(self) -> FindingKind: ...

    def run(self, cwd: Path) -> CheckResult: ...


class CommandCheck:
    """A hard check that runs a command in a sandbox; exit code 0 passes."""

    OUTPUT_LIMIT = 4000  # characters of output kept, from the end, for the finding

    def __init__(self, name: str, config: CheckConfig, sandbox: Sandbox) -> None:
        self._name = name
        self._config = config
        self._sandbox = sandbox

    @property
    def name(self) -> str:
        return self._name

    @property
    def kind(self) -> FindingKind:
        return self._config.kind

    def run(self, cwd: Path) -> CheckResult:
        result = self._sandbox.run(self._config.command, cwd, self._config.timeout_s)
        return CheckResult(name=self._name, passed=result.passed, output=result.output[-self.OUTPUT_LIMIT :])


class CheckSet:
    """Builds the configured hard checks by name, all running in the project's sandbox."""

    def __init__(self, configs: dict[str, CheckConfig], sandbox: Sandbox) -> None:
        self._configs = configs
        self._sandbox = sandbox

    def build(self, names: Sequence[str]) -> list[HardCheck]:
        return [CommandCheck(name, self._configs[name], self._sandbox) for name in names]


class GateJudgement:
    """Asks a judge about an artifact, rules on the answers with the policy, and records the decision."""

    def __init__(
        self,
        judge: Judge,
        policy: Policy,
        decider: Decider,
        decisions: DecisionStore,
        mode: JevMode,
        clock: Clock,
    ) -> None:
        self._judge = judge
        self._policy = policy
        self._decider = decider
        self._decisions = decisions
        self.mode = mode
        self._clock = clock

    def judge(self, point: str, ctx: ArtifactContext, commit: str, diff: str) -> Decision:
        """Ask, rule and record. Raises DeciderError if the decider gives no answers."""
        state = JudgeState.of(ctx, diff)
        answered = self._decider.decide(state, self._judge.spec)
        return self._decisions.record(
            DecisionDraft(
                point=point,
                artifact=ctx.artifact.id,
                commit=commit,
                mode=self.mode,
                judge=self._judge.name,
                state=state,
                answers=answered.answers.model_dump(mode="json"),
                ruling=self._policy.rule(answered.answers),
                model=answered.model,
                at=self._clock.now(),
            )
        )


class GateOutcome(Frozen):
    verdict: Verdict
    decision: DecisionId | None  # None when hard checks rejected before any judgement
    findings: tuple[Finding, ...] = ()
    reason: str = ""  # why, when the verdict is ESCALATE


class Gate:
    """The rule for moving an artifact from one level to the next."""

    _log = logging.getLogger(__name__)

    def __init__(
        self,
        from_level: Level,
        to_level: Level,
        checks: Sequence[HardCheck],
        judgement: GateJudgement | None = None,  # None: hard checks only
        requires_human: bool = False,
    ) -> None:
        self.from_level = from_level
        self.to_level = to_level
        self._checks = tuple(checks)
        self._judgement = judgement
        self._requires_human = requires_human

    @classmethod
    def from_config(cls, gate: GateConfig, checks: CheckSet, judgement: GateJudgement | None) -> Self:
        return cls(
            gate.from_level,
            gate.to_level,
            checks.build(gate.hard_checks),
            judgement,
            gate.requires_human,
        )

    @property
    def name(self) -> str:
        return f"gate:{self.from_level}->{self.to_level}"

    @property
    def team(self) -> TeamName:
        """The gate claims artifacts under its own name while it evaluates them."""
        return TeamName(self.name)

    def run_checks(self, cwd: Path, ctx: ArtifactContext) -> tuple[Finding, ...]:
        """Run the hard checks in order and stop at the first failure, which becomes a blocker finding."""
        for check in self._checks:
            result = check.run(cwd)
            if not result.passed:
                return (self._finding(ctx, check.kind, f"{result.name} failed:\n{result.output}"),)
        return ()

    def evaluate(self, ctx: ArtifactContext, worktree: Worktree) -> GateOutcome:
        findings = self.run_checks(worktree.path, ctx)
        if findings:
            return GateOutcome(verdict=Verdict.REJECT, decision=None, findings=findings)
        if self._judgement is None:
            return self._unless_human(GateOutcome(verdict=Verdict.PROMOTE, decision=None))
        try:
            decision = self._judgement.judge(
                self.name, ctx, worktree.head(), worktree.diff_against(self.to_level)
            )
        except DeciderError as error:
            if self._judgement.mode is JevMode.SHADOW:  # shadow mode never blocks: carry on unjudged
                self._log.warning(
                    "%s: judge unavailable for %s, continuing unjudged: %s", self.name, ctx.artifact.id, error
                )
                return self._unless_human(GateOutcome(verdict=Verdict.PROMOTE, decision=None))
            return GateOutcome(
                verdict=Verdict.ESCALATE, decision=None, reason=f"{self.name}: judge unavailable: {error}"
            )
        if decision.mode is JevMode.SHADOW:
            return self._unless_human(GateOutcome(verdict=Verdict.PROMOTE, decision=decision.id))
        return self._unless_human(self._acting_on(ctx, decision))

    def _acting_on(self, ctx: ArtifactContext, decision: Decision) -> GateOutcome:
        ruling = decision.ruling
        match ruling.verdict:
            case Verdict.PROMOTE:
                return GateOutcome(verdict=Verdict.PROMOTE, decision=decision.id)
            case Verdict.REJECT:
                findings = tuple(
                    self._finding(ctx, f.kind, f.detail, Severity.MAJOR) for f in ruling.failures
                )
                return GateOutcome(verdict=Verdict.REJECT, decision=decision.id, findings=findings)
            case Verdict.ESCALATE:
                unsure = ", ".join(
                    f"{q.field} (p={q.p_yes:.2f})"
                    for q in ruling.questions
                    if q.verdict is QuestionVerdict.UNSURE
                )
                return GateOutcome(
                    verdict=Verdict.ESCALATE,
                    decision=decision.id,
                    reason=f"{self.name}: judge unsure on {unsure}",
                )

    def _unless_human(self, outcome: GateOutcome) -> GateOutcome:
        """A gate that requires a human never promotes on its own."""
        if outcome.verdict is Verdict.PROMOTE and self._requires_human:
            return outcome.model_copy(
                update={"verdict": Verdict.ESCALATE, "reason": "requires human approval"}
            )
        return outcome

    def conflict_finding(self, ctx: ArtifactContext) -> Finding:
        return self._finding(
            ctx, FindingKind.INCONSISTENT, f"Conflicts with {self.to_level}; rebase onto it and resolve"
        )

    def _finding(
        self, ctx: ArtifactContext, kind: FindingKind, detail: str, severity: Severity = Severity.BLOCKER
    ) -> Finding:
        return Finding(
            artifact=ctx.artifact.id,
            level=self.from_level,
            team=self.team,
            kind=kind,
            severity=severity,
            detail=detail,
        )
