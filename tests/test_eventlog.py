from collections.abc import Iterator
from pathlib import Path

import pytest

from agentswarm.clock import FakeClock
from agentswarm.eventlog import ConcurrencyConflict, EventLog, InMemoryEventLog, SqliteEventLog
from tests.samples import Samples


@pytest.fixture(params=["memory", "sqlite"])
def log(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[EventLog]:
    """Every behaviour is checked against both implementations."""
    clock = FakeClock(Samples.AT)
    if request.param == "memory":
        yield InMemoryEventLog(Samples.PROJECT, clock)
    else:
        with SqliteEventLog(tmp_path / "events.sqlite", Samples.PROJECT, clock) as sqlite_log:
            yield sqlite_log


class TestEventLog:
    def test_starts_empty(self, log: EventLog) -> None:
        assert log.head() == 0
        assert log.read() == []

    def test_append_numbers_events_without_gaps(self, log: EventLog) -> None:
        log.append([Samples.created()])
        envelopes = log.append([Samples.commit(), Samples.commit(sha="beef")])
        assert [e.seq for e in envelopes] == [2, 3]
        assert log.head() == 3
        assert [e.seq for e in log.read()] == [1, 2, 3]

    def test_read_after_returns_only_newer_events(self, log: EventLog) -> None:
        log.append([Samples.created(), Samples.commit()])
        assert [e.event for e in log.read(after=1)] == [Samples.commit()]

    def test_envelopes_carry_project_and_time(self, log: EventLog) -> None:
        (envelope,) = log.append([Samples.created()])
        assert envelope.project == Samples.PROJECT
        assert envelope.at == Samples.AT

    def test_append_with_stale_head_conflicts_and_writes_nothing(self, log: EventLog) -> None:
        log.append([Samples.created()])
        with pytest.raises(ConcurrencyConflict):
            log.append([Samples.commit()], expected_head=0)
        assert log.head() == 1

    def test_append_with_current_head_succeeds(self, log: EventLog) -> None:
        log.append([Samples.created()])
        log.append([Samples.commit()], expected_head=1)
        assert log.head() == 2


class TestSqliteEventLog:
    def test_survives_reopening(self, tmp_path: Path) -> None:
        path = tmp_path / "events.sqlite"
        clock = FakeClock(Samples.AT)
        with SqliteEventLog(path, Samples.PROJECT, clock) as first:
            first.append([Samples.created(), Samples.commit()])
        with SqliteEventLog(path, Samples.PROJECT, clock) as second:
            assert [e.event for e in second.read()] == [Samples.created(), Samples.commit()]

    def test_two_connections_cannot_both_append_at_the_same_head(self, tmp_path: Path) -> None:
        path = tmp_path / "events.sqlite"
        clock = FakeClock(Samples.AT)
        with (
            SqliteEventLog(path, Samples.PROJECT, clock) as a,
            SqliteEventLog(path, Samples.PROJECT, clock) as b,
        ):
            a.append([Samples.created()], expected_head=0)
            with pytest.raises(ConcurrencyConflict):
                b.append([Samples.created(Samples.B)], expected_head=0)
            assert b.head() == 1
