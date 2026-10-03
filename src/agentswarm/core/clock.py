"""Time as an injected dependency, so leases and timestamps are deterministic in tests."""

from datetime import UTC, datetime, timedelta
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    """The real wall clock, always timezone-aware UTC."""

    def now(self) -> datetime:
        return datetime.now(UTC)


class FakeClock:
    """A clock that only moves when told to."""

    def __init__(self, start: datetime = datetime(2026, 1, 1, tzinfo=UTC)) -> None:
        self._now = start

    def now(self) -> datetime:
        return self._now

    def advance(self, delta: timedelta) -> None:
        self._now += delta
