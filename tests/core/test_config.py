from typing import Any

import pytest
from pydantic import ValidationError

from agentswarm.core.config import Domain, LevelLadder, Presets, ProjectConfig
from agentswarm.core.domain import FindingKind, Level
from tests.support.paths import RepoPaths

EXAMPLE = RepoPaths.EXAMPLES / "retry-client.yaml"


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
            {"teams": [{"use": "executor", "watches": "raw", "model": "missing"}]},
            {"teams": [{"use": "executor", "watches": "raw", "checks": ["missing"]}]},
            {"models": {"m": {"model": "no-provider-prefix"}}},
            {"teams": [{"use": "executor", "watches": "raw"}, {"use": "executor", "watches": "raw"}]},
            {"gates": [{"from": "raw", "to": "experimental", "requires": ["executor"]}]},
            {
                "levels": ["raw", "experimental", "candidate"],
                "gates": [{"from": "experimental", "to": "candidate", "requires": ["nobody"]}],
            },
            {
                "levels": ["raw", "experimental", "candidate"],
                "gates": [{"from": "experimental", "to": "candidate", "judge": "ChangeReadiness"}],
            },
        ],
        ids=[
            "unknown-team-level",
            "unknown-gate-level",
            "undefined-check",
            "non-adjacent-gate",
            "extra-key",
            "undefined-team-model",
            "undefined-team-check",
            "model-without-provider",
            "duplicate-team",
            "sign-offs-at-the-lowest-gate",
            "required-team-not-watching",
            "judge-above-the-lowest-level",
        ],
    )
    def test_rejects_inconsistent_configs(self, overrides: dict[str, Any]) -> None:
        with pytest.raises(ValidationError):
            ProjectConfig.model_validate(Configs.minimal(**overrides))


class TestPresets:
    def test_the_software_preset_fills_what_a_project_leaves_out(self) -> None:
        config = ProjectConfig.model_validate(
            {
                "project": "p",
                "domain": "software",
                "north_star": "n",
                "models": {"coder": {"model": "ollama:m"}},
            }
        )
        assert config.levels.levels == ("raw", "experimental", "candidate", "production")
        assert [t.use for t in config.teams] == ["executor", "integration_tester", "red_team"]
        assert [g.requires for g in config.gates][1] == ("integration_tester", "red_team")
        assert config.gates[2].requires_human
        assert config.gates[0].judge == "ChangeReadiness"

    def test_a_project_key_replaces_the_preset_key_whole(self) -> None:
        config = ProjectConfig.model_validate(Configs.minimal())
        assert config.levels.levels == ("raw", "experimental")  # not merged with the preset's four
        assert [g.judge for g in config.gates] == [None]

    def test_a_domain_without_a_preset_gets_nothing(self) -> None:
        assert Presets.for_domain("document") == {}

    def test_preset_teams_need_the_project_to_define_their_model(self) -> None:
        with pytest.raises(ValidationError, match="undefined model 'coder'"):
            ProjectConfig.model_validate({"project": "p", "domain": "software", "north_star": "n"})
