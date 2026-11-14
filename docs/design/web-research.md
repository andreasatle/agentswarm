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

## Iterative search (2026-11-14)

The user's order: advanced web search on 2026-11-14, RAG at scale after it. The problem it answers: the finder searches once, picks about 10 sources and stops; the threads no pick covers are only listed (post 3, IT service desks: two threads uncovered), and "covers" is the picker's word from a search snippet, never checked against the page.

**Best practice (read 2026-11-14):**

- Anthropic's research system: start with short, broad queries, then narrow; after each tool result, evaluate quality, identify gaps and refine the next query; effort scaled to the task in the prompt (3-10 calls for fact-finding, more for broad research), because agents both searched endlessly and stopped too early; source-quality heuristics added after agents preferred SEO farms; evaluated on about 20 queries with a rubric judge, and by people for what the judge missed ([Anthropic, 2025](https://www.anthropic.com/engineering/multi-agent-research-system)).
- Gap-driven follow-up queries with a stopping rule: an LLM compares what was found with what is needed, lists the gaps and suggests queries for them; the loop stops at no important gap or a maximum number of rounds (typically 2) (Hypothesis-Driven Deep Research, [arXiv 2605.10224](https://arxiv.org/pdf/2605.10224)). Practitioner guides agree: a hard cap paired with a quality signal, and stop on diminishing returns (no new facts in a round).
- Following links helps, modestly: WebThinker's ablation scores 45.4 with link clicking against 42.6 without it, and 38.3 without its explorer at all (averaged over four benchmarks; [Li et al., 2025](https://arxiv.org/abs/2504.21776)). Iterating retrieval over one-shot retrieval helped one model a lot (15.3 to 30.9 on WebWalkerQA) and another hardly (31.2 to 31.5) in the same paper.
- Knowing when it is done is a measured weakness of research agents (DeepSearchQA, Google 2025): absence of evidence must be told apart from evidence of absence.

**The design:** plain code runs the loop; models plan, pick and judge.

1. Plan threads and queries, search, pick, download: as now.
2. **Coverage check:** a model reads the downloaded sources (the start of each, up to a cap) and, for each thread, names the source that answers it with a verbatim quote, which plain code checks is in that source's text; a thread is covered only with a checked quote. For each uncovered thread it writes one or two narrower queries. The same quote-backed pattern as the benchmark's judge.
3. **Follow-up round:** the follow-up queries are searched, new candidates only; the picker picks for the uncovered threads only (one new source per gap, at most 4 a round); they are downloaded; the check runs again.
4. **Stop** when every thread is covered, after 2 follow-up rounds, or when a round covers no new thread (diminishing returns). Cost per round: one check and one pick on the finder's model, about 5-10 searches and a few page extracts.
5. The reading list (papers.md, shown at the sources approval) lists each thread with the source and quote that cover it, the threads still uncovered, and coverage after the first search and at the end: that pair is the measure, from every run.

**Not now:** following links and references out of the picked pages (snowballing). Its evidence is a modest gain, and gap-driven searching is the cheaper step that answers the measured problem (uncovered threads). Due when runs show threads still uncovered after the follow-up rounds while the picked pages link to sources that would cover them.

**Measure:** a few topics through `agentswarm find-papers --source web`, coverage after the first search against the end, credits and dollars; then a document run whose sources come from the loop, through the citation audit and the benchmark's judge.
