# Research from the open web (goal 14)

The user's goal (2026-10-19): documents from heterogeneous web sources, not only papers and Wikipedia; "a big project, but important". The Venice guide showed the gap: official sites refused a program (UNESCO 403) or sent an empty JavaScript shell (Venezia Unica), so it was written from Wikipedia alone, and its picks were all history and art. Evidence in docs/RESEARCH.md section 15.

## What exists

- The finder (`evaluation/papers.py`): a model writes search queries, each `PaperSource` (OpenAlex, arXiv, Wikipedia) returns `Candidate`s through the `PaperSearch` protocol, a model picks among them, and the picks are downloaded into sources.
- The document run (`pipeline/`): sources approved by the user, sections written with citations, a citation audit, and the claim-by-claim review (two rounds), which checks every cited statement against its source.
- Hybrid search over sources (`retrieval/`), for when the sources are too many or too long to read whole.

## Plan, in order of need

1. **Web search as a source (`PaperSource.WEB`)**: Tavily's search through the existing protocol; a page's site is its author, its kind recorded (`Candidate.kind`), its text taken with Tavily's extract (clean text, no scraping code of ours). The user chose Tavily (2026-11-10, free tier: 1,000 credits a month).
2. **Queries per thread of the request (STORM)**: the query writer lists the reader's questions per thread first (what a first-time visitor must know: getting there, getting around, what to see, when, costs) and writes queries for each, so the picks cover every thread.
3. **Source kinds**: official, institutional, press, reference, blog, recorded on each reference and shown at the sources approval; the picker prefers the stronger kinds where a thread has them.
4. **Dates**: a page's date kept with its source; statements about prices, hours and schedules cite a dated source.
5. **Many sources**: when the sources pass what a writer reads whole, writers and the checker get passages from the hybrid search (retrieval/, with the Self-Route fallback to the whole part).
6. **A second source for key claims (SAFE)**: later, if the audit shows web sources disagreeing or wrong.

Pages that need JavaScript (a headless browser) wait until extraction failures show they matter: Tavily's extract renders many of them.

## Definition of done for the first run

The Venice guide again, for a first-time visitor, from the open web: every thread of the request covered by at least one source of a strong kind, at least 85% of cited statements fully supported by the audit after the review, and the cost within the run's approved budget.

## Built (2026-11-10)

- **Steps 1-3:** `TavilySearch` (`PaperSource.WEB`, `--source web` in `find-papers` and `write`, and in the studio's form): a search is 1 credit, a picked page's text comes from Tavily's extract (1 credit per 5 pages), cited by site, title and link; a web search plan writes 8 queries (`PaperSource.queries`). The query plan lists the request's threads as the reader's questions before its queries, the picker must cover every thread and says which each pick answers, and labels a web page's publisher (`Standing`: official, institutional, scholarly, reference, press, guide, commercial, blog), preferring official and institutional sites. The approval listing (papers.md) shows each pick's standing and thread, and the threads no pick covers; a reference shows its site's standing.
- **First test search (2026-11-10):** "Venice vaporetto tickets for visitors" gave a pass seller, a family travel blog and a tourism portal, none of them the transit operator: the reason for `Standing` and the preference for official sites.
