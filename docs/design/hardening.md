# Hardening with red teams: design (draft for review, 2026-10-15)

## The question

The user's goal 12: set a goal, and the product is hardened toward it by iteration. The inner loop (planner, check writer, builder, ratchet) builds a product to the swarm's own checks; the hidden tests show what those checks missed (jq 87%, YAML without labels 97%, JSONPath 99%). Hardening is an outer loop that keeps finding what the checks missed, turns each confirmed failure into a new protected check, and has it fixed, until the attackers stop finding anything. The question it answers: **does the hidden score rise as the red teams attack, and what does each fixed failure cost?**

## What the evidence says

| Finding | Source | Consequence for the design |
| --- | --- | --- |
| A model used as a generator of inputs, looping on what it found, finds real bugs that hand-built fuzzers miss: 98 bugs in GCC, Clang, Z3 and others, 64 confirmed as new | [Fuzz4All, ICSE 2024](https://arxiv.org/abs/2308.04748) | Red teams generate attacking inputs from the spec, not from the code alone, and loop on what they found. |
| Tests that are rewarded for telling faulty code from correct code improve both the tester and the coder, with no ground-truth code | [CURE, NeurIPS 2025](https://arxiv.org/abs/2506.03136) | A red team is measured by failures that are confirmed and fixed, not by how many tests it writes. |
| Feeding surviving mutants back into the prompt makes model-written tests catch more faults (93.6% mutation score; up to 28% more faulty code detected) | [MuTAP, 2023](https://arxiv.org/abs/2308.16557) | One red team is guided by mutation testing: each surviving mutant is a lead to a behaviour no check covers. |
| Model-written assertions are often wrong (28-58% of ChatGPT's), the more so on the inputs most likely to find bugs | [Unit test generation, 2023](https://arxiv.org/abs/2312.10622); [UTGen, 2025](https://arxiv.org/abs/2502.01619) | A red team's failing test is a claim, not a fact: it must quote the spec, and the referee rules before it becomes a check. |
| Diversity must be engineered; one broad critic misses the same things each time (RESEARCH.md P10) | RESEARCH.md; [Knight and Leveson, 1986](https://www.kth.se/social/files/564df871f2765419e306178d/KnightLeveson.pdf) | Several narrow red teams on different evidence, each measured; the one that doesn't pay its way is dropped. |

## What exists, and what changes

The framework already has red teams (`teams/analyzer.py`): each writes pytest files in a scratch folder, runs them, and may report a problem only with a test that fails; claims without evidence are dropped. Findings above the lowest level become fix tasks (`control/router.py`). Three things don't fit hardening yet:

1. **They examine one merged change** (its diff), not the product against the spec. Hardening attacks the whole product, one spec section at a time, again and again.
2. **Their evidence isn't checked against the spec.** In a project without labels the red team's expected value can be wrong, like the check writer's.
3. **The failing test is thrown away.** It should become a protected check of the fix task and stay in the product, so the set of checks grows: that is the hardening.

## The hardening loop

```
product merged at the level
  → campaign: one attack task per spec section and per red team focus (plain code, like the worklist)
  → red team attacks: writes and runs tests in a scratch folder
      a report = a failing test + the spec passage it rests on (quoted word for word)
  → plain code: the quote must be in the spec; the test must fail on the product
  → referee (another model family): does the test demand what the spec says?
      upheld → a fix task, its acceptance test = the red team's test (protected)
      rejected → recorded, dropped
  → builder fixes it; the ratchet merges (nothing else breaks)
  → the section is attacked again, until a round confirms nothing new
```

**The red teams,** each with one focus and its own evidence (narrow teams that exist are kept where they fit):

| Red team | Attacks | Evidence of a problem |
| --- | --- | --- |
| Spec section (contract) | every rule and stated behaviour of one section, including the parts no check covers yet | a test of a stated rule that fails |
| Boundaries | edge values the spec names: empty, limits, just inside and just outside | a test at a boundary that fails |
| Rejection | inputs the spec says are invalid and must be rejected | an invalid input that is accepted |
| Mutation-guided (MuTAP) | plain code mutates the product; each mutant that no check kills is a lead; the red team writes a test that kills it and that the spec supports | a test that fails on the mutant, passes on the product only if the product is right, and quotes the spec |

**Stopping:** a section is hardened when a round of all red teams confirms nothing new; the product when every section is hardened, or the budget is spent. Each round costs a few small model calls, so the budget, not a fixed count, bounds it.

**Measures, every round:** the hidden score (scored after each round, at no model cost); per red team: reports, confirmed by the referee, fixed and merged, hidden tests gained, dollars per confirmed failure. **No red team is switched off** (the user, 2026-10-15: a quiet team can still find rare failures that are well worth finding): in the first version every team attacks every section each round; if cost later demands it, a quiet team attacks less often but never stops, explore and exploit as the router does for models.

## The first experiment

**jq** (supervised: 100% of the visible tests, 87% of the held-out ones). The gap is functions and edge cases the manual documents but no visible test exercises: exactly what a spec-section red team should find, and held-out tests to measure it. The red teams get the manual (as the builders did), never the held-out tests. Success: the held-out score rises from 87%, with no visible test broken.

Then **YAML without labels** (97%), the self-supervised case.

## What gets built

| Part | Size | Exists? |
| --- | --- | --- |
| Campaign: attack tasks per spec section and focus, re-queued until a round confirms nothing (plain code, like the worklist) | medium | the worklist pattern |
| A red team mode that attacks the product, not a diff, given a spec section | small | `ProbingAnalyzer` and its toolbox |
| Reports that quote the spec; plain-code check of the quote; referee ruling before a fix task | small | the referee (`teams/referee.py`) |
| The red team's failing test as the fix task's protected acceptance test | small | acceptance tests and their protection |
| Mutation-guided leads | medium | `evaluation/mutation.py` |
| Scoring after each round; the per-team report | small | scoring exists |

About two days of work, no paid model calls; each part with tests, prompts in PROMPTS.md. Then one paid run on jq, capped at about **$5**, after a yes.

## Risks and guards

- **False reports flood the builders:** every report needs a failing test and a verbatim quote, and the referee rules before anything becomes a task.
- **Red teams that find nothing cost money (the October 7 smoke run: 90% of the cost, nothing found):** each team is measured per confirmed failure; rounds stop when a whole round confirms nothing new, and the budget caps the rest. Quiet teams are kept, since rare finds are the point of diverse attackers.
- **Overfitting to the red teams' own tests:** the hidden tests stay hidden and measure every round.
- **Fixes that break other things:** the ratchet, as everywhere.
