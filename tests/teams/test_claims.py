from pydantic import BaseModel

from agentswarm.evaluation.citations import CitationRequest, Support
from agentswarm.llm.workers import FakeWorker, Step, Toolbox
from agentswarm.retrieval.passages import Chunker, PassageIndex
from agentswarm.teams.claims import (
    Citation,
    CitedStatement,
    ClaimChecker,
    ClaimVerdict,
    Retrieved,
    WholeParts,
)
from tests.support.fakes import HashingEmbedder

SECTION = """## Replay

Cells replay the day's sequences during sleep [@paper, page 3].
This replay is the cause of all memory [@paper].
An uncited sentence.
"""
PAPER = """# Sleep and memory

[page 3]
Hippocampal cells replay the day's sequences during slow-wave sleep, compressed in time.

[page 4]
Acetylcholine is low during slow-wave sleep.

[page 5]
Replay is one mechanism of consolidation among several.
"""
SOURCES = {"paper": PAPER}


def verdict(support: Support, quote: str = "") -> Step[BaseModel]:
    def step(toolbox: Toolbox | None) -> BaseModel:
        return ClaimVerdict(reason="the paper says otherwise", support=support, source_quote=quote)

    return step


class TestCitedStatement:
    def test_each_line_citing_a_source_is_a_statement_with_its_citations(self) -> None:
        found = CitedStatement.all_in("sections/01.md", SECTION)
        assert [s.citations for s in found] == [
            (Citation(name="paper", page=3),),
            (Citation(name="paper", page=None),),
        ]
        assert found[0].claim() == "Cells replay the day's sequences during sleep ."
        assert found[1].text == "This replay is the cause of all memory [@paper]."


class TestSourceText:
    async def test_whole_parts_give_the_named_page_and_its_neighbours_else_the_part(self) -> None:
        paged, unpaged = CitedStatement.all_in("s.md", SECTION)
        near = (await WholeParts().cited(paged, SOURCES))[0]
        assert "replay the day's sequences" in near.text and "one mechanism" not in near.text
        assert (await WholeParts().cited(unpaged, SOURCES))[0].text == PAPER
        missing = CitedStatement(file="s.md", text="x [@gone]", citations=(Citation(name="gone", page=None),))
        assert (await WholeParts().cited(missing, SOURCES))[0].text == "(no such source)"

    async def test_retrieval_gives_only_the_passages_found_for_the_claim(self) -> None:
        source_text = Retrieved(PassageIndex(HashingEmbedder(), chunker=Chunker(size=100)), k=1)
        statement = CitedStatement.all_in("s.md", SECTION)[0]
        cited = (await source_text.cited(statement, SOURCES))[0]
        assert (
            cited.text
            == "[page 3]\nHippocampal cells replay the day's sequences during slow-wave sleep, "
            + ("compressed in time.")
        )


class TestClaimChecker:
    async def test_each_claim_gets_a_verdict_with_a_quote_only_when_it_is_in_the_source(self) -> None:
        worker = FakeWorker(
            [
                verdict(Support.SUPPORTED, "replay the day's sequences"),
                verdict(Support.PARTLY, "Replay is one mechanism of consolidation among several."),
            ]
        )
        checker = ClaimChecker(worker, WholeParts())
        checked = await checker.check(CitedStatement.all_in("s.md", SECTION), SOURCES)
        assert [c.support for c in checked] == [Support.SUPPORTED, Support.PARTLY]
        assert (checked[1].source, checked[1].source_quote) == (
            "paper",
            "Replay is one mechanism of consolidation among several.",
        )
        request = worker.calls[0][1]
        assert isinstance(request, CitationRequest) and request.statement.startswith("Cells replay")

    async def test_an_invented_quote_is_dropped_and_a_failed_call_has_no_verdict(self) -> None:
        checker = ClaimChecker(
            FakeWorker([verdict(Support.UNSUPPORTED, "replay causes all memory")]), WholeParts()
        )
        statements = CitedStatement.all_in("s.md", SECTION)
        invented, failed = await checker.check(statements, SOURCES)  # the script ends after one call
        assert (invented.support, invented.source, invented.source_quote) == (Support.UNSUPPORTED, None, "")
        assert failed.support is None and "exhausted" in failed.reason
