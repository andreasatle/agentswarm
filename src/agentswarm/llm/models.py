# LangChain adapter: its types are partially unknown to pyright, so these two strict rules are relaxed here.
# pyright: reportUnknownVariableType=false, reportUnknownMemberType=false
"""Chat models from any LangChain provider, built from the project configuration."""

from typing import Any, ClassVar

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel

from agentswarm.core.config import ModelConfig


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

    # Settings a provider's chat model names differently. LangChain drops an unknown name without a word:
    # Ollama's model ignores `max_tokens` and limits output only through `num_predict`.
    RENAMED: ClassVar[dict[str, dict[str, str]]] = {"ollama": {"max_tokens": "num_predict"}}
    # Providers whose thinking models can be switched to answer without reasoning, and the setting's name.
    THINKING_SETTING: ClassVar[dict[str, str]] = {"ollama": "reasoning"}
    # Providers whose context window is a setting, and the name of that setting. Elsewhere the window is
    # fixed by the model, so setting it is a configuration error rather than something to ignore.
    CONTEXT_SETTING: ClassVar[dict[str, str]] = {"ollama": "num_ctx"}
    # Ollama's own default is 4096 tokens whatever the model supports, and it silently drops the start of
    # a longer conversation, instructions included: Phase 8's first runs lost their answers to it.
    DEFAULT_CONTEXT: ClassVar[dict[str, int]] = {"ollama": 32_768}

    @classmethod
    def settings(cls, config: ModelConfig) -> dict[str, Any]:
        """The keyword arguments for init_chat_model: what the configuration sets, plus a context window
        for providers whose default one is too small."""
        renamed = cls.RENAMED.get(config.provider, {})
        candidates: dict[str, object | None] = {
            "temperature": config.temperature,
            renamed.get("max_tokens", "max_tokens"): config.max_tokens,
            "base_url": config.base_url,
            "timeout": config.timeout_s,
        }
        if config.thinking is not None:
            setting = cls.THINKING_SETTING.get(config.provider)
            if setting is None:
                raise ModelUnavailable(
                    f"{config.model}: `thinking` can't be set for provider {config.provider!r} yet "
                    f"(settable for: {sorted(cls.THINKING_SETTING)})"
                )
            candidates[setting] = config.thinking
        context = config.context_window or cls.DEFAULT_CONTEXT.get(config.provider)
        if context is not None:
            setting = cls.CONTEXT_SETTING.get(config.provider)
            if setting is None:
                raise ModelUnavailable(
                    f"{config.model}: the context window is fixed for provider {config.provider!r}; "
                    f"remove `context_window` (settable for: {sorted(cls.CONTEXT_SETTING)})"
                )
            candidates[setting] = context
        return {key: value for key, value in candidates.items() if value is not None}

    @classmethod
    def install_hint(cls, config: ModelConfig) -> str:
        extra = cls.EXTRAS.get(config.provider)
        if extra is not None:
            return f"install the provider with `uv sync --extra {extra}`"
        provider = config.provider
        return f"install the LangChain package for provider {provider!r} (usually langchain-{provider})"
