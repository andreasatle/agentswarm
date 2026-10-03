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
