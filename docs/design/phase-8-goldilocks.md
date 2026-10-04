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

## Steps

1. The narrow red teams, registered in the catalog.
2. `agentswarm compare EXPERIMENT`: runs every task of a suite under every setup and prints the table
   (and writes it as JSON next to the projects). Tested offline with scripted models.
3. The review benchmark (planted bugs and bug-free controls), as a second kind of experiment.
4. The setups in `examples/goldilocks/` and the live runs: the building comparison on the hard and
   held-out suites, the review comparison on the same tasks' references.
5. Results, honestly reported here and in the tutorial, including where small swarms lose.

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
