"""Domain model (proposal §7): artifacts, findings, claims and the events that change the blackboard."""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal, NewType

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

ArtifactId = NewType("ArtifactId", str)  # e.g. "a-0042"
TeamName = NewType("TeamName", str)
ProjectId = NewType("ProjectId", str)
DecisionId = NewType("DecisionId", str)
ClaimId = NewType("ClaimId", str)  # e.g. "c-3f9a0c1d2e4b"
FindingId = NewType("FindingId", str)  # "f-" + 12 hex digits: unique without coordination between teams

# Levels are per-domain configuration (software: raw → … → production, document: draft → … → published),
# so they are names validated against the project's level ladder, not a fixed enum.
Level = NewType("Level", str)


class Frozen(BaseModel):
    """Base for all domain values: immutable, and unknown fields are an error."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class Artifact(Frozen):
    """One unit of work: a task and the git branch that holds its current state."""

    id: ArtifactId
    task: str  # what this artifact is meant to achieve
    branch: str  # e.g. "raw/a-0042-retry-policy"
    level: Level
    parent: ArtifactId | None = None  # the artifact this one was split from, or fixes a finding on
    fixes: FindingId | None = None  # set on fix artifacts created by the finding router
    created_by: TeamName


class FindingKind(StrEnum):
    TEST_FAILURE = "test_failure"
    BUILD_FAILURE = "build_failure"
    OUT_OF_SCOPE = "out_of_scope"
    INCOMPLETE = "incomplete"
    SECURITY = "security"
    INCONSISTENT = "inconsistent"
    CONFLICT = (
        "conflict"  # doesn't merge into the next level: settled by merging that level in, not by rework
    )
    TOO_LARGE = "too_large"  # should be decomposed
    EDGE_CASE = "edge_case"  # breaks on unusual input or misuse (red team)
    UNSUPPORTED = "unsupported"  # a claim that may be wrong or lacks support (fact checker)
    UNCLEAR = "unclear"  # hard to follow for the intended reader (audience reviewer, document gate)


class Severity(StrEnum):
    INFO = "info"
    MINOR = "minor"
    MAJOR = "major"
    BLOCKER = "blocker"


class FindingIds:
    @staticmethod
    def new() -> FindingId:
        return FindingId(f"f-{uuid.uuid4().hex[:12]}")


class Finding(Frozen):
    """Typed feedback. A rejected gate always produces at least one. Resolved by a FindingResolved event."""

    id: FindingId = Field(default_factory=FindingIds.new)
    artifact: ArtifactId
    level: Level
    team: TeamName
    kind: FindingKind
    severity: Severity
    detail: str  # where and why; see proposal §11.6 for where it comes from


class Claim(Frozen):
    """A lease: one team works on one artifact until it releases or the lease expires."""

    artifact: ArtifactId
    team: TeamName
    expires_at: datetime
    id: ClaimId | None = None  # None only in logs written before claims had ids


# Events: nothing changes the blackboard except appending one of these.
# Each event's `subject` is the artifact it belongs to, which is how per-artifact history is built.


class ArtifactCreated(Frozen):
    kind: Literal["artifact_created"] = "artifact_created"
    artifact: Artifact

    @property
    def subject(self) -> ArtifactId:
        return self.artifact.id


class Claimed(Frozen):
    kind: Literal["claimed"] = "claimed"
    claim: Claim

    @property
    def subject(self) -> ArtifactId:
        return self.claim.artifact


class Released(Frozen):
    kind: Literal["released"] = "released"
    artifact: ArtifactId
    team: TeamName
    claim: ClaimId | None = None  # releases only this claim; None (older logs): whatever the team holds

    @property
    def subject(self) -> ArtifactId:
        return self.artifact


class CommitPushed(Frozen):
    kind: Literal["commit_pushed"] = "commit_pushed"
    artifact: ArtifactId
    commit: str
    model: str | None = None  # the model tier that made it, when a model did (§13.3)

    @property
    def subject(self) -> ArtifactId:
        return self.artifact


class FindingAdded(Frozen):
    kind: Literal["finding_added"] = "finding_added"
    finding: Finding

    @property
    def subject(self) -> ArtifactId:
        return self.finding.artifact


class FindingResolved(Frozen):
    kind: Literal["finding_resolved"] = "finding_resolved"
    artifact: ArtifactId  # the artifact the finding is about
    finding: FindingId
    resolved_by: ArtifactId | None  # the fix artifact, if one did it
    reason: str

    @property
    def subject(self) -> ArtifactId:
        return self.artifact


class SignedOff(Frozen):
    """An analyzer team has examined this version of the artifact, with or without findings."""

    kind: Literal["signed_off"] = "signed_off"
    artifact: ArtifactId
    team: TeamName
    version: int  # the artifact's version (BoardViews.version) when examined

    @property
    def subject(self) -> ArtifactId:
        return self.artifact


class AcceptanceFile(Frozen):
    path: str  # relative to the project root
    sha256: str


class AcceptanceWritten(Frozen):
    """An artifact's acceptance tests, written before its work by a team other than its producer.

    The producer sees them but can't change them: its commits are checked against this record.
    """

    kind: Literal["acceptance_written"] = "acceptance_written"
    artifact: ArtifactId
    team: TeamName
    commit: str  # the commit on the artifact's branch that adds them
    files: tuple[AcceptanceFile, ...]  # empty when no usable tests could be written; note says why
    note: str = ""

    @property
    def subject(self) -> ArtifactId:
        return self.artifact


class AcceptanceRestored(Frozen):
    """The producer had changed acceptance tests; they were put back before its work was committed."""

    kind: Literal["acceptance_restored"] = "acceptance_restored"
    artifact: ArtifactId
    paths: tuple[str, ...]

    @property
    def subject(self) -> ArtifactId:
        return self.artifact


class Sized(Frozen):
    """The decomposition team's verdict: the artifact fits one branch, or was split into children."""

    kind: Literal["sized"] = "sized"
    artifact: ArtifactId
    fits: bool  # False: the artifact is now an umbrella over its children and does no work itself
    children: tuple[ArtifactId, ...] = ()
    decision: DecisionId | None = None

    @property
    def subject(self) -> ArtifactId:
        return self.artifact


class Resolution(StrEnum):
    RETRY = "retry"  # clear the escalation: teams pick the artifact up again
    ACCEPT = "accept"  # a person approves its current state at its gate (judgement, not hard checks)
    DROP = "drop"  # retire it; merged code stays (reverting is a separate, explicit action)


class EscalationResolved(Frozen):
    kind: Literal["escalation_resolved"] = "escalation_resolved"
    artifact: ArtifactId
    resolution: Resolution
    note: str = ""

    @property
    def subject(self) -> ArtifactId:
        return self.artifact


class Paused(Frozen):
    """New work stops project-wide (the supervisor saw drift); analyzers and gates carry on."""

    kind: Literal["paused"] = "paused"
    reason: str

    @property
    def subject(self) -> None:
        return None


class Resumed(Frozen):
    kind: Literal["resumed"] = "resumed"
    reason: str

    @property
    def subject(self) -> None:
        return None


class Usage(Frozen):
    """Tokens one model call (or a run of them) used.

    input_tokens counts every input token; the cache counts are the parts of it read from, or written
    to, the provider's prompt cache, which are priced differently.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    model_calls: int = 0  # how many calls these tokens came from
    largest_input: int = 0  # the input of the largest single call: how far a conversation grew

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_read_tokens=self.cache_read_tokens + other.cache_read_tokens,
            cache_write_tokens=self.cache_write_tokens + other.cache_write_tokens,
            model_calls=self.model_calls + other.model_calls,
            largest_input=max(self.largest_input, other.largest_input),
        )


class Spent(Frozen):
    """Money spent on models: one event per team action or Jev decision (Phase 6)."""

    kind: Literal["spent"] = "spent"
    by: str  # the team, or "jev:<decision point>"
    model: str
    input_tokens: int
    output_tokens: int
    usd: float
    priced: bool  # False when the model has no price in the config, so usd is 0
    cache_read_tokens: int = 0  # parts of input_tokens read from the provider's prompt cache
    cache_write_tokens: int = 0
    model_calls: int = 0  # 0 on boards from before it was recorded
    largest_input: int = 0  # the input tokens of the largest single call

    @property
    def subject(self) -> None:
        return None


class Promoted(Frozen):
    kind: Literal["promoted"] = "promoted"
    artifact: ArtifactId
    from_level: Level
    to_level: Level
    gate_decision: DecisionId | None  # link to the decision log; None for a gate without a judge
    # The commit the target level points to afterwards: the merge commit when the artifact first joins a
    # shared level (then merge^1..merge is exactly what it brought in), the new tip on later promotions.
    commit: str | None = None

    @property
    def subject(self) -> ArtifactId:
        return self.artifact


class CallKind(StrEnum):
    """What a model call does, whichever team makes it: what a model's routing record is kept per."""

    PRODUCE = "produce"  # executors, writers, produce probes
    CRITIQUE = "critique"  # analyzers, critique probes
    PLAN = "plan"  # decomposers, outliners
    ACCEPT = "accept"  # acceptance writers, accept probes


class Profiled(Frozen):
    """What Jev said about an artifact's task, for routing: asked once, read by every routed team."""

    kind: Literal["profiled"] = "profiled"
    artifact: ArtifactId
    difficulty: float  # 1 - p(simple)
    attributes: dict[str, float]  # Jev's p(yes) per CallProfile question
    decision: DecisionId
    active: bool  # whether Jev acts at the profile point; in shadow, routers keep the cheapest tier

    @property
    def subject(self) -> ArtifactId:
        return self.artifact


class Routed(Frozen):
    """Which model a team started an artifact on, and why (Phase 8, routing).

    Recorded once per artifact and team, before its first model call; an executor's later attempts step up
    the tiers from there.
    """

    kind: Literal["routed"] = "routed"
    artifact: ArtifactId
    team: TeamName
    call_kind: CallKind = CallKind.PRODUCE  # boards from before every role was routed: only executors
    model: str  # the name in the project's `models`
    model_id: str  # the model itself, "<provider>:<model>", which the routing ledger is keyed by
    difficulty: float | None  # 1 - p(simple) from Jev; None when Jev couldn't answer
    decision: DecisionId | None
    acted: bool  # whether the route was chosen from the ledger (active) or the cheapest tier was kept
    attributes: dict[str, float] = Field(
        default_factory=dict[str, float]
    )  # Jev's p(yes) per CallProfile question

    @property
    def subject(self) -> ArtifactId:
        return self.artifact


class AttemptFailed(Frozen):
    """An executor attempt that failed with nothing for the gate to judge: for routing, a model failure.

    Thrown away before the next attempt, or the last attempt, escalated without a commit of its model's work.
    """

    kind: Literal["attempt_failed"] = "attempt_failed"
    artifact: ArtifactId
    team: TeamName
    model: str | None  # the name in the project's `models`
    reason: str
    changed_nothing: bool = (
        False  # it said it was done without changing a file: told so, it gets one more try
    )

    @property
    def subject(self) -> ArtifactId:
        return self.artifact


class ToolCall(Frozen):
    """One tool call a model made: which tool, and how much text went in and came back."""

    tool: str
    input_chars: int
    output_chars: int


class ToolsUsed(Frozen):
    """The tool calls of one model run on an artifact, in order: where a producer's tokens went."""

    kind: Literal["tools_used"] = "tools_used"
    artifact: ArtifactId
    team: TeamName
    model: str | None
    calls: tuple[ToolCall, ...]

    @property
    def subject(self) -> ArtifactId:
        return self.artifact


class OutcomeRecorded(Frozen):
    """A routed call's outcome, settled by later events and written to the routing ledger (once)."""

    kind: Literal["outcome_recorded"] = "outcome_recorded"
    artifact: ArtifactId
    team: TeamName
    call_kind: CallKind
    model_id: str
    success: bool
    settles: int  # the position, in the artifact's history, of the event whose outcome this is

    @property
    def subject(self) -> ArtifactId:
        return self.artifact


class PromotionStarted(Frozen):
    """Written just before a level's branch moves (CODEX_AUDIT.md F03).

    Git and the board can't change in one transaction. The promoter records what it is about to do, moves
    the branch, then records the promotions together with a `PromotionSettled`. If it stops in between, the
    next step settles the open record from where the branch actually is.
    """

    kind: Literal["promotion_started"] = "promotion_started"
    branch: Level
    previous: str  # the branch's tip before the move
    target: str  # the commit it moves to
    promotions: tuple[Promoted, ...]

    @property
    def subject(self) -> None:
        return None


class PromotionSettled(Frozen):
    kind: Literal["promotion_settled"] = "promotion_settled"
    branch: Level
    moved: bool  # False: the branch never moved, so the promotions did not happen

    @property
    def subject(self) -> None:
        return None


class Rejected(Frozen):
    kind: Literal["rejected"] = "rejected"
    artifact: ArtifactId
    level: Level
    findings: tuple[Finding, ...] = Field(min_length=1)
    gate_decision: DecisionId | None  # None when hard checks rejected it before any judgement

    @property
    def subject(self) -> ArtifactId:
        return self.artifact


class Escalated(Frozen):
    kind: Literal["escalated"] = "escalated"
    artifact: ArtifactId | None
    reason: str
    approval_for: Level | None = None  # set when a human gate asks to promote the artifact to this level
    # False: a notice about one team's trouble (a reviewer that couldn't finish). It waits for a person like
    # any escalation, but doesn't park the artifact, so the other teams carry on with it.
    parks: bool = True

    @property
    def subject(self) -> ArtifactId | None:
        return self.artifact


type Event = Annotated[
    ArtifactCreated
    | Claimed
    | Released
    | CommitPushed
    | FindingAdded
    | FindingResolved
    | SignedOff
    | Sized
    | AcceptanceWritten
    | AcceptanceRestored
    | Paused
    | Resumed
    | Spent
    | Promoted
    | Profiled
    | Routed
    | AttemptFailed
    | OutcomeRecorded
    | ToolsUsed
    | PromotionStarted
    | PromotionSettled
    | Rejected
    | Escalated
    | EscalationResolved,
    Field(discriminator="kind"),
]

EventAdapter: TypeAdapter[Event] = TypeAdapter(Event)


class Envelope(Frozen):
    """An event at its position in a project's log."""

    seq: int = Field(ge=1)  # position in the log
    at: datetime
    project: ProjectId
    event: Event


class ArtifactContext(Frozen):
    """What a team, a gate or a model sees about one artifact (proposal §8.3)."""

    north_star: str
    artifact: Artifact
    history: tuple[Event, ...]  # this artifact's events, oldest first
    open_findings: tuple[Finding, ...]
    diff_summary: str  # git diff --stat of the artifact's own change
    version: int  # BoardViews.version: changes exactly when the artifact does
    signed_off_by: tuple[TeamName, ...]  # analyzer teams that signed off this version
    merge_commit: str | None  # where it joined the first shared level; None while it is on its own branch
    depth: int  # how many decompositions above it: 0 for a task a person created
    waits_for: ArtifactId | None = None  # an earlier sibling of a split, still to be merged first
    # The last child of a split closes it: the parent it closes, and the parent's acceptance tests once
    # written, which become the last child's own, so the whole is checked against the original task.
    closes: ArtifactId | None = None
    parent_acceptance: AcceptanceWritten | None = None


class Instruction(Frozen):
    """What a producer is asked to do: the task, and the findings it must fix (proposal §13.2)."""

    goal: str  # the artifact's task
    must_fix: tuple[Finding, ...]  # open findings, e.g. from the last rejection
    constraints: tuple[str, ...]  # from the project configuration
