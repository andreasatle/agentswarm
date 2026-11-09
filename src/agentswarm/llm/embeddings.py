# LangChain adapter: its types are partially unknown to pyright, so these two strict rules are relaxed here.
# pyright: reportUnknownVariableType=false, reportUnknownMemberType=false
"""Embedding models from any LangChain provider, for retrieval (retrieval/passages.py)."""

from collections.abc import Sequence

from langchain.embeddings import init_embeddings

from agentswarm.llm.models import ModelUnavailable


class LangChainEmbedder:
    """An embedding model named "<provider>:<model>", e.g. "ollama:nomic-embed-text" (local and free).

    Some models are trained with a task prefix on each text (nomic-embed-text: "search_query: " and
    "search_document: "); the configuration names them, since they belong to the model, not the provider.
    """

    def __init__(self, model: str, query_prefix: str = "", document_prefix: str = "") -> None:
        try:
            self._model = init_embeddings(model)
        except ImportError as error:
            raise ModelUnavailable(f"embedding model {model!r}: install its provider's extra") from error
        self._name = model
        self._query_prefix = query_prefix
        self._document_prefix = document_prefix

    @property
    def name(self) -> str:
        return self._name

    async def documents(self, texts: Sequence[str]) -> list[list[float]]:
        return await self._model.aembed_documents([self._document_prefix + t for t in texts])

    async def query(self, text: str) -> list[float]:
        return await self._model.aembed_query(self._query_prefix + text)
