# Phase 4: Jev active

Status: **in progress** (decided on the recommendations, as for Phase 3). Proposal sections: §11.5
(thresholds), §12.3 (shadow mode), §12.4 (fallback), §13.3 (cascade routing), §15 (evaluation).

**Exit criterion (§19):** gate error rates measured against outcomes and within agreed bounds.

The bounds, proposed here since the proposal leaves them open, for the gate out of the lowest level
with Jev active, measured on an evaluation run:

| Error | Meaning | Bound |
| --- | --- | --- |
| false promotion | promoted, but a ground truth says one of its questions is "no" | ≤ 5% of promotions |
| false rejection | rejected, but every ground truth says "yes" | ≤ 10% of rejections |
| escalation | neither, a person decides | ≤ 30% of decisions |

With samples of 50–100, each rate is reported with a 95% Wilson interval, so "within bounds" is
honest about how little 60 decisions can prove.

## Where we start

- Every decision point is shadow or active together (`jev.mode` per project); the proposal wants each
  point promoted to active only after its own calibration (§12.3).
- Thresholds are the §11.5 defaults (pass 0.9, fail 0.2). Phase 2 showed `complete` under-confident
  (right whenever it decided, but 38% would escalate) and `in_scope` mixing in test quality.
- A Jev outage in active mode escalates everything; there are no retries.
- One model per team.
- The model executor solved every greeter task: model runs give no negative examples.

## Design

### 1. Active per decision point

`jev.active: [patterns]` lists the decision points that act (glob patterns over point names such as
`gate:raw->experimental`, `relevance:*`, `size:raw`); every other point stays shadow. `jev.mode` remains
as the default for points no pattern names. A `Judgement` resolves its mode per point when it asks, so
one project can run its gate active while relevance filters are still being calibrated. Decisions record
the mode they ran in, as before.

### 2. Reliability: retries and a fallback

- `JevClient` retries timeouts, 429 and 5xx answers with exponential backoff (3 attempts by default);
  other errors fail at once.
- `jev.fallback: <model name>` adds an `LLMDecider`: a language model answers each question as
  yes / no / unsure, mapped to p(yes) = 0.95 / 0.05 / 0.5. These answers are not calibrated, so decisions
  record `model: "llm:<name>"`, and the calibration report and the tuner leave them out. Without a
  fallback, active points escalate when Jev is unreachable, as today.

### 3. Thresholds from data

`agentswarm tune DECISIONS [--precision 0.95] [--min-support 20]` reads a decision log and, per decision
point and question, picks the lowest pass threshold whose decided-yes answers are right at least
`precision` of the time, and the highest fail threshold likewise for no, among decisions with an
outcome. It prints them as YAML for the gate's `thresholds:`, with coverage and the Wilson lower bound,
and says which points have enough support to go active. Tuning only reads Jev's own decisions for the
current wording of the judge (see 6).

### 4. Cascade routing (§13.3)

`model_tiers: [small, large]` on a team replaces `model:`. The executor starts each artifact on the first
tier and moves up a tier after each rejection, staying on the last. `CommitPushed` records the model
that made the commit, so an evaluation can say how often the cheap tier passed the gate. Jev choosing
the tier (a Choice question) waits for Choice policies, which no judge needs yet.

### 5. Measuring gate error rates

`GateAudit` compares each active gate decision's verdict with the ground truths of its questions:
promote while any truth is "no" is a false promotion; reject while all truths are "yes" is a false
rejection; escalations are counted. `agentswarm audit DECISIONS` prints the three rates with Wilson
intervals against the bounds above.

### 6. Better data

- **Judge versions.** Decisions record a hash of the judge's questions (text and order). Changing a
  question's wording starts a new version; the report, tuner and audit use one version at a time, so old
  data never silently calibrates new wording.
- **Sharper `in_scope`.** The question names what to ignore: "Does the change modify only what the task
  asks for? Judge which files and behaviour change, not the quality of the tests." (§12.2: one narrow
  property per question.)
- **A harder suite.** Ten more greeter tasks that a small model gets wrong some of the time (parsing,
  formatting rules, error handling across functions), with references and hidden tests, so model runs
  contribute negative examples too.
- **Active evaluation.** `agentswarm evaluate --active PATTERN` runs a suite with those points active,
  which is what the exit criterion measures.

## Steps

1. Per-point active modes.
2. Jev retries and the LLM fallback decider.
3. Judge versions; sharper `in_scope` wording.
4. `ThresholdTuner` and `agentswarm tune`.
5. `GateAudit` and `agentswarm audit`; `evaluate --active`.
6. Model tiers.
7. The harder suite.
8. Runs: shadow evaluation of both suites → tune → active evaluation → audit against the bounds.

## Not in Phase 4

- Score and Choice policies, and the judges that need them (`CandidateToProduction`, Jev tier routing):
  no judge needs them yet. To the backlog.
- Explicit revert of merged artifacts; per-item checks and the explainer: still not needed.
- Live decomposition becomes possible with step 1 (`size:*` active); it is exercised in step 8 if a task
  is large enough to split.

## Decisions made during implementation

**Step 1 (active per decision point).** `jev.active` holds glob patterns (`fnmatchcase`) over point
names; `ModeTable` resolves a point's mode, and `Judgement.mode_for(point)` replaces the single
`Judgement.mode`. Places that decide what to do without an answer (the gate and the join when Jev is
unreachable) ask the table for their own point. Existing configs are unchanged: no patterns means every
point uses `jev.mode`.

**Step 2 (retries, fallback).** `JevClient` retries transport errors (timeouts, refused or dropped
connections) and 429/500/502/503/504 with doubling waits (3 attempts, 0.5 s first); anything else fails
at once. The HTTPS check is not an HTTP error and is never retried. `LLMDecider` sends the questions and
the state to a worker that answers yes/no/unsure per question (a fixed output model, so any spec of
Noul questions works); its decisions are marked `llm:<model>`, and the calibration report skips them.
Gates are synchronous inside the project's event loop, so the decider runs the async worker on a
helper thread. `FallbackDecider` logs and falls back when the primary raises `DeciderError`;
`jev.fallback` names the model.

**Step 3 (judge versions, sharper wording).** `Judge.version` hashes each question's field name, wording,
criteria and levels (not the class name); decisions record it as `judge_version` (`""` in older logs).
The calibration report groups by decision point, judge version and question. `in_scope` now reads "Does
the change modify only what the task asks for? Judge which files and behaviour it changes, not how good
its tests are." This starts a new version of `ChangeReadiness`: the Phase 2 data remains in the logs as
the unversioned wording.

**Step 4 (tuning).** `judging/tuning.py:ThresholdTuner` works on `LabelledAnswers` (the calibration
report's grouping, now shared): per point, judge version and question, the lowest pass and highest fail
threshold reaching the target precision with at least `min_decided` answers on that side, the pass
threshold kept strictly above the fail threshold (with discrete data they can otherwise meet). Ready =
current wording, both thresholds, and `min_support` answers. `agentswarm tune` prints each question and a
`thresholds:` YAML line per gate. On the Phase 2 log it reports everything as old wording (correctly:
`in_scope` changed in step 3). Thresholds for non-gate points (relevance, size, join, drift) still use
the defaults; making them configurable waits until one of them has enough data to tune.

**Step 5 (audit).** `judging/audit.py:GateAudit` audits active decisions at one gate point (shadow ones
never acted). A promotion is wrong as soon as one question's truth is "no"; a rejection is wrong only if
every question's truth is "yes"; anything the truths don't settle is counted as unknown. Each rate is
compared with its bound through the 95% Wilson upper limit, so an empty or tiny sample is never "within
bounds". `agentswarm audit DECISIONS` prints it; `agentswarm evaluate --active "gate:*"` runs a suite with
those points active and prints the audit after the calibration report.

**Step 6 (model tiers).** `model_tiers` replaces `model` on a team (setting both is an error). The
executor's tier is the number of rejections in the artifact's history, capped at the strongest tier;
`CommitPushed.model` records the tier's model name (`None` for single-model teams built directly). Other
teams take one model, as before; routing by a Jev Choice question waits for Choice policies.

**Step 7 (harder suite).** `examples/greeter/evaluation/hard/suite.yaml`: ten tasks with exact rules and
edge cases (Roman numerals, ordinals, a centred banner, strict parsing, list joining, time-of-day
boundaries, counting with a pattern, careful name capitalisation, formal initials with particles, a
template registry class). Same project, layout and safeguard as the first suite: all 24 reference
solutions pass their hidden tests and the seed fails them.

**Step 8 (runs), first half.** Shadow evaluation of both suites with the new `in_scope` wording: 93
decisions (three model runs on the hard suite ended without a decision: two without a structured answer,
one with no change; one more failed its hidden tests, the first negative example from a model run). All
three questions now separate cleanly (`complete`: every "no" ≤ 0.32, every "yes" ≥ 0.66; `in_scope`:
"no" ≤ 0.20, "yes" ≥ 0.37; `tests_relevant`: near perfect). **Tuner fix:** the greedy fail threshold
(highest t still ≥ 95% precise) could include a correct answer for no gain; thresholds now snap to the
nearest answer of their own kind, which never lowers precision or the coverage of correct answers, and
leaves unobserved values to escalate. Tuned: complete 0.63 / 0.33, in_scope 0.37 / 0.20, tests_relevant
0.84 / 0.03, all ready; written into `examples/greeter/evaluation/project.yaml`.

**eval-3 (gate active; in-sample, since the thresholds were tuned on these tasks).** 108 active decisions,
20 not settled by outcomes. False promotions 1/25 = 4% (upper bound 20%, so not within 5%); false rejections
0/62 (upper bound 6%, within 10%); escalations 1/108 (upper bound 5%, within 30%). The false-promotion
bound fails on sample size and is in-sample. Phase 4 stays open until a cohort-separated re-audit and a
held-out task set, after Phase 7 (see CODEX_AUDIT.md F10, F11).

