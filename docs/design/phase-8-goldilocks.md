# Phase 8: Small swarms against bigger models

Status: **in progress** (decided on the recommendations; the user lets me work freely and redirects when
needed).

## The question

The motivation for AgentSwarm is a hypothesis: there is a *goldilocks range* where many small, narrow
agents beat a single bigger model, because the structure asks less of each model call. That structure is
narrow tasks (decomposition), machine checks (tests, the merge queue), a calibrated judge, and reviewers
that each look at the current state instead of following a long plan. Phase 8 makes this measurable on the
task suites we already have, where the ground truth is automatic.

Two questions, each with an objective answer:

1. **Building:** given the same task, does a swarm of small models deliver working code as often as one
   bigger model? At what cost in tokens and time?
2. **Reviewing:** do several *narrow* red teams catch more planted bugs than one broad red team? At what
   cost?

## Design

### Setups are project configs

A *setup* is an ordinary project config: which teams, which models, which levels. The comparison runs the
same tasks under every setup, so the differences between setups are only what their configs say. The
starting set, in `examples/goldilocks/`, all on local models through Ollama:

| Setup | Teams | Models |
| --- | --- | --- |
| `big` | executor | gemma4:26b |
| `small` | executor | gemma4:e4b |
| `tiered` | executor with tiers (a rejection moves it up) | gemma4:e4b → gemma4:12b |
| `swarm` | decomposer, executor, narrow red teams, the router's fix loop | gemma4:e4b everywhere |

Every setup uses the same first gate: the task's unit tests as a hard check, and no judge, so the gate is
identical across setups and Jev's thresholds aren't part of what's compared. The swarm still uses Jev for
sizing and splitting, because decomposition is part of what the swarm is.

### What counts as solved: the delivered result

A run is scored on what it *delivered*, not on one intermediate decision: after `run_until_idle`, the
task's hidden tests run against the tip of the first shared level (`experimental`). Fixes routed from
red-team findings also land there, so a swarm gets credit for the bugs it found and fixed, and a setup that
merged nothing is scored on the untouched seed, which fails. Hidden tests that can't run give no score
(Phase 7's rule).

### Measured per run, summarised per setup

| Per run | Per setup |
| --- | --- |
| solved (hidden tests on the delivered tip) | solve rate, with its 95% Wilson interval |
| model runs, input and output tokens (from `Spent` events) | tokens per task and per solved task |
| wall-clock seconds | median time per task |
| escalations still open at the end | escalation rate |

Tokens rather than dollars: local models are unpriced, and tokens are what a price would multiply. A run
records the model each token came from, so a mixed setup (tiers, a swarm with a bigger reviewer) can be
priced later with any price list.

### Narrow red teams

Four analyzers that each try one kind of attack, instead of one red team trying everything:

| Team | Looks only at |
| --- | --- |
| `edge_value_tester` | empty, zero, negative, very large and boundary values |
| `text_tester` | Unicode, whitespace, quotes, separators, very long strings |
| `error_tester` | wrong types, missing arguments, and whether the documented errors are raised |
| `contract_tester` | whether the code does exactly what the task says: every stated example and rule |

They reuse `ProbingAnalyzer` (evidence is a test that fails), so they differ from the red team only in
their instructions and finding kinds.

### The review benchmark: planted bugs

To measure catching rather than building, each run starts from a task's *reference* solution with one
planted bug: a mutant of a changed line that the hidden tests kill, so it is a real bug by construction.
The scripted executor writes the buggy code with a test that passes, so the gate merges it, and then the
reviewers of the setup under test examine it. A bug counts as caught when a reviewer reports a finding
(evidence is a failing test, so a finding is never just a claim). Measured per reviewer set: catch rate,
tokens per bug, and how many findings were raised on bug-free control runs (false alarms).

### Routing: difficulty scores and model curves (decided with the user)

Choosing a model per task is split in two parts that never depend on each other:

- **Jev scores the task's difficulty.** One model-agnostic question at a `route:<team>` point, such as "is
  this a simple, single-step change?". Jev's probability is the score. The score only has to *order*
  tasks (easy before hard); it doesn't have to be calibrated itself.
- **Each model gets a measured curve:** its success rate at each difficulty, fitted from comparison and
  evaluation runs, never from descriptions.
- **The router picks the cheapest model whose curve predicts success above a target** (for example 80%)
  at the task's difficulty, and the strongest model if none does. Tiers stay as the safety net: a
  rejection still steps up.

Adding or swapping a model means measuring its curve; Jev's question doesn't change. Two conditions,
checked with data rather than assumed:

1. **Difficulty must be roughly one-dimensional.** If two models' curves *cross* (one better on some kinds
   of task, the other on others), one score can't route correctly. The comparison results show whether
   they cross. If they do, use a few scores or one curve per task category.
2. **A curve belongs to its cohort:** one model version and one version of the difficulty question. A new
   Jev version or new wording means refitting the curves.

### Memory: a 24 GB machine

All local models share 24 GB of unified memory with macOS and Docker, so a setup's models must fit
together, and runs are grouped by setup so each model loads once (see step 2's decisions). A swarm's
many agents are prompts on one loaded model, which is itself part of the case for small swarms.

## Steps

1. The narrow red teams, registered in the catalog.
2. `agentswarm compare EXPERIMENT`: runs every task of a suite under every setup and prints the table
   (and writes it as JSON next to the projects). Tested offline with scripted models.
3. The review benchmark (planted bugs and bug-free controls), as a second kind of experiment.
4. Routing: the difficulty question, per-model curves fitted from results, the router, a `routed`
   setup.
5. A complexity-graded suite (levels 2–4) and a multi-team setup with the decomposer; see the decision
   below.
6. The setups in `examples/goldilocks/` (models that fit in memory together) and the live runs: the
   building comparison on the hard and held-out suites, the review comparison on the same tasks'
   references.
7. Results, honestly reported here and in the tutorial, including where small swarms lose, and whether
   the model curves cross.

## Not in this phase

- Hosted models (`openai:`, `anthropic:`): the setups are configs, so adding one is a config change, but the
  runs here stay local and free. Whether to spend money on hosted baselines is the user's call.
- Tuning the swarm until it wins. The point is to measure; changes that a result suggests are recorded as
  follow-ups.

## Decisions made during implementation

**Step 1 (narrow red teams).**
- **`NarrowRedTeam(ProbingAnalyzer)`** holds the shared rules (evidence is a failing test; stay within
  your focus, since other teams cover the rest; no style comments). Each subclass sets only `FOCUS` and its
  finding `KIND`, and its `INSTRUCTIONS` are built from those, so the four can't drift apart in the common
  rules.
- **The four teams:** `edge_value_tester` and `text_tester` report `edge_case`, `error_tester` reports
  `test_failure`, and `contract_tester` reports `incomplete`, because missing a stated example or rule
  means the task isn't done.
- **They're registered next to the integration tester and the broad red team**, built the same way:
  model, test runner and an optional relevance filter.

**Step 2 (`agentswarm compare`).**
- **`evaluation/compare.py`:**
  - `Experiment` names a suite and setups; paths are relative to the experiment file, and missing files
    and unknown task ids are errors at load time.
  - `ComparisonRun` loops tasks outside and setups inside, so an interrupted comparison still has
    balanced results.
  - `RunMeasure` records one run; `SetupSummary` and `ComparisonReport` aggregate and render them.
- **Scoring:** the hidden tests run against the tip of the base level after the project is idle. A setup
  that merged nothing is scored on the seed, which fails.
- **Tokens are counted per real model** (`ollama:gemma4:e4b`), not per config name. The test caught that
  `Spent` records the config's name for the model, and two setups can both call theirs `coder`.
- **Team model runs only:** Jev decisions and the LLM fallback (`jev*`) aren't counted. They are judging
  costs, separate from the building cost being compared.
- **Results:** the command prints a line per run as it finishes, then the table, and writes every run
  and the summary to `<workdir>/results.json`.
- **Run order (after the memory discussion):** `order: by_setup` is the default, so every task runs under one
  setup before the next and each model loads once. Looping task by task would reload models on almost
  every run on a 24 GB machine. `order: by_task` stays available, for when an interrupted comparison
  should still have balanced results.

**Step 3 (review benchmark).**
- **`evaluation/review.py`; `kind: review` in the experiment file** selects it, and `agentswarm compare`
  runs either kind.
- **`BugPlanter`:**
  - takes the lines a task's reference changed relative to the seed (`ChangedLines.between`, a
    `difflib` diff of the two texts);
  - generates mutants on those lines with the existing `MutantGenerator`;
  - keeps only the mutants the hidden tests catch, checked in a scratch copy in the setup's sandbox;
  - picks `bugs_per_task` of them reproducibly (a seeded shuffle).
- **Bugs are planted once per task and shared by every setup**, so every setup reviews exactly the same
  bugs.
- **`PlantingExecutor`** writes the reference (with the bug, or clean for a control) and a test that
  passes, so the gate merges it. It ignores fix artifacts: the benchmark measures *finding* bugs, and a
  routed fix would only be rewritten by the script.
- **Swapping the executor:** `TeamCatalog.with_team` returns a copy of the catalog with the executor
  replaced, so a setup's reviewers are built exactly as in a normal run.
- **Scoring:**
  - a bug is caught when any reviewer team (a team watching above the lowest level) adds a finding;
  - a finding on a control run is a false alarm;
  - each run records which teams found something, so the summary credits bugs to individual narrow
    teams;
  - setups with no reviewers are refused up front.
- **Review runs always go one setup at a time**, for the memory reason in step 2.

**Step 4 (routing).**
- **`TaskDifficulty`** is a judge with one model-agnostic question ("is this a simple task: one small,
  clearly specified change, with no design decisions and no tricky rules?"). The difficulty is 1 −
  p(simple). Route decisions get no outcome of their own: they aren't calibrated as yes/no answers; the
  model curves do the calibration.
- **`teams/routing.py`:**
  - `ModelCurves` holds each model's success per difficulty band (3 by default), keyed by the real model
    id;
  - a band's prediction adds one imagined success and one failure (Laplace), so two lucky runs don't read
    as 100%, and an empty band predicts nothing;
  - `crossings()` reports bands where a model that is weaker overall clearly beats a stronger one (by more
    than 20 points, both with 3 or more runs). That checks condition 1 with data.
- **`Router.choose`:**
  - asks Jev at `route:<team>`;
  - when that point is active and curves exist, starts on the cheapest tier predicted to succeed at or
    above `target`, else on the strongest;
  - in shadow mode, without curves, or when Jev can't answer, keeps the cheapest tier and records the
    difficulty, which is how data for the curves is collected.
- **`ExecutorTeam`:**
  - routes once per artifact, before its first attempt, recording a `Routed` event (model name, model id,
    difficulty, decision, and whether the route came from curves);
  - later attempts read the starting tier back from that event, and a rejection steps up from there.
- **`routing: {curves: FILE, target: 0.8}`** on a team turns it on, with the path relative to the config
  file. `routing: {}` only logs difficulty. A project that routes needs a decider, like one with judged
  gates.
- **`agentswarm curves WORKDIR... --out FILE`:**
  - reads build comparisons' `results.json` and each run's `Routed` event, and joins them into
    (model, difficulty, solved) observations;
  - prints the curves and any crossings, and writes them as JSON;
  - when a task was split, only the task's own artifact counts, since the children's difficulty isn't the
    task's.

**Step 5 (setups and live runs).**
- **Models that fit (24 GB, checked on this machine):**
  - gemma4:e4b takes 3.2 GB on the GPU when loaded, much less than its 9.6 GB file;
  - gemma4:12b takes 8 GB;
  - gemma4:26b takes 18.3 GB in total, 13.4 GB on the GPU and the rest on the CPU, so it is slower but
    usable when it runs alone, which running one setup at a time guarantees;
  - gpt-oss:20b doesn't load in this Ollama version (it can't read the model file), so it's left out;
  - all three gemma4 sizes make real tool calls.
- **`examples/goldilocks/`:**
  - building setups: `small`, `medium`, `big` (one executor each, logging difficulty for the curves),
    `tiered` (small, stepping up to medium on rejection), `swarm` (a small executor plus the four narrow
    red teams, with findings becoming fix tasks) and `routed` (small, medium and big tiers chosen from
    curves; run after the curves exist);
  - reviewing setups: one broad red team on the small model, four narrow ones on the small model, and one
    broad red team on the big model. "Narrow and small" against "broad and big" is the goldilocks question
    for reviewing.
  - every setup shares the project, the seed and the first gate: unit tests only, no judge.
- **The swarm has no decomposer:** greeter tasks are single functions, so splitting would add noise
  rather than help. A suite with multi-part tasks is where decomposition should be compared.
- **Curve data comes only from single-model runs:** a run whose commits came from another model than the
  routed one (a tiered step up) is skipped, so the result is never credited to the wrong model.
- **A smoke run** (one task under all five building setups) precedes the long runs.
- **What the smoke run found, fixed before the long runs:**
  - **One failing reviewer silenced the others.** The text tester's small model couldn't produce its
    structured report and escalated, which parked the artifact, so the error and contract testers never
    looked at it. A reviewer's failure is now an `Escalated(parks=False)`, a *notice*: it waits for a
    person and can be retried like any escalation, but `BoardViews.active` ignores it, so the other teams
    carry on.
  - **The final-answer recovery gets two tries.** The small model sometimes replies without the
    structured answer even when asked for nothing else; a second ask is cheap.
- **Smoke result:** all five setups solved `greet-pair`. Big took 381 s against 31 s for small, with
  4× the tokens; the swarm took 218 s with 6× the tokens of small.
- **The review benchmark runs on half the held-out tasks** (decided with the user, to keep the run to a few
  hours): every second task in suite order, so the choice is mechanical, not hand-picked. That gives 8
  planted bugs and 8 clean controls per reviewing setup.

**The complexity gradient (decided with the user, while the runs were going).**
- **The greeter suites only map the easy end.** Every task is one function in one file, which a single model
  can solve in one go. There a multi-team setup can only add overhead, so the results so far say nothing
  about the hypothesis, which is about *complex* work. Token cost isn't the criterion either: what matters
  is whether the multi-team setup solves problems a single model can't.
- **The prediction is a crossover:** single models do well up to some complexity and then fall off, while
  a multi-team setup degrades more slowly. Testing it needs tasks graded by complexity:
  - level 2: several interacting functions in one module, with shared rules and edge cases;
  - level 3: a small package of several modules with a data model and persistence;
  - level 4: a feature across the package, such as a command-line interface with configuration and error
    handling.

  Hidden tests check the integration, and each reference solution passes them while the seed fails them.
  Solve rate is plotted per setup against complexity level.
- **For those tasks the multi-team setup includes the decomposer,** left out of the greeter runs because
  there was nothing to split.
- **Naming:** results call the setups *single-model* and *multi-team*. "Swarm" suggests many identical agents
  with emergent behaviour; AgentSwarm is closer to an organisation of specialised teams.

