# Best-practice review (2026-10-10)

Every mechanism in AgentSwarm, held against established practice. Asked for by the user after 2026-10-09, when several of Claude's decisions turned out not to follow practices already written down in [RESEARCH.md](../RESEARCH.md). Sources are in RESEARCH.md unless linked here. Status: **aligned**, **gap** (with the proposed fix), or **fixed** (during this review).

## Summary

| # | Mechanism | Status | Proposed fix | Cost |
|---|---|---|---|---|
| 1 | Builder context | Aligned (since 2026-10-09) | — | — |
| 2 | Early stop for reading | Fixed: removed | — | — |
| 3 | Routing | Gap | Choose by expected cost per success; no hand-written order | Free (code) |
| 4 | Cascade | Gap (same as 3) | Climb to the next-best model not yet failed on this step | Free (code) |
| 5 | Decomposition | Gap | Also split a step when every model has failed it (ADaPT), instead of escalating to a person | Free (code) |
| 6 | Retries and repairs | Partly measured | Record repair against fresh attempt in the ledger; decide by data | Free (code) |
| 7 | Cost accounting | Gap | Refuse a model entry without a cached-input price where the provider caches | Free (code) |
| 8 | Progress measurement | Gap | Every run reports the spec pass rates (visible and held-out) and cost per example gained | Free (code) |
| 9 | Product hygiene | Gap | A check that fails on stray files (debug_*.py and the like) outside the package and tests | Free (code), then a run to clean up |
| 10 | Experiments | Fixed: policy | No paid comparison runs; the router learns in real runs; one-step trial for new configs | — |
| 11 | Checks, gates, ratchet, held-out tests | Aligned | — | — |
| 12 | Blackboard and decoupled teams | Aligned | — | — |
| 13 | Jev's thresholds | Aligned | — | — |
| 14 | Builders' tools | Aligned | — | — |
| 15 | Supervisor | Fixed (2026-10-09) | — | — |

Suggested order: 8 first (every later decision needs the measurement), then 3 and 4 together, 7, 5, 9, 6.

## 1. Builder context

- **Practice:** mask old tool outputs, keeping the last 10; as good as LLM summaries and simpler, halves cost on SWE-bench Verified (Lindenbauer et al., NeurIPS 2025). Anthropic: compaction, notes outside the context, sub-agents with clean contexts.
- **We do:** the last 10 tool results kept, older ones replaced by a note, cleared in batches past 40k tokens (60% of a local model's window) so the prompt cache breaks rarely.
- **Gap:** none known. Replaying our traces: 42% fewer tokens sent, 51% on long attempts; the saving in dollars is smaller (most re-sent tokens were cached) and is measured on the next run.

## 2. Early stop for reading (fixed: removed)

- **Practice:** none found; added on 2026-10-09 from one failure.
- **We did:** refuse reads after 20 calls without an edit.
- **Finding:** in our own traces, 17 of 65 successful attempts read 20 times or more before their first edit (median 14, at most 65). The rule would have blocked a quarter of the successes. Removed (commit 1cac19f).

## 3. Routing

- **Practice:** cost-aware routing: choose the model that minimizes expected cost subject to quality, with costs and success rates learned online (FrugalGPT; bandit routers with budget constraints, RESEARCH.md section 9). New models are explored, with exploration of expensive ones bounded.
- **We do:** Thompson sampling on success rates per call kind, difficulty band and model, which is right; but "cheapest" is the order written in the config. On 2026-10-09 that order was wrong (DeepSeek before Sol, at about 7 times Sol's cost per merged step).
- **Fix:** record each attempt's cost in the ledger; per band, pick the model with the lowest expected cost per success (a sampled cost per attempt divided by a sampled success rate). Models become a set in the config. Exploration of a model above a price limit needs a record first, or is capped per day.

## 4. Cascade

- **Practice:** cascades move to a stronger model when a reliable signal says the answer isn't good enough (FrugalGPT); the signal is the hard part.
- **We do:** the signal is good (the checks and tests). The next model is the next one in the config's order.
- **Fix:** with 3, after a failure the router picks again among the models that haven't failed this step.

## 5. Decomposition

- **Practice:** decompose as needed: try the task, and split it only when the executor fails, recursively (ADaPT, Prasad et al., NAACL 2024: up to 28-33 points higher success than plan-first methods, because a plan fails when any one sub-task can't be done). Up-front planning helps where the order of work matters.
- **We do:** split up front when Jev judges a task too big (Sonnet plans, a second level if needed). A step that every model fails is escalated to a person.
- **Fix:** keep up-front planning (the restructuring needs its order), and add the ADaPT path: a step that has failed at the strongest model is split by the planner instead of escalated, with Jev's progress guard (each child clearly smaller). Escalate only when splitting doesn't help.

## 6. Retries and repairs

- **Practice:** self-repair is worth it only if it beats fresh samples at the same budget; the gains are often modest, larger with strong feedback (Olausson et al., ICLR 2024).
- **We do:** a failed check keeps the work and reports what broke (repairs: 2), then fresh attempts on a stronger model. Our feedback is strong (test output), and keeping work was measured once (the first run threw away 150 passes).
- **Fix:** small: settle repairs in the ledger as their own outcomes, so repair against a fresh attempt is decided by data, per model.

## 7. Cost accounting

- **Practice:** count what is billed; where a provider doesn't report it, estimate with the full price list, cached input included.
- **We do:** OpenRouter's billed cost when reported; otherwise our estimate from the config's prices. Sol had no cached-input price and was counted about 5 times too high.
- **Fix:** a model entry for a provider that caches (OpenAI, Anthropic, OpenRouter) must name its cached-input price, or the config is refused; every report says which figures are billed and which are estimated.

## 8. Progress measurement

- **Practice:** track the objective metric continuously and against held-out data (evaluation hygiene, RESEARCH.md section 13).
- **We do:** held-out tests exist, but pass rates are computed by hand after a run; on 2026-10-09 Claude didn't measure them until asked.
- **Fix:** every run ends with the definition-of-done score of each level (visible and held-out pass rates) and the cost per example gained, on the board and in `agentswarm status`. This is the "what it bought" line of every report.

## 9. Product hygiene

- **Practice:** the definition of done includes the state of the repository, enforced by checks (lint, a clean tree), not by reminders (Phase 9).
- **We do:** the CommonMark product carries 16 stray scripts at its root (debug_*.py, temp_debug.py, test_ex93.py and others) that builders left behind; nothing checks for them.
- **Fix:** a hard check in the gate that fails on files outside the package, its tests and docs; then one cleanup step.

## 10. Experiments (fixed: policy)

- **Practice:** a bandit router learns about models while doing the real work; separate comparisons repeat that work.
- **We did:** about $20 of about $43 went to paid comparison runs, and about $10 to runs that merged nothing.
- **Now:** no paid comparison runs; a new model joins the router's set; a new configuration is tried on one step first; every paid run has a one-line plan the user approves.

## 11. Checks, gates, ratchet, held-out tests (aligned)

- **Practice:** verification by tests, regression gates in a merge queue, held-out tests the producer can't see or change (agents game their checks, RESEARCH.md section 12; MAST: missing or incorrect verification is one of the three main causes of multi-agent failures, Cemri et al., NeurIPS 2025).
- **We do:** exactly that: hard checks at every gate, a ratchet (what passed must keep passing), a merge queue, held-out tests outside the producer's reach. On 2026-10-09 no merge broke a passing example.

## 12. Blackboard and decoupled teams (aligned)

- **Practice:** MAST finds most multi-agent failures come from system design and agents misaligned with each other, not from the models; decoupled roles with explicit, typed hand-offs reduce both. Event sourcing gives an audit trail and crash safety.
- **We do:** teams share only the event-sourced board, with typed events; producers, critics and referees are separate teams; outcomes are settled by an observer, never by the team that made the call.

## 13. Jev's thresholds (aligned)

- **Practice:** selective prediction: act alone only where measured precision meets the target, with a confidence bound for small samples; escalate the rest.
- **We do:** thresholds from the decision log with a target precision and the 95% Wilson lower bound; a question goes active only with enough answers; new questions run in shadow mode first.

## 14. Builders' tools (aligned)

- **Practice:** an agent-computer interface designed for the model: partial reads, search, edits by replacement, short outputs, guards against loops (SWE-agent, Yang et al. 2024); a repository map up front (Aider).
- **We do:** all of these, plus a countdown of calls left; sandboxed, without network.

## 15. Supervisor (fixed on 2026-10-09)

- **We did:** counted hours between runs as stalls and flagged 15 queued steps.
- **Now:** only time while a run is active counts, and a step waiting its turn isn't stalled.

## Sources added by this review

- Prasad et al., ADaPT: As-Needed Decomposition and Planning with Language Models, Findings of NAACL 2024: [arXiv 2311.05772](https://arxiv.org/abs/2311.05772)
- Olausson et al., Is Self-Repair a Silver Bullet for Code Generation?, ICLR 2024: [arXiv 2306.09896](https://arxiv.org/abs/2306.09896)
- Cemri et al., Why Do Multi-Agent LLM Systems Fail?, NeurIPS 2025: [arXiv 2503.13657](https://arxiv.org/abs/2503.13657)
