# Where we are (2026-10-18, before a ten-day break)

A note to pick up from after the user's trip (back about 2026-10-29). Details live in the docs named.

## What works

- **Building software to a definition of done** (stages 1-3, docs/ROADMAP.md): CommonMark, TOML, jq, YAML 1.2 and HTML5 from empty stubs, 87-100% of held-out tests; self-supervised TOML, YAML and JSONPath (every test hidden) 97-100%.
- **Hardening by red teams** (docs/design/hardening.md): jq 87% to 94% held out over three rounds and the combination focus; YAML-self 97% to 98%. Diminishing returns per round.
- **Documents** (docs/design/documents.md): briefer, one-call section writers, author-year citations with pages, perspective reviewers (facts, coverage, reader, consistency) and a citation audit. Measured on Qasper: perspectives suggestive, not proven (19 against 15-18 of 36).
- **Real use:** `import-docs` (a report from your own files: the Challenger report), `learn` (a tutorial from papers: JEPA, test-time compute), `find-papers` (a topic to papers from OpenAlex or arXiv, picked by a model, real because they come from the search; downloads what it can, lists the rest with free links for the user).

## Measured weak spots

- **Citations:** about 75% of cited statements fully supported after one facts review (65% before); most of the rest overstate. Checking every statement is parked (docs/BACKLOG.md).
- **Downloads:** publishers refuse most programs; on sleep and memory none of five picks downloaded, the user fetched three by hand. Repository copies (PubMed Central, arXiv) are reliable; "bronze" open access is not.
- **Budget checks** happen between attempts, so a run can overshoot its cap (one trial: estimated $0.30, cost $2.16).

## Open, in the order I'd take them

0. **Two goals from the user, 2026-10-19, before anything below** (docs/BACKLOG.md): one command from topic to published document with the user's approvals built in, and research from the open web beyond papers and Wikipedia. Since this note was first written: the mechanistic interpretability tutorial (fact-reviewed, about 62% of citations fully supported) and a Venice guide from Wikipedia are on the homepage.
1. **Sleep and memory tutorial** (the first non-technical test of `learn`): three of five papers in `~/agentswarm-data/sleep-and-memory`; Siegel 2001 is free in PubMed Central (link in papers.md). About $0.20. Tells whether `learn` serves a lay reader.
2. **The picker told how each paper is open** (green and gold before bronze), so it prefers reliably free copies when two papers are equally good. Free work.
3. **Homepage:** commit 4feba43 in ~/Projects/AgenticFramework (find-papers) is not deployed; `scripts/deploy.sh` there, run by the user.
4. **Stage 4, when a swarm is competitive** (COLLABORATION.md goal 2): waits until the framework behaves well. The document path is close to that; software is there.

## Standing rules to remember

- No daily budget: each paid run gets its expected cost and the day's spend, and the user decides (memory: spending-levels).
- Chat answers short, main message first; the user learns by asking.
- Nothing runs unattended while the user is away.
