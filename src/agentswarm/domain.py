"""Domain model (proposal §7): artifacts, findings, claims and the events that change the blackboard."""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal, NewType

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

ArtifactId = NewType("ArtifactId", str)  # e.g. "a-0042"
TeamName = NewType("TeamName", str)
ProjectId = NewType("ProjectId", str)
DecisionId = NewType("DecisionId", str)

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
    parent: ArtifactId | None = None  # set when created by decomposition
    created_by: TeamName


class FindingKind(StrEnum):
    TEST_FAILURE = "test_failure"
    BUILD_FAILURE = "build_failure"
    OUT_OF_SCOPE = "out_of_scope"
    INCOMPLETE = "incomplete"
    SECURITY = "security"
    INCONSISTENT = "inconsistent"
    TOO_LARGE = "too_large"  # should be decomposed


class Severity(StrEnum):
    INFO = "info"
    MINOR = "minor"
    MAJOR = "major"
    BLOCKER = "blocker"


class Finding(Frozen):
    """Typed feedback. A rejected gate always produces at least one."""

    artifact: ArtifactId
    level: Level
    team: TeamName
    kind: FindingKind
    severity: Severity
    detail: str  # where and why; see proposal §11.6 for where it comes from
    resolved_by: ArtifactId | None = None


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


class Promoted(Frozen):
    kind: Literal["promoted"] = "promoted"
    artifact: ArtifactId
    from_level: Level
    to_level: Level
    gate_decision: DecisionId  # link to the decision log

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
    ArtifactCreated | Claimed | Released | CommitPushed | FindingAdded | Promoted | Rejected | Escalated,
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
    diff_summary: str  # e.g. git diff --stat against the level the artifact was forked from
