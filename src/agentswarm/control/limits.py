"""Spending limits across every run on the machine (`SpendLimits`; ~/agentswarm-data/limits.yaml).

Each project has its own daily budget on its own board, so several runs at once could each stay within theirs
and still add up to too much. Every priced model call is also written to one shared SQLite ledger, and before
an action starts the budget checks the machine's total for today and the total of each account the project
bills (anthropic, openrouter.ai, ...): over a limit, no new work starts until the next UTC day.
"""

import sqlite3
from collections.abc import Iterable
from datetime import UTC, datetime

from agentswarm.core.clock import Clock
from agentswarm.core.config import SpendLimits


class SharedSpend:
    def __init__(self, limits: SpendLimits, clock: Clock) -> None:
        self._limits = limits
        self._clock = clock
        path = limits.ledger
        if str(path) != ":memory:":
            path.parent.mkdir(parents=True, exist_ok=True)
        # Several processes write it: short transactions, and wait for each other rather than fail.
        self._db = sqlite3.connect(str(path), timeout=30.0, check_same_thread=False)
        with self._db:
            self._db.execute(
                "CREATE TABLE IF NOT EXISTS spend (at TEXT, project TEXT, account TEXT, usd REAL)"
            )

    def record(self, project: str, account: str, usd: float) -> None:
        if usd <= 0:
            return
        with self._db:
            self._db.execute(
                "INSERT INTO spend VALUES (?, ?, ?, ?)",
                (self._clock.now().isoformat(), project, account, usd),
            )

    def day_start(self) -> datetime:
        now = self._clock.now().astimezone(UTC)
        return now.replace(hour=0, minute=0, second=0, microsecond=0)

    def spent_today(self, account: str | None = None) -> float:
        since = self.day_start().isoformat()
        if account is None:
            row = self._db.execute(
                "SELECT COALESCE(SUM(usd), 0) FROM spend WHERE at >= ?", (since,)
            ).fetchone()
        else:
            row = self._db.execute(
                "SELECT COALESCE(SUM(usd), 0) FROM spend WHERE at >= ? AND account = ?", (since, account)
            ).fetchone()
        return float(row[0])

    def over(self, accounts: Iterable[str], reserve: float = 0.0) -> str | None:
        """Why no new action may start (a limit would be reached), or None."""
        total = self._limits.usd_per_day
        if total is not None and self.spent_today() + reserve >= total:
            return f"machine-wide limit: ${self.spent_today():.2f} of ${total:.2f} spent today"
        for account in sorted(set(accounts)):
            limit = self._limits.per_account.get(account)
            if limit is not None and self.spent_today(account) + reserve >= limit:
                return f"{account} limit: ${self.spent_today(account):.2f} of ${limit:.2f} spent today"
        return None

    def report(self) -> str:
        """Today's spend per account, against the limits."""
        rows = self._db.execute(
            "SELECT account, SUM(usd) FROM spend WHERE at >= ? GROUP BY account ORDER BY account",
            (self.day_start().isoformat(),),
        ).fetchall()
        lines = [f"spent today (UTC), every run on this machine: ${self.spent_today():.2f}"]
        if self._limits.usd_per_day is not None:
            lines[0] += f" of ${self._limits.usd_per_day:.2f}"
        for account, usd in rows:
            limit = self._limits.per_account.get(account)
            lines.append(f"  {account:<16} ${usd:.2f}" + (f" of ${limit:.2f}" if limit is not None else ""))
        return "\n".join(lines)

    def close(self) -> None:
        self._db.close()
