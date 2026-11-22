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

**First measure (2026-11-14), `find-papers --source web`, 8 picks, $0.45 for both:**

| topic | threads covered after the first search | at the end | rounds | sources | cost |
|---|---|---|---|---|---|
| how companies automate software engineering and IT operations (post 3's topic, without its brief) | 6 of 8 | 8 of 8 | 1 | 9 | $0.23 |
| a guide to Venice for a first-time visitor | 7 of 8 | 8 of 8 | 1 | 9 | $0.22 |

The loop did what it was built for: one follow-up round closed the gaps in both. Two caveats. "Covered" is a low bar: Venice's history and art was covered by one sentence of a travel guide ("Venice city used to be a bridge between the Middle East and the rest of Europe"), which answers the question only nominally; the check should ask whether a passage answers the thread well enough for the document, not whether it touches it. And without post 3's brief the plan's threads differ (no IT service desk thread), so post 3's gap is not retested here. Two topics are not a result; the real measure is a document run from the loop's sources through the audit and the benchmark's judge.

The check's bar raised the same day: a passage answers a thread only if the document could be written from it (the facts, figures, steps or evidence the question asks for; a broad thread's substance, not one general sentence), else the thread counts as uncovered and is searched again.

**A document from the loop's sources (2026-11-14, `iter-engineering`, post 3's brief, a measure only):** coverage 4 of 10 threads after the first search, 7 after 2 rounds (the stricter bar); 14 sources against post 3's 8, and 9 sections against 7 (new: the enterprise rollout at Accenture, the Stack Overflow survey, AI for SRE). Still uncovered: IT service desks (a vendor's ROI page was found and judged not to answer it) and incident-response outcomes; and one thread no search can cover, "what the cases have in common", the document's own conclusion: the plan should not list such threads. Sources cost $0.40 against $0.08. Audit before review 89% (post 3: 84%). The benchmark's judge: 9 flaws against post 3's 10 (citation form 2 against 3, unclear 6 against 5, repetition 1 against 2), within its noise, and post 3 had its review while this run's was cut short by the budget: the mandatory reader review cost $1.38 in referee calls (docs/design/self-correction.md). Not a result yet on document quality; on coverage, more sources and sections for the same brief.

**A document on Lean (2026-11-21, `lean-agents`, `--source web`, $2.05 in all):** coverage 2 of 8 threads after the first search, 6 after 2 rounds. Still uncovered: mathlib, and Lean against other proof assistants. The mathlib gap is a miss the loop should not make: the Lean community's own homepage describes mathlib and links its 2020 paper, so a search for the library's home would have found both; added by hand with the user's approval, with a de Moura lecture's garbled auto-captions dropped. Audit 80% before review, 75% after (within the audit's noise). The hand-added homepage exposed a matching bug: files were matched to records in alphabetical order, and the homepage, which names the Lean 4 textbook near its top, took the textbook's record, so its reference showed the textbook's title. Fixed (a record kept for another file is that file's; a refresh matches files by name first) and the run's reference list rebuilt with the fixed code, one line changed; the text is the swarm's. Due with the next web runs: whether the follow-up queries should search for a thread's primary home (a project's own site) before secondary pages.

**The second Lean document (2026-11-21, `lean-agents-why`):** coverage 3 of 8 threads after the first search, 5 after 1 round, and the 3 uncovered were the document's core: what plays the verifier's role in software, in science and engineering, and the limits. Four well-known sources were added by hand with the user's approval (Jason Wei on the asymmetry of verification, AlphaEvolve, FunSearch, SWE-bench). The second miss of this kind in a row (mathlib in `lean-agents`): the follow-up queries find pages about the topic's own words but not the canonical works a thread names only by idea. Two candidate fixes, due when the next web run shows the same pattern: ask the planner for each thread's best-known works by name (the model knows AlphaEvolve and SWE-bench) and search for those titles; or let follow-up rounds search papers (arXiv, OpenAlex) as well as the web. Also seen: a PDF's first line can be a venue header ("Published as a conference paper at ICLR 2024"), so the named-PDF title needs a better source than the first line.

**The Principia-to-Lean document (2026-11-21, `principia-to-lean`, a topic the swarm hadn't seen, no sources added by hand):** 12 sources, fitting ones: seven Stanford Encyclopedia entries (Principia, Russell's paradox, Hilbert's programme, Gödel, Church, Church-Turing and its Entscheidungsproblem subpage), Wadler's propositions as types, Harrison's history of interactive theorem proving, de Bruijn's road to Automath, Gonthier's Four Colour proof, the Lean 4 paper. Coverage reported 2 of 8 threads, then 4 after 2 rounds, but that undercounts: "what were Russell and Whitehead trying to do" is listed uncovered with two sources on exactly that, and Church-Turing with three. The coverage check asks for a quoted passage per thread, so a thread whose sources are right but whose quote didn't match counts as uncovered and drives needless follow-up rounds. Also: mathlib missed a third time (Coq only through the Four Colour paper); three overlapping Church-Turing pages; every source n.d. though archived Stanford entries carry their edition in the title (BACKLOG, dating searched sources). Due with the next web run: whether coverage should count a thread covered when a source chosen for it is kept, and check the quote only as evidence.
