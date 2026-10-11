"""The ratchet: a test suite that may only move forward (ROADMAP.md, stage 1).

A project built against a large external test suite (a spec's conformance examples) starts with most tests
failing, so "every test passes" can't be a gate. The ratchet asks instead: compared with the level the work
forked from, does at least one more of its failing tests pass now, and does every test that passed there still
pass? New tests the producer wrote don't count as progress, and a test that disappeared counts as broken.

With `protected` paths (the independent acceptance tests, which the producer can't change), only those are
held to "passed there, passes now": the producer's own tests are part of its work, so it may rewrite or
delete them (a refactor that removes old code must remove the tests of that code), but none may fail.
The CommonMark switch-over's last step reached 99% and was rejected for 7 such transitional tests
("lists still use the old code"), which it could neither let fail nor delete (2026-10-11).

It runs pytest with `-rA --tb=no` and reads the one-line outcome pytest prints per test. The level's outcomes
are run once per commit on a plain copy of it (`git archive`), in the same sandbox, and kept
(`checks.RatchetCheck`).
"""

import re
from typing import ClassVar, Self

from pydantic import BaseModel, ConfigDict

from agentswarm.core.domain import Level


class Outcomes(BaseModel):
    """Which tests passed and which didn't, by pytest node id."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    passed: frozenset[str]
    failed: frozenset[str]  # failures and errors

    LINE: ClassVar[re.Pattern[str]] = re.compile(r"^(PASSED|FAILED|ERROR|XPASS) (\S+)")

    @classmethod
    def parse(cls, output: str) -> Self:
        passed: set[str] = set()
        failed: set[str] = set()
        for line in output.splitlines():
            match = cls.LINE.match(line)
            if match is None:
                continue
            outcome, node = match.groups()
            (passed if outcome in ("PASSED", "XPASS") else failed).add(node)
        return cls(passed=frozenset(passed), failed=frozenset(failed - passed))


class Progress(BaseModel):
    """A change's outcomes against the level's: what it fixed and what it broke."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    base: Outcomes
    now: Outcomes
    protected: tuple[str, ...] = ()  # path prefixes held to the ratchet; none: every test is

    def guarded(self, test: str) -> bool:
        return not self.protected or test.startswith(self.protected)

    @property
    def fixed(self) -> frozenset[str]:
        return self.base.failed & self.now.passed

    @property
    def broken(self) -> frozenset[str]:
        return frozenset(t for t in self.base.passed - self.now.passed if self.guarded(t))

    @property
    def added_failing(self) -> frozenset[str]:
        """The producer's own tests that fail: those it added, and with `protected`, any it may change."""
        if not self.protected:
            return self.now.failed - (self.base.passed | self.base.failed)
        return frozenset(t for t in self.now.failed if not self.guarded(t))

    @property
    def moves_forward(self) -> bool:
        return bool(self.fixed) and not self.broken and not self.added_failing

    def acceptable(self, progress: bool) -> bool:
        """Moves forward; or, without progress asked (a refactor), breaks nothing and adds no failing test."""
        return self.moves_forward if progress else not (self.broken or self.added_failing)

    def summary(self, level: Level, base: str, shown: int = 20, progress: bool = True) -> str:
        theirs = self.base.passed | self.base.failed
        lines = [
            f"ratchet against {level} ({base[:10]}): {len(self.now.passed & theirs)}"
            f"/{len(theirs)} of its tests pass now, {len(self.base.passed)} passed there; "
            f"{len(self.fixed)} newly passing, {len(self.broken)} broken"
        ]
        if self.broken:
            lines.append("BROKEN (passed before, not now): " + ", ".join(sorted(self.broken)[:shown]))
        if self.added_failing:
            own = "the producer's own tests: fix or delete them" if self.protected else "added by this work"
            lines.append(f"TESTS FAILING ({own}): " + ", ".join(sorted(self.added_failing)[:shown]))
        if not self.fixed and progress:
            lines.append("NO PROGRESS: none of the level's failing tests passes yet")
        still = sorted(self.base.failed - self.now.passed)
        if still:
            lines.append(f"still failing ({len(still)}), first: " + ", ".join(still[:shown]))
        return "\n".join(lines)
