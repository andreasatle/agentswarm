import sys
from pathlib import Path

import pytest

from agentswarm.core.config import CheckConfig
from agentswarm.gating.checks import CheckStatus, CommandCheck
from agentswarm.workspace.sandbox import LocalSandbox, SandboxResult


class TestCheckStatus:
    @pytest.mark.parametrize(
        ("exit_code", "codes", "status"),
        [
            (0, (1,), CheckStatus.PASSED),
            (1, (1,), CheckStatus.FAILED),
            (2, (1,), CheckStatus.ERROR),  # pytest: collection error or interrupted
            (2, None, CheckStatus.FAILED),  # no codes configured: every non-zero exit is a failure
            (None, None, CheckStatus.ERROR),  # timed out or couldn't start
        ],
    )
    def test_classifies_how_a_command_ended(
        self, exit_code: int | None, codes: tuple[int, ...] | None, status: CheckStatus
    ) -> None:
        assert CheckStatus.of(SandboxResult(exit_code=exit_code, output=""), codes) is status


class TestCommandCheck:
    def test_an_error_running_the_check_blocks_but_is_not_a_failure(self, tmp_path: Path) -> None:
        config = CheckConfig(
            command=(sys.executable, "-c", "import sys; sys.exit(4)"), failure_exit_codes=(1,)
        )
        result = CommandCheck("tests", config, LocalSandbox()).run(tmp_path)
        assert result.status is CheckStatus.ERROR
        assert not result.passed and not result.failed
