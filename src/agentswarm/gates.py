"""Gates: hard checks first, then a judgement, then a verdict (proposal §11).

Phase 0 has no policy layer: the decider returns a verdict directly. Jev, thresholds and per-field
policy replace FakeDecider in phase 2 behind the same Decider interface.
"""

from collections.abc import Sequence
from enum import StrEnum
from pathlib import Path
from typing import Protocol, Self

from pydantic import BaseModel, ConfigDict

from agentswarm.config import CheckConfig, GateConfig
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


class Verdict(StrEnum):
    PROMOTE = "promote"
    REJECT = "reject"
    ESCALATE = "escalate"


class Judgement(Frozen):
    """What a decider concluded about one artifact at one decision point."""

    decision: DecisionId
    verdict: Verdict
    findings: tuple[Finding, ...] = ()  # why, when the verdict is REJECT


class Decider(Protocol):
    def judge(self, point: str, ctx: ArtifactContext) -> Judgement: ...


class FakeDecider:
    """Returns scripted verdicts in order, then repeats the last one. Records every call."""

    def __init__(self, verdicts: Sequence[Verdict] = (Verdict.PROMOTE,)) -> None:
        if not verdicts:
            raise ValueError("FakeDecider needs at least one verdict")
        self._verdicts = list(verdicts)
        self.calls: list[tuple[str, ArtifactContext]] = []

    def judge(self, point: str, ctx: ArtifactContext) -> Judgement:
        self.calls.append((point, ctx))
        verdict = self._verdicts.pop(0) if len(self._verdicts) > 1 else self._verdicts[0]
        decision = DecisionId(f"d-{len(self.calls):04d}")
        if verdict is not Verdict.REJECT:
            return Judgement(decision=decision, verdict=verdict)
        finding = Finding(
            artifact=ctx.artifact.id,
            level=ctx.artifact.level,
            team=TeamName(point),
            kind=FindingKind.INCOMPLETE,
            severity=Severity.MAJOR,
            detail="Rejected by the fake decider",
        )
        return Judgement(decision=decision, verdict=verdict, findings=(finding,))


class GateOutcome(Frozen):
    verdict: Verdict
    decision: DecisionId | None  # None when hard checks rejected before any judgement
    findings: tuple[Finding, ...] = ()
    reason: str = ""  # why, when the verdict is ESCALATE


class Gate:
    """The rule for moving an artifact from one level to the next."""

    def __init__(
        self,
        from_level: Level,
        to_level: Level,
        checks: Sequence[HardCheck],
        decider: Decider,
        requires_human: bool = False,
    ) -> None:
        self.from_level = from_level
        self.to_level = to_level
        self._checks = tuple(checks)
        self._decider = decider
        self._requires_human = requires_human

    @classmethod
    def from_config(cls, gate: GateConfig, checks: CheckSet, decider: Decider) -> Self:
        return cls(
            gate.from_level,
            gate.to_level,
            checks.build(gate.hard_checks),
            decider,
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
        judgement = self._decider.judge(self.name, ctx)
        match judgement.verdict:
            case Verdict.PROMOTE if self._requires_human:
                return GateOutcome(
                    verdict=Verdict.ESCALATE, decision=judgement.decision, reason="requires human approval"
                )
            case Verdict.PROMOTE:
                return GateOutcome(verdict=Verdict.PROMOTE, decision=judgement.decision)
            case Verdict.REJECT:
                findings = judgement.findings or (
                    self._finding(ctx, FindingKind.INCOMPLETE, "Rejected without a stated reason"),
                )
                return GateOutcome(verdict=Verdict.REJECT, decision=judgement.decision, findings=findings)
            case Verdict.ESCALATE:
                return GateOutcome(
                    verdict=Verdict.ESCALATE,
                    decision=judgement.decision,
                    reason=f"{self.name}: judgement unsure",
                )

    def conflict_finding(self, ctx: ArtifactContext) -> Finding:
        return self._finding(
            ctx, FindingKind.INCONSISTENT, f"Conflicts with {self.to_level}; rebase onto it and resolve"
        )

    def _finding(self, ctx: ArtifactContext, kind: FindingKind, detail: str) -> Finding:
        return Finding(
            artifact=ctx.artifact.id,
            level=self.from_level,
            team=self.team,
            kind=kind,
            severity=Severity.BLOCKER,
            detail=detail,
        )
