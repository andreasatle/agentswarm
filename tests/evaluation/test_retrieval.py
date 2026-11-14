import json
from pathlib import Path

from agentswarm.evaluation.retrieval import GoldQuery, RetrievalBenchmark, RetrievalCase, RetrievalScore
from agentswarm.retrieval.passages import Chunker, PassageIndex
from tests.support.fakes import HashingEmbedder

PART = (
    "# gais-2004, part 1 of 1\n\nAcetylcholine levels are low during slow-wave sleep in humans.\n\n"
    "Declarative memories were tested after a night of sleep or of wakefulness."
)
OTHER = "# born-2006, part 1 of 1\n\nSynapses are scaled down during sleep, which saves energy."


def audited(statement: str, support: str, reason: str) -> dict[str, object]:
    return {"file": "s.md", "statement": statement, "support": support, "page_right": None, "reason": reason}


def case(tmp_path: Path) -> RetrievalCase:
    sources = tmp_path / "sources"
    sources.mkdir()
    (sources / "gais-2004.md").write_text(PART)
    (sources / "born-2006.md").write_text(OTHER)
    records = [
        audited(
            "Acetylcholine is low in slow-wave sleep [@gais-2004].",
            "supported",
            'The source says: "Acetylcholine levels are low during slow-wave\nsleep in humans."',
        ),
        audited(
            "Sleep makes people taller [@gais-2004].", "unsupported", 'It says "nothing of the kind at all".'
        ),
        audited("Memory was tested [@gais-2004].", "partly", "No quotation in this reason."),
    ]
    (tmp_path / "audit.json").write_text(json.dumps({"reference": records, "arms": []}))
    return RetrievalCase(name="sleep", audit=tmp_path / "audit.json", sources=sources)


class TestRetrievalBenchmark:
    def test_gold_is_each_supported_statement_with_a_quote_found_in_the_part_it_cites(
        self, tmp_path: Path
    ) -> None:
        (gold,) = RetrievalBenchmark.gold(case(tmp_path))
        assert gold.query == "Acetylcholine is low in slow-wave sleep ."
        assert gold.part == "gais-2004"
        assert gold.quote == RetrievalBenchmark.letters(
            "Acetylcholine levels are low during slow-wave sleep in humans"
        )

    async def test_the_passage_holding_the_quote_is_found_within_the_part_and_across_all(
        self, tmp_path: Path
    ) -> None:
        index = PassageIndex(HashingEmbedder(), chunker=Chunker(size=80))
        score = await RetrievalBenchmark(index).score(case(tmp_path))
        assert score.queries == 1
        assert score.within[0] is not None and score.across[0] is not None

    def test_a_hit_holds_the_quotes_start_or_end_and_recall_counts_ranks_within_k(self) -> None:
        query = GoldQuery(query="q", part="p", quote="a" * 30 + "middle" + "z" * 30)
        assert RetrievalBenchmark.hit("x" + "a" * 30, query) and RetrievalBenchmark.hit("z" * 30, query)
        assert not RetrievalBenchmark.hit("middle", query)
        score = RetrievalScore(case="c", queries=4, within=(1, 3, None, 7), across=(2, None, None, None))
        assert score.recall(score.within, 5) == 0.5 and score.recall(score.within, 10) == 0.75
        assert score.mrr(score.across) == 0.125
