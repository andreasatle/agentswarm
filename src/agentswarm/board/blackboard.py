"""A project's blackboard: the event log plus views derived from it (proposal §8.1)."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import ClassVar, assert_never

from agentswarm.board.eventlog import EventLog
from agentswarm.core.domain import (
    AcceptanceRestored,
    AcceptanceWritten,
    Artifact,
    ArtifactCreated,
    ArtifactId,
    AttemptFailed,
    Claim,
    Claimed,
    CommitPushed,
    Envelope,
    Escalated,
    EscalationResolved,
    Event,
    Finding,
    FindingAdded,
    FindingId,
    FindingResolved,
    Level,
    OutcomeRecorded,
    Paused,
    Profiled,
    Promoted,
    PromotionSettled,
    PromotionStarted,
    Rejected,
    Released,
    Repaired,
    Resolution,
    Resumed,
    Routed,
    Scored,
    SignedOff,
    Sized,
    Spent,
    TeamName,
    ToolsUsed,
    Unbuildable,
)


@dataclass
class BoardViews:
    """Every view of the board, as a fold over the log. Two folds over the same log compare equal.

    Rules that the fold encodes:
    - A promotion moves the artifact to its new level and closes the findings recorded at the level it left.
    - A rejection replaces the open findings with the gate's findings: the gate's verdict is current.
    - A commit is "unjudged" until a gate promotes, rejects or escalates the artifact.
    - A release only removes the releasing team's own claim.
    """

    seq: int = 0
    _artifacts: dict[ArtifactId, Artifact] = field(default_factory=dict[ArtifactId, Artifact])
    _findings: dict[ArtifactId, list[Finding]] = field(default_factory=dict[ArtifactId, list[Finding]])
    _claims: dict[ArtifactId, Claim] = field(default_factory=dict[ArtifactId, Claim])
    _histories: dict[ArtifactId, list[Envelope]] = field(default_factory=dict[ArtifactId, list[Envelope]])
    _last_commit: dict[ArtifactId, str] = field(default_factory=dict[ArtifactId, str])
    _unjudged: dict[ArtifactId, str] = field(default_factory=dict[ArtifactId, str])
    _fixes: dict[FindingId, ArtifactId] = field(default_factory=dict[FindingId, ArtifactId])
    _merge_commit: dict[ArtifactId, str] = field(default_factory=dict[ArtifactId, str])
    _moving: dict[Level, PromotionStarted] = field(default_factory=dict[Level, PromotionStarted])
    _escalations: dict[ArtifactId, Escalated] = field(default_factory=dict[ArtifactId, Escalated])
    _accepted: set[ArtifactId] = field(default_factory=set[ArtifactId])
    _dropped: set[ArtifactId] = field(default_factory=set[ArtifactId])
    _spent: list[tuple[datetime, float]] = field(default_factory=list[tuple[datetime, float]])
    _unpriced: int = 0  # Spent events for models without a price
    _billed: float = 0.0  # the part of the spend the providers reported charging
    _scores: list[Scored] = field(default_factory=list[Scored])
    _project_escalations: list[Escalated] = field(default_factory=list[Escalated])
    paused: bool = False  # project-wide: teams that start new work wait (see Team.pausable)
    _umbrellas: set[ArtifactId] = field(default_factory=set[ArtifactId])
    _resolved: set[FindingId] = field(default_factory=set[FindingId])
    _sign_offs: dict[ArtifactId, dict[TeamName, int]] = field(
        default_factory=dict[ArtifactId, dict[TeamName, int]]
    )

    def apply(self, envelope: Envelope) -> None:
        if envelope.seq != self.seq + 1:
            raise ValueError(f"views are at {self.seq}, cannot apply event {envelope.seq}")
        self.seq = envelope.seq
        event = envelope.event
        if event.subject is not None:
            self._histories.setdefault(event.subject, []).append(envelope)
        match event:
            case ArtifactCreated():
                self._artifacts[event.artifact.id] = event.artifact
                if event.artifact.fixes is not None:
                    self._fixes[event.artifact.fixes] = event.artifact.id
            case Claimed():
                self._claims[event.claim.artifact] = event.claim
            case Released():
                # A late release must not remove a newer claim, even by the same team (CODEX_AUDIT.md F04).
                claim = self._claims.get(event.artifact)
                if claim is not None and claim.team == event.team and event.claim in (None, claim.id):
                    del self._claims[event.artifact]
            case CommitPushed():
                self._last_commit[event.artifact] = event.commit
                self._unjudged[event.artifact] = event.commit
                self._accepted.discard(event.artifact)  # an approval covers the state a person saw
            case FindingAdded():
                self._findings.setdefault(event.finding.artifact, []).append(event.finding)
            case FindingResolved():
                self._resolved.add(event.finding)
            case SignedOff():
                self._sign_offs.setdefault(event.artifact, {})[event.team] = event.version
            case Sized():
                if not event.fits:
                    self._umbrellas.add(event.artifact)
            case Scored():
                self._scores.append(event)
            case Spent():
                self._spent.append((envelope.at, event.usd))
                self._billed += event.usd if event.billed else 0.0
                self._unpriced += not event.priced
            case Paused():
                self.paused = True
            case Resumed():
                self.paused = False
            case Promoted():
                self._artifacts[event.artifact] = self._artifacts[event.artifact].model_copy(
                    update={"level": event.to_level}
                )
                self._findings[event.artifact] = [
                    f for f in self._findings.get(event.artifact, []) if f.level != event.from_level
                ]
                self._unjudged.pop(event.artifact, None)
                self._accepted.discard(event.artifact)  # an approval is for one gate
                # The first commit is the artifact's merge into the first shared level. An umbrella never
                # merges: its commits are snapshots of the level it joined (CODEX_AUDIT.md F09).
                if event.commit is not None and event.artifact not in self._umbrellas:
                    self._merge_commit.setdefault(event.artifact, event.commit)
            case PromotionStarted():
                self._moving[event.branch] = event
            case PromotionSettled():
                self._moving.pop(event.branch, None)
            case (
                Profiled()
                | Routed()
                | AttemptFailed()
                | OutcomeRecorded()
                | ToolsUsed()
                | AcceptanceWritten()
                | AcceptanceRestored()
                | Unbuildable()
                | Repaired()
            ):
                pass  # kept in the artifact's history, where the teams read them
            case Rejected():
                self._findings[event.artifact] = list(event.findings)
                self._unjudged.pop(event.artifact, None)
            case Escalated():
                if event.artifact is not None:
                    self._unjudged.pop(event.artifact, None)
                    self._escalations[event.artifact] = event
                else:
                    self._project_escalations.append(event)
            case EscalationResolved():
                self._escalations.pop(event.artifact, None)
                match event.resolution:
                    case Resolution.ACCEPT:
                        self._accepted.add(event.artifact)
                        last = self._last_commit.get(event.artifact)
                        if last is not None:
                            self._unjudged[event.artifact] = last  # back to its gate, approved
                    case Resolution.DROP:
                        self._dropped.add(event.artifact)
                    case Resolution.RETRY:
                        pass
            case _:
                assert_never(event)

    def artifact(self, id: ArtifactId) -> Artifact:
        return self._artifacts[id]

    def artifacts(self) -> list[Artifact]:
        return list(self._artifacts.values())

    def artifacts_at(self, level: Level) -> list[Artifact]:
        """The live artifacts at a level (dropped ones are retired and left out)."""
        return [a for a in self._artifacts.values() if a.level == level and a.id not in self._dropped]

    def open_escalation(self, id: ArtifactId) -> Escalated | None:
        return self._escalations.get(id)

    def open_escalations(self) -> list[Escalated]:
        return list(self._escalations.values())

    def accepted(self, id: ArtifactId) -> bool:
        """A person approved the artifact's current state (until its next commit)."""
        return id in self._accepted

    def dropped(self, id: ArtifactId) -> bool:
        return id in self._dropped

    def active(self, id: ArtifactId) -> bool:
        """Neither waiting for a person (open escalation) nor dropped (CODEX_AUDIT.md F07).

        Every team runner, the joins and the router act only on active artifacts. The release train has two
        deliberate exceptions: a dropped artifact's merged code rides along (it can't leave the level's
        history), and a human gate's own request for approval doesn't hold the artifact it asks about.
        An escalation that doesn't park (one team's trouble, `Escalated.parks`) leaves the artifact active.
        """
        escalation = self._escalations.get(id)
        parked = escalation is not None and escalation.parks
        return not parked and id not in self._dropped

    def open_findings(self, id: ArtifactId) -> list[Finding]:
        return [f for f in self._findings.get(id, []) if f.id not in self._resolved]

    def spent(self) -> tuple[float, int]:
        """All spend so far: US dollars, and how many model runs had no price (counted as $0)."""
        return sum(usd for _, usd in self._spent), self._unpriced

    def billed(self) -> float:
        """The part of the spend the providers reported charging; the rest is estimated from prices."""
        return self._billed

    def scores(self) -> list[Scored]:
        """Every measurement of the definition of done, oldest first."""
        return list(self._scores)

    def project_escalations(self) -> list[Escalated]:
        """Escalations about the whole project, such as an exhausted budget, oldest first."""
        return list(self._project_escalations)

    def spent_since(self, since: datetime) -> float:
        """US dollars spent on models since a moment (e.g. the start of the day)."""
        return sum(usd for at, usd in self._spent if at >= since)

    def last_activity(self, id: ArtifactId) -> datetime:
        """When anything last happened to the artifact."""
        return self._histories[id][-1].at

    def is_umbrella(self, id: ArtifactId) -> bool:
        """Split into children by decomposition: it does no work itself and joins when they are done."""
        return id in self._umbrellas

    def children(self, id: ArtifactId) -> list[Artifact]:
        """The parts an artifact was split into (fix artifacts are not parts)."""
        return [a for a in self._artifacts.values() if a.parent == id and a.fixes is None]

    def waits_for(self, id: ArtifactId) -> ArtifactId | None:
        """The earlier sibling a child of a split must wait for: still active, and not yet done.

        Children run in order, each starting from what the ones before it merged: split tasks usually build on
        each other and touch the same files, and siblings worked on side by side conflicted (Phase 8). A child
        of a child also waits while its parent does, so a nested plan keeps its order across levels. A sibling
        is done once its work has left the level: merged, or, for one split again, promoted as a whole.
        """
        artifact = self._artifacts[id]
        if artifact.parent is None or artifact.fixes is not None:
            return None
        for sibling in self.children(artifact.parent):
            if sibling.id == id:
                break
            done = sibling.id in self._merge_commit or self._artifacts[sibling.id].level != artifact.level
            if self.active(sibling.id) and not done:
                return sibling.id
        return self.waits_for(artifact.parent)

    def closes(self, id: ArtifactId) -> ArtifactId | None:
        """The parent whose split this artifact closes, if it is its last child."""
        artifact = self._artifacts[id]
        if artifact.parent is None or artifact.fixes is not None:
            return None
        children = self.children(artifact.parent)
        return artifact.parent if children and children[-1].id == id else None

    def depth(self, id: ArtifactId) -> int:
        """How many decompositions above this artifact: 0 for a task a person created."""
        depth, artifact = 0, self._artifacts[id]
        while artifact.parent is not None and artifact.fixes is None:
            depth, artifact = depth + 1, self._artifacts[artifact.parent]
        return depth

    def fix_for(self, finding: FindingId) -> ArtifactId | None:
        """The artifact created to fix a finding, if there is one."""
        return self._fixes.get(finding)

    def moving(self) -> list[PromotionStarted]:
        """Branch moves that were started and not yet settled: what a crash may have left behind."""
        return list(self._moving.values())

    def merge_commit(self, id: ArtifactId) -> str | None:
        """The merge commit that brought the artifact into the first shared level, once it is there."""
        return self._merge_commit.get(id)

    def signed_off_by(self, id: ArtifactId) -> tuple[TeamName, ...]:
        """The teams whose sign-off is for the artifact's current version."""
        version = self.version(id)
        return tuple(team for team, seen in self._sign_offs.get(id, {}).items() if seen == version)

    def signed_off(self, id: ArtifactId, team: TeamName) -> bool:
        """Whether `team` has signed off the artifact's current version."""
        return self._sign_offs.get(id, {}).get(team) == self.version(id)

    def claim_on(self, id: ArtifactId, now: datetime) -> Claim | None:
        claim = self._claims.get(id)
        return claim if claim is not None and claim.expires_at > now else None

    def active_claims(self, now: datetime) -> list[Claim]:
        return [c for c in self._claims.values() if c.expires_at > now]

    def history(self, id: ArtifactId) -> list[Event]:
        return [e.event for e in self._histories.get(id, [])]

    def last_commit(self, id: ArtifactId) -> str | None:
        return self._last_commit.get(id)

    def unjudged_commit(self, id: ArtifactId) -> str | None:
        """The latest commit no gate has ruled on yet, if any."""
        return self._unjudged.get(id)

    # Events that change the artifact itself. Claims, releases, sign-offs, analyzer findings and escalations
    # don't: if they did, one analyzer signing off would make every other analyzer look again, and an
    # escalation would void the sign-offs it was raised about.
    # A retry is a change too (teams should look again); accepting or dropping leaves the artifact as it was.
    CHANGES: ClassVar[tuple[type, ...]] = (
        ArtifactCreated,
        CommitPushed,
        Promoted,
        Rejected,
        Sized,
        Unbuildable,
    )

    def version(self, id: ArtifactId) -> int:
        """The seq of the artifact's last change (see CHANGES); teams key on it to act once per change."""
        for envelope in reversed(self._histories.get(id, [])):
            event = envelope.event
            if isinstance(event, self.CHANGES) or (
                isinstance(event, EscalationResolved) and event.resolution is Resolution.RETRY
            ):
                return envelope.seq
        return 0


class Blackboard:
    """Owns a project's log and keeps its views up to date. The only way to change it is `append`."""

    def __init__(self, log: EventLog) -> None:
        self._log = log
        self._views = BoardViews()

    @property
    def log(self) -> EventLog:
        return self._log

    def current(self) -> BoardViews:
        """The views, after catching up with anything appended to the log since the last call."""
        for envelope in self._log.read(after=self._views.seq):
            self._views.apply(envelope)
        return self._views

    def append(self, events: Sequence[Event], expected_head: int | None = None) -> BoardViews:
        if events:
            self._log.append(events, expected_head)
        return self.current()

    def rebuild(self) -> BoardViews:
        """Discard the views and fold the whole log again."""
        self._views = BoardViews()
        return self.current()
