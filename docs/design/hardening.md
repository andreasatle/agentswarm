# Hardening with red teams: design (built 2026-10-15)

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

## First campaign on jq (2026-10-15, $4.80 including a failed start): 87% to 92% of the held-out tests

**Result:** one whole round, section focus only (11 attacks, one per manual section or part). 25 problems reported, 21 confirmed by the referee against the manual, 20 fixed and merged. Held out 109/125 to 115/125; visible 550/550 to 765/765, the red teams' tests now part of the protected checks and all passing. It stopped at the day's $5 budget, which included $0.31 for a first attack by GLM-5.3 that reasoned past its output limit and returned nothing.

**What the red teams found, from the manual alone:** documented builtins the builders never wrote (`arrays`, `booleans`, `normals`, `finites`, `strings`, `halt`, `halt_error`), number precision rules, variable keys in objects, and more; several match the held-out failures seen on 2026-10-12, which no agent saw.

**Fixed on the way (framework):** the router now pools each model's record across difficulty bands (a model failing everywhere no longer gets a fresh chance in each band); a branch that conflicts with the moved base is redone there, keeping its acceptance tests, instead of waiting for a person (several fixes from one attack touch the same file).

**Cost:** about $0.24 per merged fix, about $0.80 per held-out test gained. Still to measure: the other three focuses (boundaries, rejection, mutation) and further rounds.

## Second round on jq (2026-10-15, $4.75): the other three focuses, held out unchanged at 92%

**Result:** 32 attacks (boundaries 11, rejection 11, mutation 10; the budget ended the 11th mutation attack). 25 reported, 10 confirmed, 9 fixed and merged; one more section fix from round 1 merged too. Visible 765 to 817, all passing; held out 115/125, unchanged.

**Per focus:** boundaries found the most (20 reported, 8 confirmed, 8 merged, e.g. `abs` at the binary64 precision limit); rejection 3 reported, 1 confirmed, 0 merged; mutation 2 reported, 1 confirmed, 1 merged, and its 20 mutants per attack make it the slowest. The section focus remains the one that moved the held-out score.

**A wrong report got through the referee:** the manual says `a op= b` is equivalent to `a |= . op b`, read literally that makes `.a += .a` on {"a":2} an error; jq 1.7.1 evaluates the right side against the original input ({"a":4}), as the visible test jq.test:1086 shows. The red team and the referee both read the manual literally; the ratchet refused every fix (it would break 1086 and 1203), so nothing wrong was merged, but the task and its split cost the builders about $0.5. **Guard built (2026-10-15):** when every builder model fails a red team's fix task, the Hardener checks the task's branch in plain code before the planner splits it: if the last change made the red team's test pass and broke protected tests the product passed, the referee looks again with those tests (`Referee.RECONSIDER`: could a correct product pass both? if not, the existing tests show what the spec means). Withdrawn: the task and its steps are dropped (`Reconsidered` event), and later red teams see the withdrawn claims so they don't report them again. No conflict found: the report stands without a model call. Cost: one test run on the branch and at most three referee calls per failed fix; on this case it would have saved the split and its steps, about $0.2, and kept the wrong report from coming back. Checking before the task exists isn't possible: the conflict shows only once a change makes the red team's test pass.

**What it says:** the new focuses find real failures the visible tests miss (52 new protected tests), but not the ones the held-out tests check; the held-out gap left (10 tests) isn't where boundaries, rejection and mutation look. Further rounds of the section focus, or a deeper attack on the sections with held-out failures, are the next measured step.

## Third run on jq (2026-10-16, $4.74): section rounds 2 and 3, held out 92% to 93%

**Result:** 18 section attacks (round 2 whole, round 3 seven of eleven before the budget ended it). 35 reported, 14 confirmed, 14 fixed and merged (Oniguruma regex syntax among them). Visible 817 to 946, all passing; held out 115 to 116 of 125. The guard (`Reconsidered`) never fired: no fix failed on every model.

**Diminishing returns on the held-out score:** the section focus still confirms about 7 problems per round, real ones by the manual, but they're mostly not what the held-out tests check. Round 1 gained 6 held-out tests for $4.80; the next two runs gained 1 for $9.50. The 9 left: `debug/0` (the manual's I/O section, not attacked), `@base64d` without padding, slice assignment, NaN to integer, number literal precision (2), `split` with flags, identity on a large number, a path case. Each is narrow: an attack would have to land on that exact case.

**Over the campaign:** held out 87% to 93% for $14.30, about $2.40 per held-out test, and 396 new protected tests from the red teams (550 to 946 visible).

## First round on YAML without labels (2026-10-16, $5.04): hidden score unchanged at 97%

**A false start first:** the Hardener waited for the builders forever: YAML's first task was split into steps, one of them escalated, and the open parent counted as work in progress. Fixed: a task split into steps does no work itself.

**Result:** 17 attacks (section 9, boundaries 8) before the budget ended the round. 16 reported, 4 confirmed, 1 fixed and merged (a byte-order mark inside block scalars), 3 still open. Hidden 663/681, unchanged. Visible 170 to 327, but mostly the check writer's tests for split steps, not the red team's.

**Where the money went:** the project sizes tasks before building (Jev's size question active), so each fix task, one failing test, was split into 2 to 3 steps before any builder tried it, and the check writer (Sonnet, 64k) wrote checks for each step: about $3.30 of the $5 went to 8 steps from 3 fixes. Fixed: a red team's fix task is sized as fitting one branch when it is made; it is split only if every builder model fails it (ADaPT, as the executors already do).

**What the red teams found:** narrow cases (block scalar indentation at the root, a BOM in a block scalar, a 5000-digit `%YAML` version, a BOM-less stream starting with a non-ASCII character). The 18 hidden failures are different: valid documents the parser rejects (anchors and tags in flow and block collections, indentation, tabs in a literal), in chapters that were attacked. The spec here has no examples, and the red teams test the rules one at a time; the hidden tests combine features.

## Second round on YAML without labels (2026-10-16, $4.81): combination focus, hidden 97% to 98%

**Result:** 26 combination attacks (two rounds over the 14 chapter parts). 31 reported, 4 confirmed, 3 fixed and merged (separation tabs after indentation, percent escapes in verbatim tags, a BOM before `---` after a block scalar; key order in a flow mapping still open), and the three fixes split in round 1 finished their steps (a split fix had counted as unmerged in the run's summary; it now counts once every step is merged): 7 of round 1 and 2's 8 fixes merged. Hidden 663 to 668 of 681, the first gain on YAML; visible 327 to 529.

**Where reports were lost:** 27 dropped, 12 because the quote wasn't the spec's words verbatim (a combination rests on rules from several places, and a report allows one quote), 15 by the referee. Two claims came back again and again from different chapters (plain scalars starting with `:` after a tag or anchor in flow, 17 mentions; empty tagged keys right before `:`, 13): the red teams don't see what was already rejected.

**Fixed (2026-10-16):** a report may quote up to three passages, each checked word for word, and the referee sees each in its context; the reports the referee rejects are recorded (`Attacked.rejected`) and the latest 20 are shown to every later attack with the referee's reason, to be raised again only with new evidence that answers it (a rejection can be wrong, and rare finds are the point).

## Combination focus on jq (2026-10-17, $4.72): held out 93% to 94%

**Result:** 17 combination attacks (one whole round of 11 sections and six of the next) before the budget ended it. 20 reported, 8 confirmed, 8 fixed and merged; held out 116 to 117 of 125; visible 946 to 996, all passing. The reconsider guard didn't fire (no fix failed on every model).

**What it says:** the combination focus works on a second problem, at about the section focus's later-round yield (8 confirmed per round, one held-out test per $5). On YAML it gave the first gain; on jq, after five rounds of other focuses, the remaining 8 held-out failures are narrow cases that any focus reaches only by chance. Over the jq campaign: 87% to 94% held out for about $19, and 446 new protected tests.

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
