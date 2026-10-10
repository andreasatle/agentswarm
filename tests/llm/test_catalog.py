from datetime import date

import pytest
import yaml

from agentswarm.llm.catalog import OpenRouterCatalog

LISTING = {
    "data": [
        {
            "id": "qwen/qwen3-coder",
            "context_length": 262144,
            "pricing": {"prompt": "0.0000003", "completion": "0.000001", "input_cache_read": "0.0000001"},
            "supported_parameters": ["tools", "temperature"],
        },
        {"id": "x/no-tools", "pricing": {"prompt": "0.000001", "completion": "0.000002"}},
        {
            "id": "y/tiered",
            "pricing": {
                "prompt": "0.000001",
                "completion": "0.000002",
                "overrides": [{"min_prompt_tokens": 200000, "prompt": "0.000002"}],
            },
            "supported_parameters": ["tools"],
        },
        {
            "id": "z/thinker",
            "pricing": {"prompt": "0.000001", "completion": "0.000002"},
            "supported_parameters": ["tools", "reasoning"],
            "top_provider": {"max_completion_tokens": 32000},
        },
    ]
}


class TestOpenRouterCatalog:
    def test_entries_carry_live_prices_the_base_url_and_the_keys_name(self) -> None:
        entries = OpenRouterCatalog(lambda: LISTING).entries(["qwen/qwen3-coder"])
        (name, config) = next(iter(entries.items()))
        assert name == "qwen3-coder" and config.model == "openai:qwen/qwen3-coder"
        assert config.base_url == OpenRouterCatalog.BASE_URL and config.api_key_env == "OPENROUTER_API_KEY"
        price = config.usd_per_million
        assert price is not None and (price.input, price.output, price.cache_read) == (0.3, 1.0, 0.1)
        text = OpenRouterCatalog.render(entries, date(2026, 10, 8))
        assert text.startswith("# OpenRouter models, prices read 2026-10-08")
        assert yaml.safe_load(text)["models"]["qwen3-coder"]["model"] == "openai:qwen/qwen3-coder"

    def test_a_model_without_a_listed_cache_price_has_no_discount(self) -> None:
        (config,) = OpenRouterCatalog(lambda: LISTING).entries(["y/tiered"]).values()
        price = config.usd_per_million
        assert price is not None and price.cache_read == price.input == 1.0

    def test_unknown_models_and_models_without_tool_calling_are_refused(self) -> None:
        catalog = OpenRouterCatalog(lambda: LISTING)
        with pytest.raises(ValueError, match="doesn't list"):
            catalog.entries(["nobody/nothing"])
        with pytest.raises(ValueError, match="no tool calling"):
            catalog.entries(["x/no-tools"])

    def test_prices_that_rise_for_long_prompts_are_flagged(self) -> None:
        catalog = OpenRouterCatalog(lambda: LISTING)
        tiered = catalog.tiered(["y/tiered", "qwen/qwen3-coder"])
        assert tiered == {"tiered": [200000]}
        text = OpenRouterCatalog.render(catalog.entries(["y/tiered"]), date(2026, 10, 8), tiered)
        assert "# tiered: a higher price applies from 200,000 prompt tokens" in text

    def test_reasoning_models_get_room_to_think_within_their_hosts_limit(self) -> None:
        catalog = OpenRouterCatalog(lambda: LISTING)
        assert catalog.entries(["qwen/qwen3-coder"])["qwen3-coder"].max_tokens == OpenRouterCatalog.MAX_TOKENS
        assert (
            catalog.entries(["z/thinker"])["thinker"].max_tokens == 32000
        )  # 65,536 wanted, the host allows less
