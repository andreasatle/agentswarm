
import pytest
from pydantic import ValidationError

from agentswarm.domain import (
    ArtifactCreated,
    Claim,
    Claimed,
    DecisionId,
    Envelope,
    EventAdapter,
    Finding,
    ProjectId,
    Rejected,
)
from tests.samples import Samples


class TestEvents:
    def test_round_trip_through_json_by_discriminator(self) -> None:
        events = (
            ArtifactCreated(artifact=Samples.artifact()),
            Claimed(claim=Claim(artifact=Samples.A, team=Samples.EXECUTOR, expires_at=Samples.AT)),
            Rejected(
                artifact=Samples.A,
                level=Samples.RAW,
                findings=(Samples.finding(),),
                gate_decision=DecisionId("d-1"),
            ),
        )
        for event in events:
            assert EventAdapter.validate_json(EventAdapter.dump_json(event)) == event

    def test_rejection_requires_a_finding(self) -> None:
        with pytest.raises(ValidationError):
            Rejected(artifact=Samples.A, level=Samples.RAW, findings=(), gate_decision=DecisionId("d-1"))

    def test_unknown_kind_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            EventAdapter.validate_python({"kind": "teleported", "artifact": Samples.A})


class TestEnvelope:
    def test_round_trips(self) -> None:
        env = Envelope(
            seq=1,
            at=Samples.AT,
            project=ProjectId("demo"),
            event=ArtifactCreated(artifact=Samples.artifact()),
        )
        assert Envelope.model_validate_json(env.model_dump_json()) == env


class TestFinding:
    def test_is_immutable_and_strict(self) -> None:
        f = Samples.finding()
        with pytest.raises(ValidationError):
            f.detail = "changed"  # type: ignore[misc]
        with pytest.raises(ValidationError):
            Finding.model_validate({**f.model_dump(), "extra": 1})
