# Taking Claude out of the loop (proposal, 2026-11-22)

The user, 2026-11-22: "I do want an almost fully automated generation of whatever is the project we are working on, so what you are doing is too much for a user. But the user has to provide some input, possibly approve references etc. But it should be 'passive' interactions, like approve ... The system could for instance suggest that the user should download something that the agentswarm has difficulties with."

Goal 13 (COLLABORATION.md) made concrete: the user types once (what, for whom, how much), then only answers what the system asks: approve, choose among options it offers, or do a concrete chore it names (fetch this PDF from this link into this folder). Everything Claude does around a run today moves into the framework, or into a queue of framework issues that a developer works on outside any run.

## What Claude does around a run today (the evidence, from the runs of 2026-11-21/22)

| Claude's role | Seen in | What replaces it |
| --- | --- | --- |
| Writes the topic line, with its list of subjects (it shapes the finder's questions and so the whole document) | every run | **Scope proposal:** the planner expands the user's short topic into the questions the document must answer; the user approves or unticks |
| Adds sources the search missed (mathlib, AlphaEvolve, FunSearch, SWE-bench, Branch-Train-MiX, Anthropic's agents post) | lean-agents, lean-agents-why, moe-systems-2 | **Gap filling:** the finder asks for each uncovered question's best-known works by name and searches their titles; what it still can't get becomes a fetch request |
| Notices a stalled search, a coverage undercount | principia-to-lean, moe-systems | The search's own report (coverage per question), and the run reviewer below |
| Reads the draft and judges it (choppy prose, a table with one row, repetition, "Section 5", trivia) | every run | **The document's red team:** an agent of another model family attacks the finished document and the board and reports flaws with evidence |
| Fixes flaws in the run's output (references rebuilt) | principia-to-lean, moe-systems-2, lean-agents-why | Flaws the swarm can fix go back to its writers before publishing; flaws in the framework go to the issue queue |
| Fixes the framework's code | most runs | Unchanged in kind, but driven by the issue queue, between runs, never during one |
| Puts the page on the homepage | every published run | Stays a user command (the homepage is a separate project that AgentSwarm never touches) |

## The user's interactions, all of them

| When | What the user does | What the system shows |
| --- | --- | --- |
| Start | Types the topic, the reader and the budget (the only typing) | The plan and its expected cost |
| Scope | Approves, or unticks questions | The questions the document will answer, from the planner |
| Sources | Approves, unticks sources, ticks suggested additions; does any fetch request | Each source with why it was chosen; each question's coverage; **fetch requests:** "X (link) could not be downloaded (paywall, bot check): save it as `sources/<name>.pdf`" |
| Publish | Approves or stops | The document, the audit, the red team's report and what was fixed after it, the cost |

All four in the studio, which notifies when a run waits. A run that hits something it can't handle stops at the nearest of these with the choices spelled out (the pipeline's rule since 2026-11-01), never waiting for someone who knows the internals.

## What to build, in order of need

1. **The document's red team (first: it replaces the largest part of Claude's role).** Red teaming in the user's sense (2026-11-22): an agent attacking the product to find its flaws, by any means; here the whole document, where today's reviewers each look at one section from one perspective. After the review rounds and before the publishing approval: an agent of a family other than the writers' (error-correlation.md: same-family checkers miss the same things) reads the document, the source list and the board's summary, and reports typed findings, each with a quote or an event as evidence and a kind: **revise** (the swarm can fix it in this run: a broken table, repetition, a dangling cross-reference), **framework** (a bug in the code: a wrong reference, a stalled search), **note** (for the user, no action). Revise findings go to the section writers as ordinary findings (the referee rules on them as on every other); framework findings go to the issue queue. Like every red team (goal 12), it must show each flaw, not argue it: a quote, a reference entry, an event. **Measured before it is trusted:** on the five finished runs with flaws Claude found by hand (site as author, dates missing, venue header as title, "Section 5", the stalled search, the one-row table, duplicated sources, repeated examples), how many it finds and how many it invents. About $1. Kept if it finds most of them with few inventions (goal 15).
2. **Scope proposal.** The finder already writes the questions (threads) from the topic; show them at a new pause before the search, and let the user untick. Code only, no new model call.
3. **Gap filling and fetch requests.** For each question no source covers after the search: ask the planner for the best-known works on it by name, look each up by title (arXiv, OpenAlex, then the web), and add what it finds as a suggestion; what can't be downloaded becomes a fetch request with its link and file name. Measured on the next two runs: sources Claude adds by hand (target 0) and coverage.
4. **The issue queue.** A file per framework finding (`~/agentswarm-data/issues/`), with the run, the evidence and the red team's diagnosis; `agentswarm issues` lists them. A developer (Claude in a session, or the user) works from it; the fix names the issue. Later, perhaps, a coding agent proposes the fix as a branch for a person to accept; never merged by the framework itself.

## How we know it worked

Claude's interventions per run, counted from the runs' `NOTE-by-claude.md` files (today 1-3 per run), go to zero: **three runs in a row, started by the user in the studio in their own words, published with no edit by Claude**, an audit of at least 80% fully supported, and a red team that finds no structural flaw left. Claude's work then shows up only as commits that close issues from the queue.

## Open questions for the user

- The four pauses above: is approving the scope one pause too many? It can be folded into the source approval (questions and sources on one page).
- Should the fetch requests ever include paid access (a paper behind a paywall the user may have through a library), or only free copies?
