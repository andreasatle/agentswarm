from collections.abc import Callable
from datetime import timedelta
from pathlib import Path

from agentswarm.board.blackboard import Blackboard, BoardViews
from agentswarm.board.claims import ClaimRegistry
from agentswarm.board.eventlog import EventLog, InMemoryEventLog, SqliteEventLog
from agentswarm.core.clock import FakeClock
from tests.support.samples import Samples

LEASE = timedelta(minutes=10)


class Claims:
    """A board with one artifact, and a registry over it."""

    def __init__(self) -> None:
        self.clock = FakeClock(Samples.AT)
        self.board = Blackboard(InMemoryEventLog(Samples.PROJECT, self.clock))
        self.board.append([Samples.created()])
        self.registry = ClaimRegistry(self.board, self.clock)

    def version(self) -> int:
        return self.board.current().version(Samples.A)


class TestClaimRegistry:
    def test_claim_grants_a_lease(self) -> None:
        c = Claims()
        claim = c.registry.claim(Samples.A, Samples.EXECUTOR, LEASE)
        assert claim is not None
        assert claim.expires_at == Samples.AT + LEASE
        assert c.board.current().claim_on(Samples.A, c.clock.now()) == claim

    def test_second_team_is_refused_while_the_lease_holds(self) -> None:
        c = Claims()
        c.registry.claim(Samples.A, Samples.EXECUTOR, LEASE)
        assert c.registry.claim(Samples.A, Samples.TESTER, LEASE) is None

    def test_release_frees_the_artifact(self) -> None:
        c = Claims()
        claim = c.registry.claim(Samples.A, Samples.EXECUTOR, LEASE)
        assert claim is not None
        c.registry.release(claim)
        assert c.registry.claim(Samples.A, Samples.TESTER, LEASE) is not None

    def test_expired_lease_can_be_taken_over(self) -> None:
        c = Claims()
        c.registry.claim(Samples.A, Samples.EXECUTOR, LEASE)
        c.clock.advance(LEASE + timedelta(seconds=1))
        claim = c.registry.claim(Samples.A, Samples.TESTER, LEASE)
        assert claim is not None
        assert claim.team == Samples.TESTER

    def test_late_release_does_not_free_the_new_owner(self) -> None:
        c = Claims()
        first = c.registry.claim(Samples.A, Samples.EXECUTOR, LEASE)
        assert first is not None
        c.clock.advance(LEASE + timedelta(seconds=1))
        c.registry.claim(Samples.A, Samples.TESTER, LEASE)
        c.registry.release(first)
        assert c.registry.claim(Samples.A, Samples.EXECUTOR, LEASE) is None

    def test_late_release_by_the_same_team_does_not_free_its_newer_claim(self) -> None:
        c = Claims()
        old = c.registry.claim(Samples.A, Samples.EXECUTOR, timedelta(seconds=1))
        c.clock.advance(timedelta(seconds=2))
        new = c.registry.claim(Samples.A, Samples.EXECUTOR, LEASE)
        assert old is not None and new is not None and old.id != new.id
        c.registry.release(old)
        assert c.board.current().claim_on(Samples.A, c.clock.now()) == new


class TestFencedPublication:
    def test_the_holder_publishes(self) -> None:
        c = Claims()
        claim = c.registry.claim(Samples.A, Samples.EXECUTOR, LEASE)
        assert claim is not None
        assert c.registry.publish(claim, c.version(), [Samples.commit()]) is not None

    def test_an_expired_lease_nobody_took_still_publishes(self) -> None:
        c = Claims()
        claim = c.registry.claim(Samples.A, Samples.EXECUTOR, timedelta(seconds=1))
        assert claim is not None
        c.clock.advance(timedelta(minutes=30))
        assert c.registry.publish(claim, c.version(), [Samples.commit()]) is not None

    def test_a_superseded_holder_is_discarded(self) -> None:
        c = Claims()
        version = c.version()
        old = c.registry.claim(Samples.A, Samples.EXECUTOR, timedelta(seconds=1))
        c.clock.advance(timedelta(seconds=2))
        assert old is not None and c.registry.claim(Samples.A, Samples.EXECUTOR, LEASE) is not None
        assert c.registry.publish(old, version, [Samples.commit()]) is None
        assert c.version() == version

    def test_a_holder_whose_artifact_changed_meanwhile_is_discarded(self) -> None:
        c = Claims()
        version = c.version()
        old = c.registry.claim(Samples.A, Samples.EXECUTOR, timedelta(seconds=1))
        c.clock.advance(timedelta(seconds=2))
        successor = c.registry.claim(Samples.A, Samples.EXECUTOR, LEASE)
        assert old is not None and successor is not None
        assert c.registry.publish(successor, version, [Samples.commit()]) is not None
        c.registry.release(successor)
        assert c.registry.publish(old, version, [Samples.commit()]) is None


class TestRacing:
    def test_racing_registries_on_separate_connections_cannot_both_win(self, tmp_path: Path) -> None:
        clock = FakeClock(Samples.AT)
        path = tmp_path / "events.sqlite"
        with (
            SqliteEventLog(path, Samples.PROJECT, clock) as log_a,
            SqliteEventLog(path, Samples.PROJECT, clock) as log_b,
        ):
            board_a = Blackboard(log_a)
            board_a.append([Samples.created()])
            registry_a = ClaimRegistry(board_a, clock)
            # B reads the artifact as free, then A claims it before B appends.
            board_b = InterleavingBoard(log_b, lambda: registry_a.claim(Samples.A, Samples.EXECUTOR, LEASE))
            registry_b = ClaimRegistry(board_b, clock)

            assert registry_b.claim(Samples.A, Samples.TESTER, LEASE) is None
            assert board_b.interleaved
            claims = board_b.current().active_claims(clock.now())
            assert [c.team for c in claims] == [Samples.EXECUTOR]
            assert log_a.head() == 2


class InterleavingBoard(Blackboard):
    """A board that runs `competitor` once, right after its first read, to force a race."""

    def __init__(self, log: EventLog, competitor: Callable[[], object]) -> None:
        super().__init__(log)
        self._competitor = competitor
        self.interleaved = False

    def current(self) -> BoardViews:
        views = super().current()
        if not self.interleaved:
            self.interleaved = True
            self._competitor()
        return views
