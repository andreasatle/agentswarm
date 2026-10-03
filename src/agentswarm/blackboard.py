"""A project's blackboard: the event log plus views derived from it (proposal §8.1)."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import assert_never

from agentswarm.domain import (
    Artifact,
    ArtifactCreated,
    ArtifactId,
    Claim,
    Claimed,
    CommitPushed,
    Envelope,
    Escalated,
    Event,
    Finding,
    FindingAdded,
    Level,
    Promoted,
    Rejected,
    Released,
)
from agentswarm.eventlog import EventLog


@dataclass
class BoardViews:
    """Every view of the board, as a fold over the log. Two folds over the same log compare equal.

    Rules that the fold encodes:
    - A promotion moves the artifact to its new level and closes its open findings.
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
            case Claimed():
                self._claims[event.claim.artifact] = event.claim
            case Released():
                claim = self._claims.get(event.artifact)
                if claim is not None and claim.team == event.team:
                    del self._claims[event.artifact]
            case CommitPushed():
                self._last_commit[event.artifact] = event.commit
                self._unjudged[event.artifact] = event.commit
            case FindingAdded():
                self._findings.setdefault(event.finding.artifact, []).append(event.finding)
            case Promoted():
                self._artifacts[event.artifact] = self._artifacts[event.artifact].model_copy(
                    update={"level": event.to_level}
                )
                self._findings.pop(event.artifact, None)
                self._unjudged.pop(event.artifact, None)
            case Rejected():
                self._findings[event.artifact] = list(event.findings)
                self._unjudged.pop(event.artifact, None)
            case Escalated():
                if event.artifact is not None:
                    self._unjudged.pop(event.artifact, None)
            case _:
                assert_never(event)

    def artifact(self, id: ArtifactId) -> Artifact:
        return self._artifacts[id]

    def artifacts(self) -> list[Artifact]:
        return list(self._artifacts.values())

    def artifacts_at(self, level: Level) -> list[Artifact]:
        return [a for a in self._artifacts.values() if a.level == level]

    def open_findings(self, id: ArtifactId) -> list[Finding]:
        return [f for f in self._findings.get(id, []) if f.resolved_by is None]

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

    def version(self, id: ArtifactId) -> int:
        """The seq of the artifact's last event other than claims and releases.

        It changes exactly when there is something new to react to, so teams key on it to stay idempotent.
        """
        for envelope in reversed(self._histories.get(id, [])):
            if not isinstance(envelope.event, Claimed | Released):
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
