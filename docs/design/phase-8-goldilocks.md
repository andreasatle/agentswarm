# Phase 8: Small swarms against bigger models

Status: **in progress** (decided on the recommendations; the user lets me work freely and redirects when needed).

## The question

The motivation for AgentSwarm is a hypothesis: there is a *goldilocks range* where many small, narrow agents beat a single bigger model, because the structure asks less of each model call. That structure is narrow tasks (decomposition), machine checks (tests, the merge queue), a calibrated judge, and reviewers that each look at the current state instead of following a long plan. Phase 8 makes this measurable on the task suites we already have, where the ground truth is automatic.

Two questions, each with an objective answer:

1. **Building:** given the same task, does a swarm of small models deliver working code as often as one bigger model? At what cost in tokens and time?
2. **Reviewing:** do several *narrow* red teams catch more planted bugs than one broad red team? At what cost?

## Design

### Setups are project configs

A *setup* is an ordinary project config: which teams, which models, which levels. The comparison runs the same tasks under every setup, so the differences between setups are only what their configs say. The starting set, in `examples/goldilocks/`, all on local models through Ollama:

| Setup | Teams | Models |
| --- | --- | --- |
| `big` | executor | gemma4:26b |
| `small` | executor | gemma4:e4b |
| `tiered` | executor with tiers (a rejection moves it up) | gemma4:e4b → gemma4:12b |
| `swarm` | decomposer, executor, narrow red teams, the router's fix loop | gemma4:e4b everywhere |

Every setup uses the same first gate: the task's unit tests as a hard check, and no judge, so the gate is identical across setups and Jev's thresholds aren't part of what's compared. The swarm still uses Jev for sizing and splitting, because decomposition is part of what the swarm is.

### What counts as solved: the delivered result

A run is scored on what it *delivered*, not on one intermediate decision: after `run_until_idle`, the task's hidden tests run against the tip of the first shared level (`experimental`). Fixes routed from red-team findings also land there, so a swarm gets credit for the bugs it found and fixed, and a setup that merged nothing is scored on the untouched seed, which fails. Hidden tests that can't run give no score (Phase 7's rule).

### Measured per run, summarised per setup

| Per run | Per setup |
| --- | --- |
| solved (hidden tests on the delivered tip) | solve rate, with its 95% Wilson interval |
| model runs, input and output tokens (from `Spent` events) | tokens per task and per solved task |
| wall-clock seconds | median time per task |
| escalations still open at the end | escalation rate |

Tokens rather than dollars: local models are unpriced, and tokens are what a price would multiply. A run records the model each token came from, so a mixed setup (tiers, a swarm with a bigger reviewer) can be priced later with any price list.

### Narrow red teams

Four analyzers that each try one kind of attack, instead of one red team trying everything:

| Team | Looks only at |
| --- | --- |
| `edge_value_tester` | empty, zero, negative, very large and boundary values |
| `text_tester` | Unicode, whitespace, quotes, separators, very long strings |
| `error_tester` | wrong types, missing arguments, and whether the documented errors are raised |
| `contract_tester` | whether the code does exactly what the task says: every stated example and rule |

They reuse `ProbingAnalyzer` (evidence is a test that fails), so they differ from the red team only in their instructions and finding kinds.

### The review benchmark: planted bugs

To measure catching rather than building, each run starts from a task's *reference* solution with one planted bug: a mutant of a changed line that the hidden tests kill, so it is a real bug by construction. The scripted executor writes the buggy code with a test that passes, so the gate merges it, and then the reviewers of the setup under test examine it. A bug counts as caught when a reviewer reports a finding (evidence is a failing test, so a finding is never just a claim). Measured per reviewer set: catch rate, tokens per bug, and how many findings were raised on bug-free control runs (false alarms).

### Routing: difficulty scores and model curves (decided with the user)

Choosing a model per task is split in two parts that never depend on each other:

- **Jev scores the task's difficulty.** One model-agnostic question at a `route:<team>` point, such as "is this a simple, single-step change?". Jev's probability is the score. The score only has to *order* tasks (easy before hard); it doesn't have to be calibrated itself.
- **Each model gets a measured curve:** its success rate at each difficulty, fitted from comparison and evaluation runs, never from descriptions.
- **The router picks the cheapest model whose curve predicts success above a target** (for example 80%) at the task's difficulty, and the strongest model if none does. Tiers stay as the safety net: a rejection still steps up.

Adding or swapping a model means measuring its curve; Jev's question doesn't change. Two conditions, checked with data rather than assumed:

1. **Difficulty must be roughly one-dimensional.** If two models' curves *cross* (one better on some kinds of task, the other on others), one score can't route correctly. The comparison results show whether they cross. If they do, use a few scores or one curve per task category.
2. **A curve belongs to its cohort:** one model version and one version of the difficulty question. A new Jev version or new wording means refitting the curves.

### Memory: a 24 GB machine

All local models share 24 GB of unified memory with macOS and Docker, so a setup's models must fit together, and runs are grouped by setup so each model loads once (see step 2's decisions). A swarm's many agents are prompts on one loaded model, which is itself part of the case for small swarms.

## Steps

1. The narrow red teams, registered in the catalog.
2. `agentswarm compare EXPERIMENT`: runs every task of a suite under every setup and prints the table (and writes it as JSON next to the projects). Tested offline with scripted models.
3. The review benchmark (planted bugs and bug-free controls), as a second kind of experiment.
4. Routing: the difficulty question, per-model curves fitted from results, the router, a `routed` setup.
5. A complexity-graded suite (levels 2–4) and a multi-team setup with the decomposer; see the decision below.
6. The setups in `examples/goldilocks/` (models that fit in memory together) and the live runs: the building comparison on the hard and held-out suites, the review comparison on the same tasks' references.
7. Results, honestly reported here and in the tutorial, including where small swarms lose, and whether the model curves cross.

## Revision after the first live runs (2026-10-05, decided with the user)

### What the first runs showed

- **The build suites are too easy to separate the models.** gemma4 e4b, 12b and 26b each solved about 90% of the hard and held-out tasks, and only 5 runs landed in the top difficulty band, so the curves can't route yet.
- **The review benchmark measured plumbing, not reviewing.** Planted bugs found: 0/8, 0/8 and 1/8. But 27 of 96 reviewer runs ended without an answer at all, and the benchmark counted them as "nothing found".
- **The cause was Ollama's context window.** Ollama runs every model with 4096 tokens unless told otherwise, and silently drops the start of a longer conversation, instructions included. Every Phase 8 run so far had that window. Fixed: a `context_window` model setting, 32k by default for Ollama.
- **Whole-project runs are a poor measuring instrument.** Each outcome mixes the model with the sandbox, the gate, the tool loop and the provider's settings. One wrong setting spoils a night of data, and a data point costs 5–10 minutes.

### The atomic benchmark: one prompt, one answer, scored by plain code

The user's proposal: measure models on single calls, with no framework loop. Each probe is one prompt (instructions and a typed payload with all the context it needs, no tools) and one typed answer, scored by plain code against a known truth:

| Kind | The prompt gives | The answer | Success |
| --- | --- | --- | --- |
| `produce` | the task and every file of the seed | the complete new content of each file it creates or changes | the task's hidden tests pass on the seed with those files |
| `critique` | the task, the changed code and its diff, either clean (the reference) or with one planted bug | whether there is a problem, and a pytest file that shows it | bug: the test fails on the buggy code and passes on the reference. Clean: no test that fails on the reference |

Samples are cheap (seconds) and plentiful: every task of every greeter suite, with several planted bugs each. The same probes run against every model, so models are compared on exactly the same prompts. That makes it the instrument for choosing models too: Qwen coder, Llama and others run alongside Gemma.

It also tests a design question: small models may do better with one prompt that carries all the context than with a tool loop, whose overhead fills their window.

The probes' prompts are listed in docs/PROMPTS.md like every other model call.

### Routing per call

Today the model is chosen once per task, for the executor only. It becomes per call: per `worker.run`, one prompt and payload giving one typed answer. A routing worker wraps the real ones and picks a model from the kind of call and the difficulty of its payload, using curves per (kind of call, model) fitted from the atomic benchmark. Every team gets routing without knowing about it. Models aren't switched between steps of one tool loop: that would need a truth for "was this a good next step", which nothing provides.

### Revised steps (updated 2026-10-05: research findings, routing learned in use)

[RESEARCH.md](../RESEARCH.md) changes what a fair comparison is. Its strongest finding is that several independent attempts, selected by tests, beat more elaborate setups at equal cost (P2, P8). So the baseline every setup must beat is **one model, n attempts, checks select, same token budget**, and reliability is reported as consistency over repeated attempts (P6), not as single successes.

8. **The atomic benchmark** (done): `agentswarm probe`, one prompt and one answer per probe, scored by plain code; invalid, cut-off and missing answers counted apart; answers stored for blind rating.
9. **The task set** (done): EvalPlus imported (538 tasks) and every task rated by Jev (`agentswarm rate`). The hard end is thin (24 tasks); harder tasks get imported if the models separate only there.
10. **Routing learned from outcomes** (done, replacing curves from a big upfront run): Jev describes each task once (`CallProfile`), the router picks the cheapest tier whose drawn success rate meets the target (Thompson sampling over a shared ledger), and every attempt's outcome teaches it.
11. **Sanity run** (done): the whole pipeline on one fixed model (`sanity.yaml`): gemma4-e4b solved 3/3. Qwen 14B describes tool calls instead of making them, so tool use is part of qualifying a model.
12. **Qualification run, open models first** (next): a short probe run (about 10 tasks per band, one planted bug, Gemma's thinking off) that writes to the ledger; it rules out models that can't answer validly and gives the router its first outcomes. Hosted models follow once the open ones have been seen.
13. **A real project, not toy tasks:** the greeter only checks that the steps run in the right order; one model call solves its tasks. The goldilocks question needs a project with a high-level goal: several modules, a decomposition, reviews. One is chosen and run end to end, routed, against the baseline.
14. **Baselines at equal cost:** one model making n attempts selected by checks, at the team setups' token budget (P2, P8); narrow red teams against n samples of a broad one (P10). Probe repeats (pass^k) and a reasoning-field variant are added when a question needs them.
15. **Results**, honestly reported, as in step 7, including where small swarms lose.

What this phase measures feeds [Phase 10](phase-10-robustness.md), which builds the robustness mechanisms that win.

## Not in this phase

- Hosted models (`openai:`, `anthropic:`): the setups are configs, so adding one is a config change, but the runs here stay local and free. Whether to spend money on hosted baselines is the user's call.
- Tuning the swarm until it wins. The point is to measure; changes that a result suggests are recorded as follow-ups.

## Decisions made during implementation

**Step 1 (narrow red teams).**
- **`NarrowRedTeam(ProbingAnalyzer)`** holds the shared rules (evidence is a failing test; stay within your focus, since other teams cover the rest; no style comments). Each subclass sets only `FOCUS` and its finding `KIND`, and its `INSTRUCTIONS` are built from those, so the four can't drift apart in the common rules.
- **The four teams:** `edge_value_tester` and `text_tester` report `edge_case`, `error_tester` reports `test_failure`, and `contract_tester` reports `incomplete`, because missing a stated example or rule means the task isn't done.
- **They're registered next to the integration tester and the broad red team**, built the same way: model, test runner and an optional relevance filter.

**Step 2 (`agentswarm compare`).**
- **`evaluation/compare.py`:**
  - `Experiment` names a suite and setups; paths are relative to the experiment file, and missing files and unknown task ids are errors at load time.
  - `ComparisonRun` loops tasks outside and setups inside, so an interrupted comparison still has balanced results.
  - `RunMeasure` records one run; `SetupSummary` and `ComparisonReport` aggregate and render them.
- **Scoring:** the hidden tests run against the tip of the base level after the project is idle. A setup that merged nothing is scored on the seed, which fails.
- **Tokens are counted per real model** (`ollama:gemma4:e4b`), not per config name. The test caught that `Spent` records the config's name for the model, and two setups can both call theirs `coder`.
- **Team model runs only:** Jev decisions and the LLM fallback (`jev*`) aren't counted. They are judging costs, separate from the building cost being compared.
- **Results:** the command prints a line per run as it finishes, then the table, and writes every run and the summary to `<workdir>/results.json`.
- **Run order (after the memory discussion):** `order: by_setup` is the default, so every task runs under one setup before the next and each model loads once. Looping task by task would reload models on almost every run on a 24 GB machine. `order: by_task` stays available, for when an interrupted comparison should still have balanced results.

**Step 3 (review benchmark).**
- **`evaluation/review.py`; `kind: review` in the experiment file** selects it, and `agentswarm compare` runs either kind.
- **`BugPlanter`:**
  - takes the lines a task's reference changed relative to the seed (`ChangedLines.between`, a `difflib` diff of the two texts);
  - generates mutants on those lines with the existing `MutantGenerator`;
  - keeps only the mutants the hidden tests catch, checked in a scratch copy in the setup's sandbox;
  - picks `bugs_per_task` of them reproducibly (a seeded shuffle).
- **Bugs are planted once per task and shared by every setup**, so every setup reviews exactly the same bugs.
- **`PlantingExecutor`** writes the reference (with the bug, or clean for a control) and a test that passes, so the gate merges it. It ignores fix artifacts: the benchmark measures *finding* bugs, and a routed fix would only be rewritten by the script.
- **Swapping the executor:** `TeamCatalog.with_team` returns a copy of the catalog with the executor replaced, so a setup's reviewers are built exactly as in a normal run.
- **Scoring:**
  - a bug is caught when any reviewer team (a team watching above the lowest level) adds a finding;
  - a finding on a control run is a false alarm;
  - each run records which teams found something, so the summary credits bugs to individual narrow teams;
  - setups with no reviewers are refused up front.
- **Review runs always go one setup at a time**, for the memory reason in step 2.

**Step 4 (routing).**
- **`TaskDifficulty`** is a judge with one model-agnostic question ("is this a simple task: one small, clearly specified change, with no design decisions and no tricky rules?"). The difficulty is 1 − p(simple). Route decisions get no outcome of their own: they aren't calibrated as yes/no answers; the model curves do the calibration.
- **`teams/routing.py`:**
  - `ModelCurves` holds each model's success per difficulty band (3 by default), keyed by the real model id;
  - a band's prediction adds one imagined success and one failure (Laplace), so two lucky runs don't read as 100%, and an empty band predicts nothing;
  - `crossings()` reports bands where a model that is weaker overall clearly beats a stronger one (by more than 20 points, both with 3 or more runs). That checks condition 1 with data.
- **`Router.choose`:**
  - asks Jev at `route:<team>`;
  - when that point is active and curves exist, starts on the cheapest tier predicted to succeed at or above `target`, else on the strongest;
  - in shadow mode, without curves, or when Jev can't answer, keeps the cheapest tier and records the difficulty, which is how data for the curves is collected.
- **`ExecutorTeam`:**
  - routes once per artifact, before its first attempt, recording a `Routed` event (model name, model id, difficulty, decision, and whether the route came from curves);
  - later attempts read the starting tier back from that event, and a rejection steps up from there.
- **`routing: {curves: FILE, target: 0.8}`** on a team turns it on, with the path relative to the config file. `routing: {}` only logs difficulty. A project that routes needs a decider, like one with judged gates.
- **`agentswarm curves WORKDIR... --out FILE`:**
  - reads build comparisons' `results.json` and each run's `Routed` event, and joins them into (model, difficulty, solved) observations;
  - prints the curves and any crossings, and writes them as JSON;
  - when a task was split, only the task's own artifact counts, since the children's difficulty isn't the task's.

**Step 5 (setups and live runs).**
- **Models that fit (24 GB, checked on this machine):**
  - gemma4:e4b takes 3.2 GB on the GPU when loaded, much less than its 9.6 GB file;
  - gemma4:12b takes 8 GB;
  - gemma4:26b takes 18.3 GB in total, 13.4 GB on the GPU and the rest on the CPU, so it is slower but usable when it runs alone, which running one setup at a time guarantees;
  - gpt-oss:20b doesn't load in this Ollama version (it can't read the model file), so it's left out;
  - all three gemma4 sizes make real tool calls.
- **`examples/goldilocks/`:**
  - building setups: `small`, `medium`, `big` (one executor each, logging difficulty for the curves), `tiered` (small, stepping up to medium on rejection), `swarm` (a small executor plus the four narrow red teams, with findings becoming fix tasks) and `routed` (small, medium and big tiers chosen from curves; run after the curves exist);
  - reviewing setups: one broad red team on the small model, four narrow ones on the small model, and one broad red team on the big model. "Narrow and small" against "broad and big" is the goldilocks question for reviewing.
  - every setup shares the project, the seed and the first gate: unit tests only, no judge.
- **The swarm has no decomposer:** greeter tasks are single functions, so splitting would add noise rather than help. A suite with multi-part tasks is where decomposition should be compared.
- **Curve data comes only from single-model runs:** a run whose commits came from another model than the routed one (a tiered step up) is skipped, so the result is never credited to the wrong model.
- **A smoke run** (one task under all five building setups) precedes the long runs.
- **What the smoke run found, fixed before the long runs:**
  - **One failing reviewer silenced the others.** The text tester's small model couldn't produce its structured report and escalated, which parked the artifact, so the error and contract testers never looked at it. A reviewer's failure is now an `Escalated(parks=False)`, a *notice*: it waits for a person and can be retried like any escalation, but `BoardViews.active` ignores it, so the other teams carry on.
  - **The final-answer recovery gets two tries.** The small model sometimes replies without the structured answer even when asked for nothing else; a second ask is cheap.
- **Smoke result:** all five setups solved `greet-pair`. Big took 381 s against 31 s for small, with 4× the tokens; the swarm took 218 s with 6× the tokens of small.
- **The review benchmark runs on half the held-out tasks** (decided with the user, to keep the run to a few hours): every second task in suite order, so the choice is mechanical, not hand-picked. That gives 8 planted bugs and 8 clean controls per reviewing setup.

**The complexity gradient (decided with the user, while the runs were going).**
- **The greeter suites only map the easy end.** Every task is one function in one file, which a single model can solve in one go. There a multi-team setup can only add overhead, so the results so far say nothing about the hypothesis, which is about *complex* work. Token cost isn't the criterion either: what matters is whether the multi-team setup solves problems a single model can't.
- **The prediction is a crossover:** single models do well up to some complexity and then fall off, while a multi-team setup degrades more slowly. Testing it needs tasks graded by complexity:
  - level 2: several interacting functions in one module, with shared rules and edge cases;
  - level 3: a small package of several modules with a data model and persistence;
  - level 4: a feature across the package, such as a command-line interface with configuration and error handling.

  Hidden tests check the integration, and each reference solution passes them while the seed fails them. Solve rate is plotted per setup against complexity level.
- **For those tasks the multi-team setup includes the decomposer,** left out of the greeter runs because there was nothing to split.
- **Naming:** results call the setups *single-model* and *multi-team*. "Swarm" suggests many identical agents with emergent behaviour; AgentSwarm is closer to an organisation of specialised teams.

- **The suite (`examples/greeter/evaluation/complex/`, 12 tasks):**
  - **level 2:** `greet-styles`, `normalize-names`, `greeting-log`, `localized-fallback`: several interacting functions in `greeter.py`;
  - **level 3:** `greetbook`, `templates`, `reminders`, `contacts`: packages of three modules with a data model, validation or JSON persistence;
  - **level 4:** `greet-cli`, `greetbook-cli`, `visit-report`, `mail-merge`: packages with a command-line interface, files, exit codes and error handling, tested by running `python -m <package>`.
- **Checked like the other suites:** each reference passes its hidden tests and the untouched seed fails them. The check found three faults in my own first drafts, fixed before committing: a registry whose `render(name, **values)` clashed with a field called `name` (the template's name is now positional-only); a command-line flag that didn't win over the config when empty; a test running from the wrong folder.

**Step 8 (the atomic benchmark).**
- **`evaluation/probe.py`; `agentswarm probe`.** A probe experiment names suites, kinds, models and a sandbox; it doesn't use project setups, since a probe has no teams.
- **One class per kind** (`Producing`, `Critiquing`) holds the instructions, builds the typed request and scores the typed answer. Both are in docs/PROMPTS.md.
- **The answer models carry field descriptions** (`Field(description=...)`), so the model sees what each field means. The team calls still use comments, which models don't see; that is fixed with the other prompt changes.
- **Scoring by plain code, in the sandbox:**
  - produce: the answer's files are written over a fresh copy of the seed (a path that leaves it makes the probe unsolved), then the hidden tests run;
  - critique: the answer's test runs on the reference, and for a bug probe on the buggy code too. Caught needs fail-on-bug and pass-on-reference; a test that fails on both is `wrong_evidence`; a test that can't run proves nothing and is dropped, as in the analyzers.
- **No answer is an outcome of its own** (`no_answer`), never folded into "missed", which is what hid the context-window problem in the review benchmark.
- **The time recorded is the model's,** not the scoring's. Planted bugs are cached in the workdir, and results are appended to `results.jsonl` as they come, so a rerun resumes.

**Step 10 (difficulty ratings), and the user's goal for routing (2026-10-05).**
- **The goal, from the user:** at the end, small models should be used as much as possible. Routing picks the cheapest model whose measured success at the task's difficulty meets the target, and the larger ones only where it doesn't.
- **`agentswarm rate SUITE... --out FILE`** asks Jev's `TaskDifficulty` question about each task's text alone, as the router does before any work exists, and appends each rating with the judge's version and Jev's model. A rerun skips tasks already rated with the same wording.
- **All 590 tasks rated (jev-1.13.0), none failed.** Jev's ordering matches how the greeter suites were built: the basic suite rates easiest (median 0.09), the complex one hardest (0.78). EvalPlus is mostly easy: 377 easy, 148 medium and 13 hard (bands 0-0.33, 0.33-0.67, 0.67-1). **The hard end is thin**: 24 tasks in all. Curves there need harder tasks (an APPS or LiveCodeBench subset), the next import if the probes show the models separating only at the top.
- **The models:** six local ones, and three sizes from each hosted provider: Claude Haiku 4.5, Sonnet 5.5 and Opus 5.5; GPT-6 Luna, 6.1 Sol and 6 Astra (OpenAI's own small-to-large line). One structured call to each through our worker returned a valid answer. Sonnet 5.5, Opus 5.5 and the GPT-6 models reject `temperature`, so none is set, and their reasoning effort is the provider's default.

**Hosted smoke run (2026-10-05): the models found a bug in our answer key.**
- All six hosted models produced valid answers on every probe; the run cost $0.97 in all.
- Five of six flagged the *clean* `greetbook` reference, independently and with the same explanation: `load()` let `AttributeError` escape for an entry with a non-string name, where the task demands `ValueError`. They were right; the hidden tests never covered it. The reference now validates entries, and the hidden tests check eight malformed books (the old reference fails four of them).
- One model flagged a clean change for adding no tests, which the task text asks for. That is a fault of the probe, not the model: the critique instructions now say that only the code's behaviour is reviewed.
- **Lesson, kept as practice:** a "false alarm" that several strong models raise independently, with a test that fails on the reference, is first a suspect answer key. Such probes are checked by hand before the results count. The bugs planted from the old reference are cached per workdir, so runs before this fix are not pooled with runs after it.

**Routing learned in use, replacing the big upfront search (decided with the user, 2026-10-05).**
- Per task: Jev reads the prompt once and answers a few questions about it (attributes); the router, plain code, picks the cheapest model whose success so far meets the target; the checks give the outcome, which is saved. Retries reuse the attributes and step up a tier, as before.
- The router counts successes and failures per (kind of call, difficulty band, model), starting from a small guess, and draws each model's rate near its ratio (Thompson sampling), so uncertain models still get tried now and then. No gradients, no training: the table can be read.
- Extra attributes (design decisions, underspecified, algorithmic, several files) are asked from the start but only logged; one joins the router only when the data shows it predicts success.
- Outcomes go to one shared ledger per machine, so projects and probes all teach the same router.
- Before building it, a sanity run checks the whole pipeline on one fixed model (`sanity.yaml`).

**Course correction: measure before optimizing (2026-10-05, decided with the user after an outside review).**
- The project had started answering "which model should AgentSwarm use?" before "does AgentSwarm help?". Architecture work is frozen (routing, Phases 9 and 10) until the complexity experiment answers the second; new code needs an experiment that shows the need.
- **The inventory suite** (`examples/inventory/`): a seed with models, a JSON store, reports and a command line, and 25 tasks in five levels of complexity, levels 4-5 being vague features that need design and features across modules. A test checks every reference against its hidden tests and the seed's own tests.
- **The complexity experiment** (`examples/goldilocks/complexity.yaml`): Claude Haiku with one attempt (the user's middle ground between local and expensive models), gemma4-e4b with one attempt, gemma4-e4b with up to 3 attempts selected by checks, and AgentSwarm on gemma4-e4b. Prove the idea with capable models first; local-only operation is a later configuration, not a starting constraint.
- **Making the swarm a fair test** took three findings, each from a check run:
  - with Jev's untuned thresholds the decomposer never split (p(fits) 0.61 is "unsure"): planners got `split_below`, and the swarm splits at 0.7;
  - once it split, Jev's split check (0.8 against an uncalibrated 0.9) parked the task: that check is logged in shadow until calibrated;
  - then it split three levels deep into 57 tasks that stalled behind one slow model, the over-decomposition the research warns about: the swarm now splits once at most (`max_depth: 1`), and its supervisor waits longer before calling a queue a stall.
- The swarm gets the same `attempts: 3` as the baseline, so any difference comes from its decomposer and critics.

**Order of models to evaluate (the user, 2026-10-06).** Start small to test the methodology, then widen:
1. Two models: Claude Haiku and gemma4-e4b (the complexity experiment).
2. Once selecting between those two works: the two more advanced Claude models, Sonnet 5.5 and Opus 5.5.
3. Then Qwen (a version that makes real tool calls; possibly through llama-server).
4. Over time, all available models. Constraint: 24 GB of local memory, so local models are evaluated one at a time.

