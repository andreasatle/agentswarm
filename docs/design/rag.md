# Retrieval at scale: an evaluated index, a library across runs, and writers on passages

Started 2026-11-14 at the user's request ("dwell into the RAGs with re-indexing and all ... whatever is best practice"), after iterative search (docs/design/web-research.md). Evidence in docs/RESEARCH.md section 14; this doc adds what is needed to use retrieval beyond the claim checker.

## What exists

- **The index** (`retrieval/passages.py`, one SQLite file): sources cut into passages of whole paragraphs (up to 1,200 characters, a page marker starts a new one, each knows its page); FTS5 keywords (BM25) and sqlite-vec vectors (cosine), merged by reciprocal rank fusion; embeddings kept by the hash of their text and the model's name, so re-indexing a changed source re-embeds only the passages whose text changed, and an unchanged source costs nothing (`sync`). Embeddings from a local model (Ollama's nomic-embed-text): free, nothing leaves the machine.
- **One user:** the claim checker can read the passages hybrid search finds instead of the cited part whole (`review.claims.retrieval`). The A/B of 2026-11-09 (docs/design/documents.md) chose the whole part: retrieval missed 3 of 23 problems and doubled the false alarms at 9 parts and 174k characters, and "a claim about what a paper doesn't do retrieves the title page: search can't find an absence".
- **Writers read their sources whole:** each section gets the full text of the parts the outline assigns it. The largest writer input so far is about 80k tokens (iter-engineering, 14 sources), at Haiku prices about $0.008.

## Where retrieval would pay, honestly

Nothing measured needs it yet: the inputs fit, whole sources read better than passages at these sizes (RESEARCH.md 14: long context ahead at 32k tokens, retrieval ahead around 128k), and the one A/B went against it. It starts to pay in three places, each with a trigger:

1. **A section's sources outgrow the writer's input** (iterative search brings 30-100 pages, or a book-length source): the writer gets the passages for what its section covers, with Self-Route's fallback to the whole part when it says they don't suffice. Trigger: a section's assigned sources above about 60k tokens.
2. **Sources reused across documents** (a series such as the AI-workflows posts, or a field read once and written about often): a library that keeps every source a run fetched, indexed once, searched before the web. This is where re-indexing is real: the library grows and changes, and only what changed is re-chunked and re-embedded.
3. **Finding an important claim's second source** (web-research.md step 6, SAFE): search the library for an independent source that says the same.

## Best practice (RESEARCH.md 14, rechecked 2026-11-14)

- Hybrid search (BM25 + dense, fused by RRF) is the baseline, a reranker on top of it the largest single gain (Recall@5 0.70 to 0.82 in one benchmark); typical: 50-100 candidates, rerank to 5-8.
- Contextual retrieval: each chunk carries where it sits (its document and section; Anthropic has a model write a note per chunk) before it is embedded and keyword-indexed; Anthropic reports 49% fewer retrieval failures, 67% with a reranker (vendor numbers). A free version: the source's title and the section heading prefixed to each passage.
- Chunking matters (up to 9% recall between methods on one corpus, secondary); 256-512 tokens with 10-20% overlap is a common start, but the best depends on the documents: measure on ours.
- Evaluate retrieval on its own, before any end-to-end run: a golden set of queries with the passage that answers each, scored by Recall@k and MRR; then end-to-end quality separately, since recall alone can mislead. Change one thing at a time.
- Self-Route: answer from passages, fall back to the whole text when the model says they don't suffice; close to long-context quality at much lower cost.
- Incremental indexing: content hashes decide what is re-chunked and re-embedded; a source that disappears is removed, not left to match.

## The plan, cheapest and most certain first

1. **A retrieval benchmark (free, local).** Golden pairs from runs we already have: each cited statement the citation audit judged fully supported, as the query, and as the answer the passage holding the source text the audit quotes in its reason, else the page the statement names when the audit found the page right (web pages have no pages, so the quote matters there); plus the 50 statements Claude labelled on 2026-11-11 with their evidence. Scored: Recall@5, Recall@10 and MRR of the current index, by source kind (papers, web pages). This says whether the index is good enough to build on, before any model reads its output.
2. **Index improvements, each measured on it (free, local):** passages prefixed with their source's title and nearest heading; chunk size and overlap; then a reranker only if recall at 10 is far above recall at 5 (the right passage found but ranked low): a local cross-encoder if one runs on the Mac, else a cheap model's ranking, priced first.
3. **The library (re-indexing for real):** one index under ~/agentswarm-data/ that every document run's sources are added to, with each source's record (title, site, date, standing, link); sync removes what is gone; the finder searches it before the web and shows library hits beside web ones at the sources approval. Measured by the share of picks the library supplies in a series of runs, and the Tavily credits saved.
4. **Writers on passages (Self-Route), when trigger 1 fires:** the section's passages for what it covers, its fallback the whole part; measured against whole parts on the benchmark's cases by audit and judge, at equal sources.

Steps 1-2 cost nothing but time; step 3 is a moderate change with a clear use if the user writes series; step 4 waits for its trigger. The claim checker's choice (whole part) is revisited after step 2 if recall has improved a lot.

## Open question for the user

Whether a library across runs (step 3) is wanted now: it pays when documents share sources (the workflow series did), and it is the natural place for "re-indexing", but no measured problem asks for it yet.

## Results (2026-11-14)

**Step 1, the retrieval benchmark** (`agentswarm retrieval-benchmark <label>`, evaluation/retrieval.py, cases in retrieval.yaml, results in ~/agentswarm-data/retrieval-benchmark.jsonl): 64 known answers, 48 from the sleep tutorial's reference audit (9 parts of 5 papers, 174k characters) and 16 from the LLM-scaling audit (44 parts of 16 web pages and papers, 1.0M characters); quotes matched on letters and digits only, so PDF line breaks and quotation marks don't lose them (41 and 14 by plain text). Local embeddings (nomic-embed-text), free, about a minute a run.

**Step 2, variants**, recall at 5 and 10 (within the cited part / across all parts):

| variant | sleep within | sleep across | scaling within | scaling across |
|---|---|---|---|---|
| baseline: 1,200 characters | 0.85 / 0.98 | 0.79 / 0.90 | 1.00 / 1.00 | 0.94 / 1.00 |
| + overlap 300 | 0.88 / 0.98 | 0.81 / 0.90 | 1.00 / 1.00 | 0.94 / 1.00 |
| + title prefixed | 0.85 / 0.94 | 0.81 / 0.81 | 0.88 / 1.00 | 0.81 / 0.94 |
| 600 characters | 0.69 / 0.81 | 0.67 / 0.79 | 1.00 / 1.00 | 0.81 / 1.00 |
| 2,000 characters | 0.92 / 0.98 | 0.77 / 0.88 | 1.00 / 1.00 | 0.81 / 1.00 |

One query is 2 points on sleep and 6 on scaling, so only the 600-character drop is clearly real. Conclusions: the index as built is good (the right passage in the top 10 for 90-100% of statements, in the top 5 for 79-100%); smaller passages lose; the free contextual prefix doesn't help, because our parts' title lines are file slugs, not titles (a real title, or a model-written note as Anthropic does, is untested); overlap and larger passages are within the noise. No change to the defaults. A reranker could lift recall at 5 toward recall at 10 (a gap of about 10 points on sleep); the cheaper equivalent is k = 10, at twice the passages.

**What it means for token cost** (the user, 2026-11-14: "even if it is slightly better to send the full docs, it is also a matter of token cost"): 10 passages are about 12k characters. Within a cited part (19-23k characters on average) that is about half; across a document's sources it is 7% of the sleep sources and about 1% of the scaling sources. So retrieval matters little to the claim checker, whose input is one part (and cached), and a lot wherever a model reads all the sources: the writers (each reads its section's parts whole, up to about 80k tokens), and later the library. The next measure is end to end, where cost is: writers on passages with Self-Route's fallback against writers on whole parts, on the benchmark's cases, by audit, judge and cost (step 4, now worth trying before its size trigger, since the token saving is the goal).
