"""The retrieval benchmark: does hybrid search find the passage that supports a statement?
(docs/design/rag.md, step 1).

Measured before any model reads retrieved passages, so a change to the index (chunking, context, reranking) is
judged on retrieval alone. The known answers come from citation audits already run: a statement the audit
found supported (fully or partly), with the source text the audit quoted in its reason. The query is the
statement without its citations; a retrieved passage is a hit when it holds the start or the end of that
quote. Two searches per query: within the cited part (as the claim checker searches) and across every part of
the document's sources (as a writer, or a library, would).
"""

import json
import re
from pathlib import Path
from typing import ClassVar, Self

import yaml
from pydantic import BaseModel, ConfigDict, TypeAdapter

from agentswarm.evaluation.citations import Audited, CitationAudit, Support
from agentswarm.retrieval.passages import Passage, PassageIndex


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ReviewComparison(BaseModel):
    """A review A/B's file (review-ab.json): the reference audit is the part read here."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    reference: list[Audited]


class RetrievalCase(Frozen):
    """A document's audited statements and the folder of source parts they cite."""

    name: str
    audit: Path  # a list of audited statements, or a review A/B's file with its reference audit
    sources: Path  # the parts as the run cut them: <name>.md, cited as [@name]

    def audited(self) -> list[Audited]:
        data = json.loads(self.audit.expanduser().read_text())
        if isinstance(data, list):
            return TypeAdapter(list[Audited]).validate_python(data)
        return ReviewComparison.model_validate(data).reference

    def parts(self) -> dict[str, str]:
        return {p.stem: p.read_text() for p in sorted(self.sources.expanduser().glob("*.md"))}


class RetrievalCases(Frozen):
    cases: tuple[RetrievalCase, ...]

    FILE: ClassVar[Path] = Path(__file__).with_name("retrieval.yaml")

    @classmethod
    def load(cls, path: Path | None = None) -> Self:
        return cls.model_validate(yaml.safe_load((path or cls.FILE).read_text()))


class GoldQuery(Frozen):
    query: str  # the statement without its citations
    part: str  # the cited part that holds the quote
    quote: str  # the source text the audit quoted, as letters and digits (`letters`)


class RetrievalScore(Frozen):
    """Ranks of the first hit, within the cited part and across all parts; recall at k and MRR from them."""

    case: str
    queries: int
    within: tuple[int | None, ...]  # 1-based rank of the first hit; None: not in the top K
    across: tuple[int | None, ...]

    @staticmethod
    def recall(ranks: tuple[int | None, ...], k: int) -> float:
        return sum(r is not None and r <= k for r in ranks) / len(ranks) if ranks else 0.0

    @staticmethod
    def mrr(ranks: tuple[int | None, ...]) -> float:
        return sum(1 / r for r in ranks if r is not None) / len(ranks) if ranks else 0.0

    def summary(self) -> dict[str, float | int | str]:
        figures: dict[str, float | int | str] = {"case": self.case, "queries": self.queries}
        for name, ranks in (("within", self.within), ("across", self.across)):
            for k in (1, 5, 10):
                figures[f"{name}_r@{k}"] = round(self.recall(ranks, k), 3)
            figures[f"{name}_mrr"] = round(self.mrr(ranks), 3)
        return figures


class RetrievalBenchmark:
    """Scores an index on the gold queries of each case."""

    QUOTE: ClassVar[re.Pattern[str]] = re.compile(r"[\"“]([^\"“”]{30,}?)[\"”]")
    END: ClassVar[int] = 30  # letters of a quote's start or end a hit must hold
    K: ClassVar[int] = 10

    def __init__(self, index: PassageIndex) -> None:
        self._index = index

    @classmethod
    def gold(cls, case: RetrievalCase) -> list[GoldQuery]:
        """A query for each supported statement whose audit quotes text that is in a part it cites."""
        parts = {name: cls.letters(text) for name, text in case.parts().items()}
        found: list[GoldQuery] = []
        for audited in case.audited():
            if audited.support not in (Support.SUPPORTED, Support.PARTLY):
                continue
            cited = [m.group(1) for m in CitationAudit.CITATION.finditer(audited.statement)]
            query = cls.normal(CitationAudit.CITATION.sub("", audited.statement))
            for quoted in cls.QUOTE.findall(audited.reason):
                quote = cls.letters(quoted)
                part = next((c for c in cited if c in parts and quote in parts[c]), None)
                if part is not None:
                    found.append(GoldQuery(query=query, part=part, quote=quote))
                    break
        return found

    async def scores(self, cases: RetrievalCases) -> list[RetrievalScore]:
        """Every case in one event loop: the embedding model's client is bound to the loop it first ran in."""
        return [await self.score(case) for case in cases.cases]

    async def score(self, case: RetrievalCase) -> RetrievalScore:
        parts = case.parts()
        await self._index.sync(parts)
        gold = self.gold(case)
        within: list[int | None] = []
        across: list[int | None] = []
        for query in gold:
            within.append(self.rank(await self._index.search(query.query, [query.part], self.K), query))
            across.append(self.rank(await self._index.search(query.query, list(parts), self.K), query))
        return RetrievalScore(case=case.name, queries=len(gold), within=tuple(within), across=tuple(across))

    @classmethod
    def rank(cls, passages: list[Passage], query: GoldQuery) -> int | None:
        for n, passage in enumerate(passages, 1):
            if cls.hit(cls.letters(passage.text), query):
                return n
        return None

    @classmethod
    def hit(cls, text: str, query: GoldQuery) -> bool:
        """The passage holds the quote's start or its end (a quote may straddle two passages)."""
        return query.quote[: cls.END] in text or query.quote[-cls.END :] in text

    @staticmethod
    def normal(text: str) -> str:
        return " ".join(text.split())

    @staticmethod
    def letters(text: str) -> str:
        """Lower-case letters and digits only: a quote still matches text from a PDF whose lines were broken,
        hyphenated or given other quotation marks."""
        return re.sub(r"[^a-z0-9]", "", text.lower())
