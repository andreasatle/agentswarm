from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from agentswarm.config import Domain, LevelLadder, ProjectConfig
from agentswarm.domain import FindingKind, Level

EXAMPLE = Path(__file__).parent.parent / "examples" / "retry-client.yaml"


class Configs:
    @staticmethod
    def minimal(**overrides: Any) -> dict[str, Any]:
        config: dict[str, Any] = {
            "project": "demo",
            "domain": "software",
            "north_star": "a demo",
            "levels": ["raw", "experimental"],
            "checks": {"tests": {"command": ["true"]}},
            "teams": [{"use": "executor", "watches": "raw"}],
            "gates": [{"from": "raw", "to": "experimental", "hard_checks": ["tests"]}],
        }
        return config | overrides


class TestLevelLadder:
    def test_navigation(self) -> None:
        ladder = LevelLadder(levels=(Level("raw"), Level("experimental"), Level("candidate")))
        assert ladder.lowest == "raw"
        assert ladder.base == "experimental"
        assert ladder.branch_levels == ("experimental", "candidate")
        assert ladder.next(Level("raw")) == "experimental"
        assert ladder.next(Level("candidate")) is None

    def test_rejects_duplicates_and_single_levels(self) -> None:
        with pytest.raises(ValidationError):
            LevelLadder(levels=(Level("raw"), Level("raw")))
        with pytest.raises(ValidationError):
            LevelLadder(levels=(Level("raw"),))


class TestProjectConfig:
    def test_loads_the_example(self) -> None:
        config = ProjectConfig.load(EXAMPLE)
        assert config.domain is Domain.SOFTWARE
        assert config.levels.levels[-1] == "production"
        gate = config.gate_from(Level("raw"))
        assert gate is not None
        assert gate.hard_checks == ("build", "unit_tests")
        assert gate.thresholds["complete"].pass_at == 0.9
        assert config.checks["build"].kind is FindingKind.BUILD_FAILURE

    def test_minimal_config_is_valid(self) -> None:
        ProjectConfig.model_validate(Configs.minimal())

    @pytest.mark.parametrize(
        "overrides",
        [
            {"teams": [{"use": "executor", "watches": "nowhere"}]},
            {"gates": [{"from": "raw", "to": "production"}]},
            {"gates": [{"from": "raw", "to": "experimental", "hard_checks": ["missing"]}]},
            {"levels": ["raw", "experimental"], "gates": [{"from": "experimental", "to": "raw"}]},
            {"unexpected": 1},
        ],
        ids=["unknown-team-level", "unknown-gate-level", "undefined-check", "non-adjacent-gate", "extra-key"],
    )
    def test_rejects_inconsistent_configs(self, overrides: dict[str, Any]) -> None:
        with pytest.raises(ValidationError):
            ProjectConfig.model_validate(Configs.minimal(**overrides))
