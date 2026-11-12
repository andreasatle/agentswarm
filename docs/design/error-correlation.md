# Error correlation (priority 3, 2026-11-11)

Priority 3 of the list agreed with the user on 2026-11-11: measure how far our models' errors go together. It decides what a second checker, a vote or an escalation to another model can buy: independent errors multiply away, shared ones don't (Kim et al. 2025; Knight and Leveson 1986; docs/essays/verification-without-ground-truth.md). Free: no model calls, only data we had.

Two sources: the five checkers on the 50 labelled statements (labels from priority 1, docs/design/documents.md), and the outcomes of routed calls on every board, through the run record (docs/design/run-record.md). Data and scripts are kept beside the run, in ~/agentswarm-runs/how-sleep-affects-memory/calibration-2026-11-11 (`correlation.py`, `escalation.py`).

## Checkers, against the labels

18 of the 50 statements have a problem (not fully supported); 32 are fine. An error is a verdict that disagrees with the label: a miss or a false alarm. Unweighted counts on the 50 (the stratified sample over-represents flagged statements; the pattern, not the levels, is the point). phi is the correlation of two checkers' error indicators; "independence" is the overlap expected if they were independent.

| pair | errors A, B | both wrong | independence | phi |
| --- | --- | --- | --- | --- |
| Haiku whole part / Haiku retrieved passages | 21, 19 | 18 | 8.0 | 0.84 |
| Haiku whole part / Gemma 4 26B whole part | 20, 15 | 11 | 6.2 | 0.43 |
| audit (DeepSeek V4 Pro) / Gemma | 13, 15 | 8 | 4.1 | 0.40 |
| audit / Haiku retrieved | 13, 19 | 8 | 4.9 | 0.29 |
| audit / Haiku whole part | 13, 21 | 8 | 5.5 | 0.23 |
| facts reviewer (GPT-6.1 Sol) / Gemma | 10, 15 | 5 | 3.1 | 0.21 |
| facts reviewer / audit | 11, 13 | 2 | 2.9 | -0.09 |
| facts reviewer / Haiku whole part | 11, 21 | 1 | 4.6 | -0.35 |

- **The same model on the same task is nearly one checker.** Haiku's two arms (whole part, retrieved passages) are wrong together on 18 of their ~20 errors: a second Haiku arm adds almost nothing.
- **Other families overlap moderately** (phi 0.2-0.4, about 1.5 to 2 times the independent overlap). A second family is worth more than a second arm, but not as much as independence would promise; this is the 60% figure of Kim et al. on our own data.
- **The facts reviewer's errors are a different kind:** it rarely flags (11 misses, no false alarms) while the checkers over-flag, so their errors don't overlap at all. The negative phi is a difference in strictness, not independence of judgement; on misses alone it is near zero.
- **Misses, the errors that matter for quality:** of the 18 problems, the audit misses 4, Haiku 3 and 2, Gemma 6, Sol 11. Only one problem (item 17, a borderline gloss: the source doesn't say the nap was too short) is missed by all four flagging checkers; the three reversals are each caught by at least two. 18 problems are too few to put a number on miss correlation.

## Routed calls, across the boards

The outcomes of routed calls (a commit promoted or rejected, a split that held or not) on 32 boards with outcomes: 795 outcomes, of which 693 name their model (102 old outcomes have no spend tied to them). Overall 33% fail. Then for each artifact, the next attempt after a failure:

| next attempt after a failure | fails |
| --- | --- |
| the same model again | 77 of 95 (81%) |
| another model | 45 of 61 (74%) |
| any attempt, no failure before (base) | about 33% |

- **Failures concentrate on tasks, not on models.** After a failure the next attempt fails about three times out of four whichever model makes it, against one in three in general. Most of what makes an attempt fail is shared by every model we route to: the task's difficulty, an unclear spec, a check that is wrong. Switching model helps a little (74% against 81%); within the noise of these counts.
- **Confounded, so a lower bound on what is task-level:** escalation mostly goes from cheap to strong, a model that "fails after another failed" is often the strong one on an artifact already shown to be hard, and some failures are rejections by a check that was itself wrong (the retry can't pass either). The old boards hold no way to tell these apart.

## What it means for the design

- **Checks:** a second opinion should come from another family, never another arm of the same model; and even then the overlap is real, so label-free combination (Dawid-Skene) will overrate agreeing checkers. The labelled calibration set stays the way to measure them (priority 1).
- **Routing:** the cascade already moves to a model that hasn't failed the step (teams/routing.py), which the data supports. But the router scores the next model with its general success rate, while after a failure the measured rate is much lower (about 26% against 67%), so it overestimates the cheap options in a cascade. A failure is evidence the step is hard. Not changed now: the counts are small and confounded; parked in BACKLOG.md with its trigger.
- **Next measurement:** checker against audit on a whole document needs their per-statement verdicts on the board; the run record that wrote them was removed (2026-11-12, docs/design/run-record.md), so this waits for a decision that needs it.
