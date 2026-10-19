"""Domain model (proposal §7): artifacts, findings, claims and the events that change the blackboard."""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal, NewType, cast

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

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


class AttackFocus(StrEnum):
    """What a red team attacks in a hardening campaign (docs/design/hardening.md)."""

    SECTION = "section"  # every rule and stated behaviour of one spec section
    BOUNDARIES = "boundaries"  # edge values the spec names: empty, limits, just inside and just outside
    REJECTION = "rejection"  # inputs the spec says are invalid and must be rejected
    MUTATION = "mutation"  # behaviours no check covers, found by planting bugs that no check notices
    # valid inputs that mix the section's constructs with each other and with the rest of the spec (Fuzz4All):
    # YAML's hidden failures were such mixes, while the red teams tested one rule at a time (2026-10-16)
    COMBINATION = "combination"


class Perspective(StrEnum):
    """What a document's reviewers look at in a review campaign (docs/design/documents.md)."""

    FACTS = "facts"  # a red team: a claim its cited source doesn't support, or one citing nothing
    COVERAGE = "coverage"  # what the brief asks for and the sources hold, missing from the document
    READER = "reader"  # what the intended reader, new to the subject, couldn't follow
    CONSISTENCY = "consistency"  # two passages that disagree
    GENERAL = "general"  # one broad reviewer: the baseline the perspectives must beat


type Focus = AttackFocus | Perspective  # what a campaign attacks a part for


class AttackFinding(Frozen):
    """A confirmed red team report, kept with the fix task made from it."""

    task: ArtifactId
    claim: str
    quotes: tuple[str, ...]  # the spec's words it rests on, each verbatim

    @model_validator(mode="before")
    @classmethod
    def one_quote(cls, data: Any) -> Any:
        """Findings recorded before a report could quote several passages had a single `quote`."""
        if isinstance(data, dict) and "quote" in data:
            fields = cast(dict[str, Any], data)
            return {**{k: v for k, v in fields.items() if k != "quote"}, "quotes": (fields["quote"],)}
        return cast(Any, data)


class RejectedReport(Frozen):
    """A red team report the referee ruled the spec doesn't back."""

    claim: str
    reason: str  # the referee's


class Attacked(Frozen):
    """One red team attack on the product during hardening: what it reported, and what the referee confirmed.

    A confirmed report becomes a fix task whose acceptance test is the red team's failing test; the campaign
    repeats rounds over every spec section and focus until a round confirms nothing new.
    """

    kind: Literal["attacked"] = "attacked"
    round: int = Field(ge=1)
    section: str  # the part's title: a spec section, or a document's section file
    focus: Focus
    commit: str  # the product's commit that was attacked
    model: str | None = None
    reported: int = Field(ge=0)  # problems the red team reported with a failing test
    confirmed: int = Field(ge=0)  # of those, confirmed by the referee against the spec
    tasks: tuple[ArtifactId, ...] = ()  # the fix tasks made from the confirmed ones
    dropped: tuple[str, ...] = ()  # why each unconfirmed report was dropped, one line each
    findings: tuple[AttackFinding, ...] = ()  # the confirmed reports, one per task
    rejected: tuple[RejectedReport, ...] = ()  # the reports the referee ruled against
    # The critic's model call failed (no credits, an outage): not an attack at all, so never a quiet one
    # (2026-10-17: 110 refused reviews ended two "quiet" rounds and the campaign)
    failed: bool = False

    @property
    def subject(self) -> None:
        return None


class Reconsidered(Frozen):
    """The referee looked again at a confirmed red team report whose fix every builder model failed: a change
    that made the red team's test pass broke protected tests the product passed. Where the spec is imprecise
    the existing tests say what it means (2026-10-15: the manual's "`a op= b` is `a |= . op b`", read
    literally, contradicted jq's own tests). Withdrawn: the task is dropped and later red teams are told."""

    kind: Literal["reconsidered"] = "reconsidered"
    artifact: ArtifactId
    claim: str
    broken: tuple[str, ...]  # the protected tests the change broke; empty: no conflict was found
    referee: str | None  # None when plain code settled it (no conflict, or the tests didn't run)
    withdrawn: bool
    reason: str

    @property
    def subject(self) -> ArtifactId:
        return self.artifact


class CheckDisputed(Frozen):
    """A builder said an acceptance test contradicts the spec, and a referee ruled on it.

    Upheld: the test is skipped from then on, with the ruling as the reason (self-supervised projects, where
    the tests are model-written and some are wrong). Rejected: the test stands. Either way it is on the board,
    so how often model-written checks are wrong is measured.
    """

    kind: Literal["check_disputed"] = "check_disputed"
    artifact: ArtifactId
    test: str  # pytest id of the disputed test: tests/acceptance/test_x.py::test_name
    quote: str  # the spec's words the builder says the test contradicts
    reason: str  # the builder's argument
    model: str | None = None  # the builder's model
    referee: str | None = None  # the referee's model; None when plain code ruled (a quote not in the spec)
    upheld: bool
    ruling: str

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
    billed_usd: float = 0.0  # what the provider says it charged, for the calls where it says (OpenRouter)
    billed_calls: int = 0

    @property
    def fully_billed(self) -> bool:
        """The provider reported what it charged for every one of these calls."""
        return self.billed_calls > 0 and self.billed_calls == self.model_calls

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_read_tokens=self.cache_read_tokens + other.cache_read_tokens,
            cache_write_tokens=self.cache_write_tokens + other.cache_write_tokens,
            model_calls=self.model_calls + other.model_calls,
            largest_input=max(self.largest_input, other.largest_input),
            billed_usd=self.billed_usd + other.billed_usd,
            billed_calls=self.billed_calls + other.billed_calls,
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
    billed: bool = False  # usd is what the provider reported charging, not a price-list estimate
    artifact: ArtifactId | None = None  # what it was spent on, when a team spent it acting on one artifact

    @property
    def subject(self) -> ArtifactId | None:
        return self.artifact  # in the artifact's history, so an attempt's cost can be read there


class Repaired(Frozen):
    """One repair round: the builder kept its work, was told what failed, and tried to fix it. Whether the
    checks passed afterwards settles it as a `repair` outcome, so repairing can be weighed against a fresh
    attempt per model (Olausson et al. 2024; docs/design/best-practice-review.md, fix 6)."""

    kind: Literal["repaired"] = "repaired"
    artifact: ArtifactId
    team: TeamName
    model: str | None
    passed: bool

    @property
    def subject(self) -> ArtifactId:
        return self.artifact


class Unbuildable(Frozen):
    """Every model of a team's cascade has failed this step: it goes back to the planner to be split, as
    ADaPT does (decompose as needed, when execution fails; docs/design/best-practice-review.md, fix 5)."""

    kind: Literal["unbuildable"] = "unbuildable"
    artifact: ArtifactId
    team: TeamName
    reason: str

    @property
    def subject(self) -> ArtifactId:
        return self.artifact


class Tally(Frozen):
    passed: int = Field(ge=0)
    total: int = Field(ge=0)

    @property
    def share(self) -> float | None:
        return self.passed / self.total if self.total else None


class OutlineEntry(Frozen):
    """One section of a document's outline: what it covers, from which sources, in how many words."""

    title: str
    covers: str
    sources: tuple[str, ...]  # source names, without folder or extension
    words: int = Field(ge=1)


class Reference(Frozen):
    """A source document as a bibliography gives it: its parts are cited by name, the reader sees this."""

    document: str  # the source name its parts share, e.g. "2301-08243v3" for 2301-08243v3-p001 ...
    authors: str  # "Assran, M., Duval, Q., Misra, I., et al."
    title: str
    year: str
    venue: str = ""  # journal, conference or "arXiv preprint 2301.08243"
    short: str  # the in-text form: "Assran et al., 2023"
    url: str = ""  # where a reader finds it, for a web source (a link in the references)


class Briefed(Frozen):
    """The briefer drafted a document's brief and outline from the goal and committed the brief on the base
    level (docs/design/documents.md, real use). Writing tasks follow, one per section, once it is approved."""

    kind: Literal["briefed"] = "briefed"
    path: str
    commit: str
    model: str | None
    outline: tuple[OutlineEntry, ...]
    references: tuple[Reference, ...] = ()  # each source document, for citations a reader can follow

    @property
    def subject(self) -> None:
        return None


class SectionAssigned(Frozen):
    """A writing task's section of the outline: which file, what it covers, from which sources. The writer
    reads these sources itself and gives the section in one answer (docs/design/documents.md)."""

    kind: Literal["section_assigned"] = "section_assigned"
    artifact: ArtifactId
    path: str  # the section's file, e.g. sections/03-the-technical-cause.md
    section: OutlineEntry

    @property
    def subject(self) -> ArtifactId:
        return self.artifact


class Specified(Frozen):
    """The specifier drafted the project's specification from its goal and committed it on the base level
    (docs/design/goals.md); the building starts after this."""

    kind: Literal["specified"] = "specified"
    path: str
    commit: str
    model: str | None
    sections: int = Field(ge=0)
    questions: tuple[str, ...]  # what the draft had to assume, asked
    answered: int = Field(ge=0)  # of those, answered (the rest kept the draft's default)
    characters: int = Field(ge=0)

    @property
    def subject(self) -> None:
        return None


class QuestionOutcome(Frozen):
    """One hidden question at one score: whether the reader answered it correctly from the document."""

    id: str
    correct: bool
    answer: str  # the reader's answer; empty when it found none (or the call failed)


class Scored(Frozen):
    """How far a level is through the project's definition of done: measured at the start and end of every
    run, so each run reports what it bought (docs/design/best-practice-review.md, fix 8)."""

    kind: Literal["scored"] = "scored"
    level: Level
    commit: str
    seen: Tally | None  # the tests the agents see; None when they see none (a guide: only hidden questions)
    held_out: Tally | None  # tests no agent has seen; None when the project has none
    spent_usd: float = Field(ge=0)  # all spend on the board when measured: the cost between two scores
    # A document's hidden questions one by one, so a revision can be traced to what it gained or lost
    questions: tuple[QuestionOutcome, ...] = ()

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
    REPAIR = "repair"  # a builder fixing its own work after the checks failed, against starting over


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
    kept: bool = False  # it passed the checks but was unfinished: kept on the branch for the next attempt

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
    | CheckDisputed
    | Attacked
    | Reconsidered
    | Specified
    | Briefed
    | SectionAssigned
    | Paused
    | Resumed
    | Spent
    | Scored
    | Unbuildable
    | Repaired
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
    # It completes a task a person or the worklist created: the task itself, or the last step of every split
    # above it. Only such a step must move a progress ratchet forward; the other steps of a split are building
    # blocks, which must break nothing (gating/checks.py, `Parts`).
    completes: bool = True


class Instruction(Frozen):
    """What a producer is asked to do: the task, and the findings it must fix (proposal §13.2)."""

    goal: str  # the artifact's task
    must_fix: tuple[Finding, ...]  # open findings, e.g. from the last rejection
    constraints: tuple[str, ...]  # from the project configuration
