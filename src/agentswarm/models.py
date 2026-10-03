# LangChain adapter: its types are partially unknown to pyright, so these two strict rules are relaxed here.
# pyright: reportUnknownVariableType=false, reportUnknownMemberType=false
"""Chat models from any LangChain provider, built from the project configuration."""

from typing import Any, ClassVar

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel

from agentswarm.config import ModelConfig


class ModelUnavailable(Exception):
    """A configured model can't be built, e.g. because its provider's package isn't installed."""


class ChatModels:
    """Builds and caches one chat model per named entry in the project's `models` section."""

    # Providers with an extra in pyproject.toml. Any other LangChain provider works once its package is
    # installed.
    EXTRAS: ClassVar[dict[str, str]] = {"anthropic": "anthropic", "openai": "openai", "ollama": "ollama"}

    def __init__(self, configs: dict[str, ModelConfig]) -> None:
        self._configs = configs
        self._built: dict[str, BaseChatModel] = {}

    def get(self, name: str) -> BaseChatModel:
        if name not in self._built:
            self._built[name] = self._build(name)
        return self._built[name]

    def _build(self, name: str) -> BaseChatModel:
        config = self._configs.get(name)
        if config is None:
            raise ModelUnavailable(f"no model named {name!r}; known: {sorted(self._configs)}")
        try:
            model = init_chat_model(config.model, **self.settings(config))
        except ImportError as error:
            raise ModelUnavailable(f"model {name!r} ({config.model}): {self.install_hint(config)}") from error
        if not isinstance(model, BaseChatModel):
            raise ModelUnavailable(f"model {name!r} ({config.model}) is not a concrete chat model")
        return model

    @staticmethod
    def settings(config: ModelConfig) -> dict[str, Any]:
        """The keyword arguments for init_chat_model: only what the configuration sets."""
        candidates: dict[str, object | None] = {
            "temperature": config.temperature,
            "max_tokens": config.max_tokens,
            "base_url": config.base_url,
            "timeout": config.timeout_s,
        }
        return {key: value for key, value in candidates.items() if value is not None}

    @classmethod
    def install_hint(cls, config: ModelConfig) -> str:
        extra = cls.EXTRAS.get(config.provider)
        if extra is not None:
            return f"install the provider with `uv sync --extra {extra}`"
        provider = config.provider
        return f"install the LangChain package for provider {provider!r} (usually langchain-{provider})"
