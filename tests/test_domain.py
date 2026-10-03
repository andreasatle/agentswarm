from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from agentswarm.domain import (
    Artifact,
    ArtifactCreated,
    ArtifactId,
    Claim,
    Claimed,
    DecisionId,
    Envelope,
    EventAdapter,
    Finding,
    FindingKind,
    Level,
    ProjectId,
    Rejected,
    Severity,
    TeamName,
)

A = ArtifactId("a-0001")
RAW = Level("raw")
EXECUTOR = TeamName("executor")


def finding() -> Finding:
    return Finding(
        artifact=A,
        level=RAW,
        team=EXECUTOR,
        kind=FindingKind.INCOMPLETE,
        severity=Severity.MAJOR,
        detail="Does not address every part of the task",
    )


def test_event_round_trips_through_json_by_discriminator() -> None:
    created = ArtifactCreated(
        artifact=Artifact(id=A, task="add retry", branch="raw/a-0001-retry", level=RAW, created_by=EXECUTOR)
    )
    claimed = Claimed(claim=Claim(artifact=A, team=EXECUTOR, expires_at=datetime(2026, 10, 3, tzinfo=UTC)))
    rejected = Rejected(artifact=A, level=RAW, findings=(finding(),), gate_decision=DecisionId("d-1"))

    for event in (created, claimed, rejected):
        assert EventAdapter.validate_json(EventAdapter.dump_json(event)) == event


def test_envelope_round_trips() -> None:
    env = Envelope(
        seq=1,
        at=datetime(2026, 10, 3, tzinfo=UTC),
        project=ProjectId("demo"),
        event=ArtifactCreated(
            artifact=Artifact(id=A, task="t", branch="raw/a-0001-t", level=RAW, created_by=EXECUTOR)
        ),
    )
    assert Envelope.model_validate_json(env.model_dump_json()) == env


def test_rejection_requires_a_finding() -> None:
    with pytest.raises(ValidationError):
        Rejected(artifact=A, level=RAW, findings=(), gate_decision=DecisionId("d-1"))


def test_unknown_event_kind_is_rejected() -> None:
    with pytest.raises(ValidationError):
        EventAdapter.validate_python({"kind": "teleported", "artifact": A})


def test_domain_values_are_immutable_and_strict() -> None:
    f = finding()
    with pytest.raises(ValidationError):
        f.detail = "changed"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        Finding.model_validate({**f.model_dump(), "extra": 1})
