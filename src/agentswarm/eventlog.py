"""The append-only event log behind a project's blackboard (proposal §8.1)."""

import sqlite3
import threading
from abc import ABC, abstractmethod
from collections.abc import Sequence
from pathlib import Path
from typing import Self, cast

from agentswarm.clock import Clock
from agentswarm.domain import Envelope, Event, ProjectId


class ConcurrencyConflict(Exception):
    """An append expected the log to end at one position, but someone else appended first."""

    def __init__(self, expected: int, actual: int) -> None:
        super().__init__(f"expected log head {expected}, found {actual}")
        self.expected = expected
        self.actual = actual


class EventLog(ABC):
    """One project's log. Appends are atomic; sequence numbers start at 1 and have no gaps.

    `append(..., expected_head=n)` is optimistic concurrency: it succeeds only if the log still ends
    at `n`. Claims use it to guarantee that two teams can't both claim the same artifact.
    """

    def __init__(self, project: ProjectId, clock: Clock) -> None:
        self.project = project
        self._clock = clock

    @abstractmethod
    def head(self) -> int:
        """The sequence number of the last event, or 0 for an empty log."""

    @abstractmethod
    def read(self, after: int = 0) -> list[Envelope]:
        """Every envelope with seq > `after`, in order."""

    @abstractmethod
    def append(self, events: Sequence[Event], expected_head: int | None = None) -> list[Envelope]:
        """Append all events in one transaction and return their envelopes."""

    def _wrap(self, start: int, events: Sequence[Event]) -> list[Envelope]:
        at = self._clock.now()
        return [
            Envelope(seq=start + i, at=at, project=self.project, event=event)
            for i, event in enumerate(events, 1)
        ]

    @staticmethod
    def _check_head(expected: int | None, actual: int) -> None:
        if expected is not None and expected != actual:
            raise ConcurrencyConflict(expected, actual)


class InMemoryEventLog(EventLog):
    """A log that lives only as long as the process. For tests."""

    def __init__(self, project: ProjectId, clock: Clock) -> None:
        super().__init__(project, clock)
        self._envelopes: list[Envelope] = []
        self._lock = threading.Lock()

    def head(self) -> int:
        with self._lock:
            return len(self._envelopes)

    def read(self, after: int = 0) -> list[Envelope]:
        with self._lock:
            return self._envelopes[after:]

    def append(self, events: Sequence[Event], expected_head: int | None = None) -> list[Envelope]:
        with self._lock:
            self._check_head(expected_head, len(self._envelopes))
            envelopes = self._wrap(len(self._envelopes), events)
            self._envelopes.extend(envelopes)
            return envelopes


class SqliteEventLog(EventLog):
    """A log in one SQLite file per project. Several instances may share the file."""

    _SCHEMA = """
        CREATE TABLE IF NOT EXISTS events (
            seq     INTEGER PRIMARY KEY,
            at      TEXT NOT NULL,
            kind    TEXT NOT NULL,
            payload TEXT NOT NULL
        )
    """

    def __init__(self, path: Path, project: ProjectId, clock: Clock) -> None:
        super().__init__(project, clock)
        # isolation_level=None: transactions are explicit, so BEGIN IMMEDIATE takes the write lock up front.
        self._db = sqlite3.connect(path, isolation_level=None, timeout=10.0)
        self._db.execute(self._SCHEMA)

    def head(self) -> int:
        row = cast(tuple[int], self._db.execute("SELECT COALESCE(MAX(seq), 0) FROM events").fetchone())
        return row[0]

    def read(self, after: int = 0) -> list[Envelope]:
        rows = cast(
            list[tuple[str]],
            self._db.execute("SELECT payload FROM events WHERE seq > ? ORDER BY seq", (after,)).fetchall(),
        )
        return [Envelope.model_validate_json(payload) for (payload,) in rows]

    def append(self, events: Sequence[Event], expected_head: int | None = None) -> list[Envelope]:
        self._db.execute("BEGIN IMMEDIATE")
        try:
            head = self.head()
            self._check_head(expected_head, head)
            envelopes = self._wrap(head, events)
            self._db.executemany(
                "INSERT INTO events (seq, at, kind, payload) VALUES (?, ?, ?, ?)",
                [(e.seq, e.at.isoformat(), e.event.kind, e.model_dump_json()) for e in envelopes],
            )
        except BaseException:
            self._db.execute("ROLLBACK")
            raise
        self._db.execute("COMMIT")
        return envelopes

    def close(self) -> None:
        self._db.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
