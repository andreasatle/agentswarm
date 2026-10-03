"""Decisions: every judgement a gate asks for, recorded with its inputs and ruling (proposal §15.1).

This module holds the record and the interface for storing it. Step 4 adds a SQLite store and outcomes.
"""

from datetime import datetime
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict

from agentswarm.config import JevMode
from agentswarm.domain import ArtifactId, DecisionId
from agentswarm.judges import JudgeState
from agentswarm.policy import Ruling


class DecisionDraft(BaseModel):
    """A decision before it is stored and given an id."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    point: str  # the decision point, e.g. "gate:raw->experimental"
    artifact: ArtifactId
    commit: str  # the commit that was judged
    mode: JevMode  # shadow: logged only; active: the gate acted on the ruling
    judge: str  # the judge spec's name, e.g. "RawToExperimental"
    state: JudgeState  # exactly what the decider was shown
    answers: dict[str, Any]  # the decider's answers as JSON; spans judge specs, so generic (§15.1)
    ruling: Ruling
    model: str  # the decider version that answered
    at: datetime


class Decision(DecisionDraft):
    id: DecisionId


class DecisionStore(Protocol):
    def record(self, draft: DecisionDraft) -> Decision:
        """Store a decision and give it an id."""
        ...

    def all(self) -> list[Decision]:
        """Every decision, oldest first."""
        ...


class InMemoryDecisions:
    """Keeps decisions in a list. For tests, and until step 4 adds persistence."""

    def __init__(self) -> None:
        self._decisions: list[Decision] = []

    def record(self, draft: DecisionDraft) -> Decision:
        decision = Decision(id=DecisionId(f"d-{len(self._decisions) + 1:04d}"), **dict(draft))
        self._decisions.append(decision)
        return decision

    def all(self) -> list[Decision]:
        return list(self._decisions)
