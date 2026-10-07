# Routing for every role

Status: **steps 1-2 done** (2026-10-07), step 2 of the order agreed with the user: (1) independent acceptance checks, (2) routing for every role learning from those checks, (3) the goldilocks comparison with the best assignment per role.

## The goal

Each role (plan, build, review, write acceptance tests) runs on the cheapest model whose measured success on that kind of call, at the task's difficulty, meets a target. The models: Claude Haiku and gemma4-e4b first, then Sonnet and Opus, then Qwen (COLLABORATION.md goal 5). Success is measured by checks the model under test can't write or change.

## What exists, and what is wrong with it

- **Only executors are routed.** Analyzers, the decomposer and the acceptance tester take one fixed model.
- **The executor grades itself.** It records success when its own checks pass and it reports itself complete, at the moment of the attempt. With an acceptance team those checks include the acceptance tests, which helps, but the critics' findings and the gate come later and never reach the ledger.
- **Jev is asked per team.** Each routed team would ask `CallProfile` about the same artifact again: more calls, and bands that could differ between roles on one task.
- **There is no truth for some roles inside a project.** Whether a review was right, or an acceptance test demanded the right thing, is known only where an answer key exists (probes, evaluation suites).

## Design

### 1. One profile per artifact

Jev answers `CallProfile` once per artifact, and a `Profiled` event (difficulty, attributes, decision) goes on the board. Every routed team reads it from the artifact's history; the first team that needs it and finds none asks. One difficulty per task means one band per task across all roles, so the outcomes of the roles can be compared and combined.

### 2. Every team can be routed

Any team that makes model calls takes `model_tiers` and `routing` in its config, as executors do now. The router (unchanged: Thompson sampling over the shared ledger, cheapest tier whose draw meets the target) gets the team's `CallKind`: produce for executors, critique for analyzers, plan for the decomposer, accept for the acceptance tester. Each team records its choice as a `Routed` event, which gains `call_kind`. A failure steps up a tier where the team already retries: executor attempts and the acceptance tester's second try. Analyzers and the decomposer make one call on the chosen tier.

### 3. Outcomes come from an observer, not from the team

Teams stop recording their own outcomes. A plain-code controller, `OutcomeRecorder` in `control/`, folds the board like any view: when an artifact's later events settle a routed call's outcome, it writes one `RoutedCall` to the ledger, and an `OutcomeRecorded` event so it is never written twice. The producer, critic and referee stay decoupled (RESEARCH.md P1, P12): nobody grades their own work, and a delayed outcome needs no team to wait for it.

What settles each kind of call, in a project:

| Kind | Success | Failure | Trusted because |
| --- | --- | --- | --- |
| produce | the attempt's commit passes the gate, acceptance tests included, and is promoted | rejected, escalated, or no change | the acceptance tests were written by someone else and restored before the commit |
| plan | every child is promoted and the parent passes its gate | a child is escalated, or the parent fails | weak on its own (the planner wrote the children's tasks), so plan outcomes also come from evaluation runs, below |
| critique | not settled in projects | | no truth about a review exists without an answer key |
| accept | not settled in projects | | likewise; a test that blocks correct work looks like a producer failure |

Evaluation runs settle more, from the hidden tests: produce and plan outcomes from whether the delivered result passes them. Probes settle produce, critique and accept, scored by plain code as now. Critique and accept are therefore learned from probes and evaluation suites only, until a project-side signal is shown to be trustworthy. The ledger's `source` keeps them apart, so a later analysis can check whether project outcomes and probe outcomes agree.

### 4. The ledger is seeded by probes before any routed run

A cell (kind, band, model) needs about ten outcomes before the router tells a 60% model from a 90% one; 3 kinds with probes × 3 bands × 2 models is 18 cells, about 200 probes. The qualification run (Phase 8 step 12) does this: produce, critique and accept probes for Haiku and gemma4-e4b, sampled by band from the inventory suite and EvalPlus (the hard band is thin: 13 EvalPlus tasks plus inventory levels 4-5). Its accept probes also give the acceptance checks the larger sample the 25-task experiment lacked. Sonnet and Opus follow, then Qwen.

### 5. The routed run

The inventory suite with every role routed over gemma4-e4b and Haiku (then adding Sonnet and Opus), compared with the best fixed setup so far (Haiku building against Sonnet's acceptance tests, 20/25), at equal cost (P8). The question: does the router find an assignment that solves as much for less, or more for the same?

## Targets per role (decided with the user, 2026-10-07)

0.8 for build, plan and review; **0.9 for acceptance writers**, because a wrong acceptance test blocks correct work and is charged to the producer, which also misleads the router about the producer. To be adapted when the data says so.

## Checking the acceptance tests themselves (the user's question, 2026-10-07)

Inside a project nothing yet checks whether an acceptance test demands the right thing; only hidden tests catch a wrong one. Two additions, measured before they act:

1. **Jev checks each acceptance test when it is written** ("Do these tests demand only what the task asks, with the right expected values?"), in shadow first. The accept probes know which tests were wrong (9 of 50 so far, more after step 3), so Jev's agreement with the truth is measured there; the check acts only once it is shown reliable.
2. **The producer can dispute a test.** A producer that says a test contradicts the task sends the dispute to an arbiter (Jev, or a person) instead of failing silently; the outcome is recorded for neither the producer nor the acceptance writer until the arbiter has ruled.

## Steps

1. `Profiled`, read by every routed team; `Routed.call_kind`; routing config for every team kind. Free.
2. `OutcomeRecorder` with the table above; the executor's own recording removed; evaluation runs settle plan and produce from hidden tests. Free.
3. Qualification probes, Haiku and gemma4-e4b, writing to the ledger. Haiku's share costs money: estimated from the accept probes ($0.43 for 25), about $3-5 for 200 probes; confirmed with the user before running.
4. The routed run on the inventory suite against the fixed baseline. Cost estimated from step 3's tokens, confirmed before running.
5. Sonnet and Opus added: probes, then the routed run again.
6. The Jev check on acceptance tests, in shadow, measured on the accept probes; the producer's dispute.

## Known limits

- An acceptance test that is wrong makes a correct producer fail; with probe-measured acceptance writers and a 0.9 target this should be rare, and the evaluation runs show how rare (acceptance verdict against hidden tests).
- Plan outcomes in projects are weak evidence; until parent-level acceptance tests exist (the acceptance tester skips umbrellas today), the planner is judged mainly by evaluation runs. Parent acceptance tests are a follow-up if plan routing proves to matter.
- Bands come from one Jev question; the logged attributes join the router only when the data shows they predict success.

## Decisions made during implementation

**Steps 1-2 (2026-10-07).**
- **One profile, one decision point.** `Router.profile` reads a `Profiled` event from the artifact's history, or asks Jev at `route:profile` and returns the event for the team to append. Configs that activate `route:*` keep working.
- **`RoutedTiers`** (teams/routing.py) is the one place a team's starting tier is chosen: once per artifact and team, recorded as `Routed` with its `call_kind`. Executors and writers step up a tier per rejection as before; the acceptance tester's second try goes one tier up; analyzers and planners make one call on the chosen tier. A planner is routed only when it actually calls the model (a task that fits makes no call).
- **Every team kind takes `model_tiers` and `routing`:** executors and writers (produce), probing and reviewing analyzers (critique), decomposer and outliner (plan), acceptance tester (accept).
- **`AttemptFailed`** replaces silent discards: an executor attempt with nothing for the gate to judge (thrown away before the next attempt, or the last one escalated without a commit of its work) goes on the board, so failures that never reach the gate still count against the model.
- **`OutcomeRecorder`** (control/outcomes.py) runs with the other controllers each step. A commit by a model succeeds when promoted and fails when rejected, escalated (not an approval request) or reworked before promotion. A split succeeds when the umbrella is promoted, fails when the planner escalates, a child is escalated or the umbrella is rejected. Each outcome is written once, keyed by the settled event's position in the artifact's history (`OutcomeRecorded`). Critique and accept calls are not settled in projects.
- **Comparisons settle plans from the hidden tests** (`settle_plans=False` for their projects, then `OutcomeRecorder.settle_plans` with the delivered result). Produce outcomes come from the board everywhere, so the same evidence teaches the router in projects and experiments.
- **`Routed.call_kind` defaults to produce,** so boards from before this change still load.
