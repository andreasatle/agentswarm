"""Model entries for OpenRouter's models, with live prices (ROADMAP.md, stage 3).

OpenRouter serves hundreds of models behind one OpenAI-compatible API and one key. Prices change often and
differ widely (some outputs cost 80x their input), so they are taken from OpenRouter's public model list when
the entries are written, dated, rather than typed into configs by hand. Each entry is an ordinary `models:`
item: `openai:<id>` with OpenRouter's base URL and the key's environment variable.
"""

import json
import re
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from datetime import date
from typing import Any, ClassVar, cast

import yaml
from pydantic import BaseModel, ConfigDict

from agentswarm.core.config import ModelConfig, Price


class ListedModel(BaseModel):
    """One model as OpenRouter lists it: prices are US dollars per token, as strings."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    id: str
    context_length: int | None = None
    pricing: dict[str, object] = {}  # mostly prices as strings; "overrides" holds tiers for long prompts
    supported_parameters: tuple[str, ...] | None = None

    def per_million(self, key: str) -> float:
        value = self.pricing.get(key)
        return round(float(value) * 1_000_000, 4) if isinstance(value, str | int | float) else 0.0

    @property
    def tiers(self) -> list[int]:
        """Prompt sizes from which a higher price applies (the entry's prices are the base tier)."""
        overrides = self.pricing.get("overrides")
        if not isinstance(overrides, list):
            return []
        found: list[int] = []
        for tier in cast(list[object], overrides):
            if isinstance(tier, dict):
                start = cast(dict[str, object], tier).get("min_prompt_tokens")
                if isinstance(start, int):
                    found.append(start)
        return sorted(found)

    @property
    def tools(self) -> bool:
        return "tools" in (self.supported_parameters or ())


class OpenRouterCatalog:
    URL: ClassVar[str] = "https://openrouter.ai/api/v1/models"
    BASE_URL: ClassVar[str] = "https://openrouter.ai/api/v1"
    KEY: ClassVar[str] = "OPENROUTER_API_KEY"
    MAX_TOKENS: ClassVar[int] = 16384

    def __init__(self, fetch: Callable[[], Any] | None = None) -> None:
        self._fetch = fetch or self.download

    @classmethod
    def download(cls) -> Any:
        with urllib.request.urlopen(cls.URL, timeout=30) as response:  # public list, no key needed
            return json.load(response)

    def listed(self) -> dict[str, ListedModel]:
        rows = self._fetch()["data"]
        return {m.id: m for m in (ListedModel.model_validate(row) for row in rows)}

    @staticmethod
    def name(model_id: str) -> str:
        """A config name for a model id: `deepseek/deepseek-v4.1-flash` becomes `deepseek-v4.1-flash`."""
        return re.sub(r"[^A-Za-z0-9.\-]+", "-", model_id.split("/", 1)[-1]).strip("-")

    def tiered(self, ids: Sequence[str]) -> dict[str, list[int]]:
        """The models whose price rises for long prompts, with the prompt sizes where it does."""
        listed = self.listed()
        return {self.name(i): listed[i].tiers for i in ids if i in listed and listed[i].tiers}

    def entries(self, ids: Sequence[str]) -> dict[str, ModelConfig]:
        """Config entries for these models. Unknown ids and models without tool calling are refused."""
        listed = self.listed()
        unknown = [i for i in ids if i not in listed]
        if unknown:
            raise ValueError(f"OpenRouter doesn't list {unknown}")
        without_tools = [i for i in ids if not listed[i].tools]
        if without_tools:
            raise ValueError(f"no tool calling, which builders need: {without_tools}")
        entries: dict[str, ModelConfig] = {}
        for model_id in ids:
            m = listed[model_id]
            cache = m.per_million("input_cache_read")
            price = Price(
                input=m.per_million("prompt"), output=m.per_million("completion"), cache_read=cache or None
            )
            entries[self.name(model_id)] = ModelConfig(
                model=f"openai:{model_id}",
                base_url=self.BASE_URL,
                api_key_env=self.KEY,
                max_tokens=self.MAX_TOKENS,
                usd_per_million=price,
            )
        return entries

    @staticmethod
    def render(entries: Mapping[str, ModelConfig], today: date, tiered: Mapping[str, list[int]] = {}) -> str:
        """The entries as a `models:` YAML section, with the date the prices were read and tier warnings."""
        data = {name: config.model_dump(exclude_none=True) for name, config in entries.items()}
        header = f"# OpenRouter models, prices read {today.isoformat()} (`agentswarm openrouter-models`)\n"
        for name, starts in tiered.items():
            header += (
                f"# {name}: a higher price applies from {', '.join(f'{n:,}' for n in starts)} prompt tokens\n"
            )
        return header + yaml.safe_dump({"models": data}, sort_keys=False, width=200)
