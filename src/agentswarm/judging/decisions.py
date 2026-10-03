"""The decision log: every judgement a gate asks for, and what turned out to be true (proposal §15).

A Decision records exactly what the judge was shown, what it answered, and how the policy ruled. An
Outcome records the ground truth for **one question** of one decision, and where that truth came from.
A question can collect outcomes from several sources; every one is kept, and readers use the strongest.
"""

import hashlib
import sqlite3
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol, Self, cast

from pydantic import BaseModel, ConfigDict

from agentswarm.core.config import JevMode
from agentswarm.core.domain import ArtifactId, DecisionId
from agentswarm.judging.judges import JudgeState
from agentswarm.judging.policy import Ruling


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class DecisionDraft(Frozen):
    """A decision before it is stored and given an id."""

    point: str  # the decision point, e.g. "gate:raw->experimental"
    artifact: ArtifactId
    commit: str  # the commit that was judged
    base: str  # the commit it was compared with: where it forked from the target level, at judging time
    mode: JevMode  # shadow: logged only; active: the gate acted on the ruling
    judge: str  # the judge spec's name, e.g. "ChangeReadiness"
    judge_version: str = ""  # Judge.version when asked; "" in logs from before versions existed
    state: JudgeState  # exactly what the decider was shown
    answers: dict[str, Any]  # the decider's answers as JSON; spans judge specs, so generic (§15.1)
    ruling: Ruling
    model: str  # the decider version that answered
    at: datetime

    @property
    def state_hash(self) -> str:
        """Identifies the exact input, so repeated judgements of the same state can be grouped."""
        return hashlib.sha256(self.state.model_dump_json().encode()).hexdigest()

    def p_yes(self, field: str) -> float:
        """The probability of yes the judge gave for one question."""
        for question in self.ruling.questions:
            if question.field == field:
                return question.p_yes
        raise KeyError(f"{self.judge} has no question {field!r}")


class Decision(DecisionDraft):
    id: DecisionId


class OutcomeSource(StrEnum):
    """Where a ground truth came from. See docs/design/phase-2-jev-shadow.md, "Outcomes"."""

    HUMAN = "human"  # a person labelled it
    PLANTED = "planted"  # a fault was planted on purpose, so the truth is known by construction
    HIDDEN_TESTS = "hidden_tests"  # acceptance tests the executor never saw
    MUTATION = "mutation"  # mutation testing of the change's own tests
    TEAM_RESULT = "team_result"  # a relevance filter's truth: did the team's work find anything?

    @property
    def strength(self) -> int:
        """Higher wins when one question has outcomes from several sources."""
        match self:
            case OutcomeSource.HUMAN:
                return 3
            case OutcomeSource.PLANTED:
                return 2
            case OutcomeSource.HIDDEN_TESTS | OutcomeSource.MUTATION | OutcomeSource.TEAM_RESULT:
                return 1


class Outcome(Frozen):
    decision: DecisionId
    field: str  # the question, e.g. "complete"
    truth: bool  # the correct answer (questions are phrased so yes is good)
    source: OutcomeSource
    detail: str = ""  # e.g. which hidden tests failed
    at: datetime


class DecisionStore(Protocol):
    def record(self, draft: DecisionDraft) -> Decision:
        """Store a decision and give it an id."""
        ...

    def all(self) -> list[Decision]:
        """Every decision, oldest first."""
        ...

    def add_outcome(self, outcome: Outcome) -> None:
        """Attach a ground truth to one question of a stored decision."""
        ...

    def outcomes(self, decision: DecisionId) -> list[Outcome]:
        """Every outcome recorded for a decision, oldest first."""
        ...


class Outcomes:
    """Rules shared by every store."""

    @staticmethod
    def check(decision: Decision, outcome: Outcome) -> None:
        if outcome.field not in decision.answers:
            raise ValueError(f"decision {decision.id} ({decision.judge}) has no question {outcome.field!r}")

    @staticmethod
    def strongest(outcomes: list[Outcome], field: str) -> Outcome | None:
        """The outcome to trust for one question: the strongest source, then the latest."""
        candidates = [o for o in outcomes if o.field == field]
        return max(candidates, key=lambda o: (o.source.strength, o.at), default=None)


class InMemoryDecisions:
    """Keeps decisions in lists. For tests."""

    def __init__(self) -> None:
        self._decisions: list[Decision] = []
        self._outcomes: list[Outcome] = []

    def record(self, draft: DecisionDraft) -> Decision:
        decision = Decision(id=DecisionId(f"d-{len(self._decisions) + 1:04d}"), **dict(draft))
        self._decisions.append(decision)
        return decision

    def all(self) -> list[Decision]:
        return list(self._decisions)

    def add_outcome(self, outcome: Outcome) -> None:
        Outcomes.check(self._get(outcome.decision), outcome)
        self._outcomes.append(outcome)

    def outcomes(self, decision: DecisionId) -> list[Outcome]:
        return [o for o in self._outcomes if o.decision == decision]

    def _get(self, id: DecisionId) -> Decision:
        for decision in self._decisions:
            if decision.id == id:
                return decision
        raise KeyError(f"no decision {id}")


class SqliteDecisions:
    """The decision log in one SQLite file per project (decisions.sqlite next to the blackboard)."""

    _SCHEMA = (
        """
        CREATE TABLE IF NOT EXISTS decisions (
            seq        INTEGER PRIMARY KEY,
            id         TEXT NOT NULL UNIQUE,
            point      TEXT NOT NULL,
            artifact   TEXT NOT NULL,
            mode       TEXT NOT NULL,
            verdict    TEXT NOT NULL,
            state_hash TEXT NOT NULL,
            payload    TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS outcomes (
            seq      INTEGER PRIMARY KEY,
            decision TEXT NOT NULL REFERENCES decisions (id),
            field    TEXT NOT NULL,
            source   TEXT NOT NULL,
            payload  TEXT NOT NULL
        )
        """,
        "CREATE INDEX IF NOT EXISTS outcomes_by_decision ON outcomes (decision)",
    )

    def __init__(self, path: Path) -> None:
        # check_same_thread=False: a swarm runs a project's blocking steps in worker threads. Each project's
        # steps still run one at a time, so the connection is never used by two threads at once.
        self._db = sqlite3.connect(path, isolation_level=None, timeout=10.0, check_same_thread=False)
        self._db.execute("PRAGMA foreign_keys = ON")
        for statement in self._SCHEMA:
            self._db.execute(statement)

    def record(self, draft: DecisionDraft) -> Decision:
        self._db.execute("BEGIN IMMEDIATE")  # ids are sequential, so take the write lock before choosing one
        try:
            (count,) = cast(tuple[int], self._db.execute("SELECT COUNT(*) FROM decisions").fetchone())
            decision = Decision(id=DecisionId(f"d-{count + 1:04d}"), **dict(draft))
            self._db.execute(
                "INSERT INTO decisions (id, point, artifact, mode, verdict, state_hash, payload)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    decision.id,
                    decision.point,
                    decision.artifact,
                    decision.mode,
                    decision.ruling.verdict,
                    decision.state_hash,
                    decision.model_dump_json(),
                ),
            )
        except BaseException:
            self._db.execute("ROLLBACK")
            raise
        self._db.execute("COMMIT")
        return decision

    def all(self) -> list[Decision]:
        rows = cast(
            list[tuple[str]], self._db.execute("SELECT payload FROM decisions ORDER BY seq").fetchall()
        )
        return [Decision.model_validate_json(payload) for (payload,) in rows]

    def get(self, id: DecisionId) -> Decision:
        row = cast(
            tuple[str] | None,
            self._db.execute("SELECT payload FROM decisions WHERE id = ?", (id,)).fetchone(),
        )
        if row is None:
            raise KeyError(f"no decision {id}")
        return Decision.model_validate_json(row[0])

    def add_outcome(self, outcome: Outcome) -> None:
        Outcomes.check(self.get(outcome.decision), outcome)
        self._db.execute(
            "INSERT INTO outcomes (decision, field, source, payload) VALUES (?, ?, ?, ?)",
            (outcome.decision, outcome.field, outcome.source, outcome.model_dump_json()),
        )

    def outcomes(self, decision: DecisionId) -> list[Outcome]:
        rows = cast(
            list[tuple[str]],
            self._db.execute(
                "SELECT payload FROM outcomes WHERE decision = ? ORDER BY seq", (decision,)
            ).fetchall(),
        )
        return [Outcome.model_validate_json(payload) for (payload,) in rows]

    def close(self) -> None:
        self._db.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
