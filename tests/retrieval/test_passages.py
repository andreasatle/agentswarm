from pathlib import Path

import pytest

from agentswarm.retrieval.passages import Chunker, PassageIndex
from tests.support.fakes import HashingEmbedder

PAPER = """# Sleep and memory

[page 3]
Slow-wave sleep follows learning in rats.

Hippocampal cells replay the day's sequences during slow-wave sleep, compressed in time.

[page 4]
Acetylcholine is low during slow-wave sleep, which lets the hippocampus write to the cortex.

[page 5]
Emotional memories are kept better after a night of sleep than after a day awake.
"""
OTHER = "# Synapses\n\nDuring sleep synapses are scaled down, so the brain saves energy and space.\n"


class TestChunker:
    def test_passages_are_whole_paragraphs_and_know_their_page(self) -> None:
        passages = Chunker(size=100).passages("paper", PAPER)
        assert [p.page for p in passages] == [None, 3, 3, 4, 5]
        assert passages[2].text.startswith("Hippocampal cells replay")
        assert all("[page" not in p.text for p in passages)

    def test_short_paragraphs_on_one_page_share_a_passage(self) -> None:
        passages = Chunker(size=1200).passages("paper", PAPER)
        assert [p.page for p in passages] == [None, 3, 4, 5]
        assert "Slow-wave sleep follows" in passages[1].text and "replay" in passages[1].text

    def test_overlap_carries_a_short_last_paragraph_into_the_next_passage_on_its_page(self) -> None:
        text = "Alpha one.\n\nBeta two.\n\nGamma three.\n\n[page 2]\nDelta four."
        passages = Chunker(size=22, overlap=12).passages("s", text)
        assert [p.text for p in passages] == [
            "Alpha one.\n\nBeta two.",
            "Beta two.\n\nGamma three.",
            "Delta four.",  # a new page starts clean
        ]

    def test_context_starts_each_passage_with_the_sources_title_without_its_part(self) -> None:
        text = "# sleep-and-memory, part 2 of 3\n\nReplay happens in slow-wave sleep."
        passages = Chunker(context=True).passages("s", text)
        assert passages[0].text.startswith("sleep-and-memory\n\n")


class TestPassageIndex:
    async def test_a_query_finds_the_passage_about_it_within_the_sources_named(self) -> None:
        index = PassageIndex(HashingEmbedder(), chunker=Chunker(size=100))
        await index.sync({"paper": PAPER, "other": OTHER})
        found = await index.search("low acetylcholine during slow-wave sleep", ["paper"], k=1)
        assert [(p.source, p.page) for p in found] == [("paper", 4)]
        assert not any(p.source == "other" for p in await index.search("synapses scaled", ["paper"], k=5))
        assert (await index.search("synapses scaled", ["other"], k=1))[0].source == "other"
        assert await index.search("anything", [], k=3) == []

    async def test_a_changed_source_is_indexed_again_and_unchanged_text_is_never_embedded_twice(self) -> None:
        embedder = HashingEmbedder()
        index = PassageIndex(embedder, chunker=Chunker(size=100))
        await index.sync({"paper": PAPER})
        first = embedder.embedded
        await index.sync({"paper": PAPER})  # unchanged: nothing to do
        assert embedder.embedded == first
        await index.sync({"paper": PAPER + "\n[page 6]\nDreams replay fragments of the day.\n"})
        assert embedder.embedded == first + 1  # only the new paragraph is embedded
        found = await index.search("dreams fragments", ["paper"], k=1)
        assert found[0].page == 6
        assert len(await index.search("sleep", ["paper"], k=50)) == 6  # the old passages were replaced

    async def test_a_saved_index_opens_again_but_never_with_another_model(self, tmp_path: Path) -> None:
        path = tmp_path / "retrieval.sqlite"
        index = PassageIndex(HashingEmbedder(), path, Chunker(size=100))
        await index.sync({"paper": PAPER})
        index.close()
        embedder = HashingEmbedder()
        reopened = PassageIndex(embedder, path, Chunker(size=100))
        await reopened.sync({"paper": PAPER})
        assert embedder.embedded == 0
        assert (await reopened.search("emotional memories", ["paper"], k=1))[0].page == 5
        reopened.close()
        with pytest.raises(ValueError, match="holds vectors of fake:hashing"):
            PassageIndex(HashingEmbedder(name="fake:other"), path)


class TestLongParagraphs:
    def test_a_paragraph_longer_than_a_passage_is_cut_at_line_breaks(self) -> None:
        page = "\n".join(f"Line {i} of a page extracted from a PDF with no blank lines." for i in range(40))
        passages = Chunker(size=300).passages("paper", f"[page 2]\n{page}\n")
        assert len(passages) > 5 and all(len(p.text) <= 300 for p in passages)
        assert all(p.page == 2 for p in passages)
        assert "\n".join(p.text for p in passages) == page
