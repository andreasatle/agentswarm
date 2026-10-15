from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from agentswarm.core.config import (
    Domain,
    LevelLadder,
    ModelConfig,
    ModelOverride,
    Presets,
    Price,
    ProjectConfig,
)
from agentswarm.core.domain import FindingKind, Level, Usage
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
            {"teams": [{"use": "executor", "watches": "raw", "model_tiers": ["missing"]}]},
            {"teams": [{"use": "executor", "watches": "raw", "referee": "missing", "spec": ["docs/s.md"]}]},
            {
                "models": {"m": {"model": "ollama:m"}},
                "teams": [{"use": "executor", "watches": "raw", "referee": "m"}],
            },
            {"hardening": {"model": "nope", "referee": "nope", "spec": "docs/s.md", "check": "unit_tests"}},
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
            "undefined-model-tier",
            "undefined-referee",
            "referee-without-spec",
            "hardening-undefined-model",
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
        assert Presets.for_domain("spreadsheet") == {}

    def test_the_document_preset_has_the_document_ladder_and_teams(self) -> None:
        config = ProjectConfig.model_validate(
            {
                "project": "p",
                "domain": "document",
                "north_star": "n",
                "models": {"main": {"model": "ollama:m"}},
            }
        )
        assert config.levels.levels == ("draft", "reviewed", "final", "published")
        assert [t.use for t in config.teams] == [
            "outliner",
            "writer",
            "consistency_checker",
            "fact_checker",
            "audience_reviewer",
        ]
        assert config.gates[0].judge == "SectionReadiness" and config.gates[-1].requires_human

    def test_preset_teams_need_the_project_to_define_their_model(self) -> None:
        with pytest.raises(ValidationError, match="undefined model 'coder'"):
            ProjectConfig.model_validate({"project": "p", "domain": "software", "north_star": "n"})


class TestPrice:
    def test_cached_input_costs_its_own_price_or_plain_input_without_one(self) -> None:
        usage = Usage(
            input_tokens=1_000_000, output_tokens=0, cache_read_tokens=800_000, cache_write_tokens=100_000
        )
        anthropic = Price(input=1.0, output=5.0, cache_read=0.1, cache_write=1.25)
        assert anthropic.usd_of(usage) == pytest.approx(0.1 + 0.08 + 0.125)
        assert Price(input=1.0, output=5.0).usd_of(usage) == pytest.approx(1.0)


class TestCachedPrice:
    def test_a_caching_provider_must_name_its_cached_input_price(self) -> None:
        with pytest.raises(ValidationError, match="cache_read"):
            ModelConfig(model="openai:gpt-6.1-sol", usd_per_million=Price(input=2.0, output=10.0))
        ModelConfig(model="openai:gpt-6.1-sol", usd_per_million=Price(input=2.0, output=10.0, cache_read=0.1))
        ModelConfig(model="ollama:gemma4:26b", usd_per_million=Price(input=0.0, output=0.0))  # caches nothing
        ModelConfig(model="anthropic:claude-sonnet-5-5")  # unpriced: nothing to get wrong


class TestSplitOnFailure:
    def test_needs_climbing_and_a_decomposer(self) -> None:
        def config(climb: bool, decomposer: bool) -> dict[str, Any]:
            teams: list[dict[str, Any]] = [
                {"use": "executor", "watches": "raw", "model": "m", "climb": climb, "split_on_failure": True}
            ]
            if decomposer:
                teams.append({"use": "decomposer", "watches": "raw", "model": "m"})
            return {
                "project": "p",
                "domain": "software",
                "north_star": "n",
                "levels": ["raw", "experimental"],
                "models": {"m": {"model": "ollama:m"}},
                "teams": teams,
                "gates": [],
            }

        ProjectConfig.model_validate(config(climb=True, decomposer=True))
        for climb, decomposer in [(False, True), (True, False)]:
            with pytest.raises(ValidationError, match="split_on_failure"):
                ProjectConfig.model_validate(config(climb, decomposer))


class TestModelOverride:
    def test_replaces_every_model_a_config_names(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        model = tmp_path / "haiku.yaml"
        model.write_text("model: 'anthropic:claude-haiku-4-5'\nmax_tokens: 100\n")
        config = tmp_path / "project.yaml"
        config.write_text(
            "project: p\ndomain: software\nnorth_star: n\nlevels: [raw, experimental]\n"
            "models: {small: {model: 'ollama:x'}, large: {model: 'ollama:y'}}\n"
            "teams: [{use: executor, watches: raw, model_tiers: [small, large]}]\n"
            "gates: [{from: raw, to: experimental}]\n"
        )
        monkeypatch.setenv(ModelOverride.VARIABLE, str(model))
        loaded = ProjectConfig.load(config)
        assert {name: m.model for name, m in loaded.models.items()} == {
            "small": "anthropic:claude-haiku-4-5",
            "large": "anthropic:claude-haiku-4-5",
        }
        monkeypatch.setenv(ModelOverride.VARIABLE, str(tmp_path / "missing.yaml"))
        with pytest.raises(ValueError, match="no such file"):
            ProjectConfig.load(config)
