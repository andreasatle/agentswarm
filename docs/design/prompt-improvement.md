# Prompt improvement from failed steps (priority 4, 2026-11-11)

Priority 4 of the list agreed with the user on 2026-11-11: improve a prompt from the steps it got wrong, in the style of GEPA (Agrawal et al. 2025, docs/essays/learning-from-our-own-runs.md), each change accepted only on held-out data. The run record (priority 3's prerequisite, docs/design/run-record.md) ties every verdict to its prompt version, so a change can be measured afterwards on real runs too.

## Which prompt first: the claim checker

- **It is the only step with labels.** The 50 statements of the sleep tutorial were labelled with care (priority 1), so its errors are known, not guessed; every other step is judged by another model or a check.
- **Its errors cost on both sides.** It passes about one wrong statement in ten, which caps a reviewed document near 92% supported, and 55% of its flags are fine, each one paid for with a ruling by the referee, the most expensive part of a review ($0.53 of $0.97 on the sleep tutorial).
- **The errors have a visible pattern** for a reflection model to find: it misses glosses the source doesn't give ("the deepest stage") and flags definitions of terms, which the labelling rule says are not claims about the source.
- **It is cheap to evaluate:** one Haiku call per statement.

The writer's prompt would cut errors at the source, but it can only be scored through checkers that miss a third of the problems; it comes after the checker is measured and improved.

## Method

- **GEPA's own implementation** (the `gepa` package), not a rewrite: an adapter runs our `ClaimChecker` with a candidate prompt through our worker and scores it against the labels; the reflection model is our worker too, so the provider stays a config choice and LangChain stays in `llm/`. The candidate is the checker's instructions only; the answer's schema stays fixed, so the typed output and the record don't change.
- **Score:** per statement, 1 when the verdict agrees with the label on supported versus not, else 0, with a written reason from the label for the reflection to read (the misses and false alarms are what it learns from). Both kinds of error weigh the same, since both cost (quality and the referee).
- **Train: the 50 sleep statements. Held out: a new set from another document**, the LLM-scaling analysis before its review (another topic, web sources instead of papers), about 40 statements sampled at random, labelled by Claude blind to every verdict with the same written rule, 10 spot-checked by the user. A gain on the sleep statements alone could be the prompt learning one document.
- **Accepted only if** the held-out agreement improves beyond the noise of three runs of each prompt (temperature 0 still varies), and misses don't rise. Otherwise the result is reported and the prompt stays.
- **Models:** the task model is the checker's (Haiku 5.5): prompts are tuned per model. The reflection model is a stronger one of another family (GPT-6.1 Sol), as GEPA uses a stronger reflector.

## Cost

Labelling is free (Claude's time). The run: GEPA with a budget of about 600 checker calls (a dozen passes over the 50), about 20 reflection calls, then three held-out passes each for the old and the new prompt; about $3, capped at $5.

## Result (2026-11-12): not accepted

GEPA ran until the day's spending limit ($1.45, 2026-11-11): six candidates, the best 0.90 on the 50 sleep statements against 0.76 for the current prompt (the training set was also GEPA's validation set, so 0.90 is optimistic). On the 78 held-out LLM-scaling statements, three runs each ($0.61):

| prompt | accuracy | missed (of 22 problems) | false alarms (of 56 fine) |
| --- | --- | --- | --- |
| current | 0.79-0.82 | 1-2 | 12-15 |
| GEPA's best | 0.82-0.85 | 1-4 | 9-11 |

- **Rejected by the rule set in advance:** the accuracy gain (+0.03) is about the spread of three runs, and misses rose (1.3 to 2.7 on average). It traded misses for fewer false alarms; a miss is a wrong statement in the published document, a false alarm costs a referee ruling, so the trade is the wrong way.
- **The prompt grew from 470 to 13,600 characters,** about 3,400 more input tokens per check: a cost on every call even if it had won.
- **What the held-out set says about the current checker:** on another document it misses few problems (about 1 in 15) but a quarter of its flags are false alarms; the false alarms, not the misses, are where it costs.
- The current prompt stays. The GEPA scripts stay in the scratch folder, not the repo.
