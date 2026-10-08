from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agentswarm.control.limits import SharedSpend
from agentswarm.core.clock import FakeClock
from agentswarm.core.config import ModelConfig, SpendLimits


class TestSpendLimits:
    def test_every_run_counts_against_the_machine_and_its_account_until_the_next_day(
        self, tmp_path: Path
    ) -> None:
        clock = FakeClock(datetime(2026, 10, 8, 12, tzinfo=UTC))
        limits = SpendLimits(
            ledger=tmp_path / "spend.sqlite", usd_per_day=10.0, per_account={"anthropic": 3.0}
        )
        shared = SharedSpend(limits, clock)
        shared.record("commonmark", "anthropic", 2.5)
        assert shared.over({"anthropic"}) is None
        assert shared.over({"anthropic"}, reserve=1.0) == "anthropic limit: $2.50 of $3.00 spent today"
        other = SharedSpend(limits, clock)  # another run, the same ledger
        other.record("inventory", "openrouter.ai", 7.0)
        assert shared.over({"openrouter.ai"}) is None
        assert (
            shared.over({"openrouter.ai"}, reserve=1.0) == "machine-wide limit: $9.50 of $10.00 spent today"
        )
        clock.advance(timedelta(days=1))
        assert shared.over({"anthropic"}, reserve=1.0) is None

    def test_read_from_the_machines_file_or_switched_off(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        file = tmp_path / "limits.yaml"
        file.write_text("usd_per_day: 10\nper_account: {anthropic: 3}\nledger: ~/x/spend.sqlite\n")
        monkeypatch.setenv(SpendLimits.VARIABLE, str(file))
        limits = SpendLimits.load()
        assert limits is not None and limits.per_account == {"anthropic": 3.0}
        assert limits.ledger == Path.home() / "x" / "spend.sqlite"
        monkeypatch.setenv(SpendLimits.VARIABLE, "off")
        assert SpendLimits.load() is None

    def test_the_account_is_who_bills(self) -> None:
        assert ModelConfig(model="anthropic:claude-haiku-4-5").account == "anthropic"
        router = ModelConfig(
            model="openai:qwen/qwen3-coder", base_url="https://openrouter.ai/api/v1", api_key_env="K"
        )
        assert router.account == "openrouter.ai"
