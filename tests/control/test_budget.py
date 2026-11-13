from pathlib import Path

import pytest

from agentswarm.control.budget import Budget
from agentswarm.core.domain import OverBudget
from tests.support.harness import Harness


class TestBudget:
    def test_runs_in_flight_count_until_their_spend_is_recorded(self, tmp_path: Path) -> None:
        h = Harness(tmp_path)
        budget, views = Budget(1.5, h.clock), h.board.current()
        budget.admit(views, 1.0, "first")
        with pytest.raises(OverBudget, match="spent or in flight"):
            budget.admit(views, 1.0, "second")  # parallel actions: the first may still spend $1
        budget.release("first")
        budget.admit(views, 1.0, "second")
        budget.admit(views, 1.2, "second")  # a run's later call replaces its own worst case, not adds to it
