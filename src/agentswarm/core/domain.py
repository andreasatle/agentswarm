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
    TOO_LARGE = "too_large"  # should be decomposed
    EDGE_CASE = "edge_case"  # breaks on unusual input or misuse (red team)


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

    @property
    def subject(self) -> ArtifactId:
        return self.artifact


class CommitPushed(Frozen):
    kind: Literal["commit_pushed"] = "commit_pushed"
    artifact: ArtifactId
    commit: str

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


class Promoted(Frozen):
    kind: Literal["promoted"] = "promoted"
    artifact: ArtifactId
    from_level: Level
    to_level: Level
    gate_decision: DecisionId | None  # link to the decision log; None for a gate without a judge
    # The commit the target level points to afterwards: the merge commit when the artifact first joins a
    # shared level (then merge^1...merge^2 is exactly its change), the new tip on later promotions.
    commit: str | None = None

    @property
    def subject(self) -> ArtifactId:
        return self.artifact


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
    | Paused
    | Resumed
    | Promoted
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


class Instruction(Frozen):
    """What a producer is asked to do: the task, and the findings it must fix (proposal §13.2)."""

    goal: str  # the artifact's task
    must_fix: tuple[Finding, ...]  # open findings, e.g. from the last rejection
    constraints: tuple[str, ...]  # from the project configuration
