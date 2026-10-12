# AgentSwarm: an overview

A broad picture of how the swarm works, in the order things happen in a run. For the details, see REFERENCE.md (every setting), PROMPTS.md (every prompt) and FRAMEWORK_PROPOSAL.md (the design).

## The idea in one paragraph

A goal is written down once, in a project file. From then on, small independent agents do the work, each with one narrow job, and none of them talks to another. They coordinate only through a shared record, the board: one agent writes "this task exists", another reads it, does the work and writes "done, here is the commit", a third checks it and writes "merged". Plain code, not a model, decides what counts as done: tests, checks and gates. Models only produce and propose.

## The parts

| Part | What it is | Model or code? |
| --- | --- | --- |
| **Project file** | One YAML file: the goal (the "north star"), the models, the checks, the teams, the budget | written by a person |
| **Seed** | The starting repository: usually a stub plus the tests that define done | made by an importer |
| **Board** | An append-only log of events (task created, claimed, committed, merged, failed, spent). Everything else is read from it | code |
| **Teams** | Workers that each watch the board for one kind of work: the decomposer plans, the executor builds | models |
| **Controllers** | Plain-code loops: the worklist, the router, the supervisor, the budget, escalations | code |
| **Gates and checks** | Decide whether work moves up a level: tests, the ratchet, layout and hygiene checks | code |
| **Levels** | Git branches the work climbs through: `raw` (being built), `experimental` (passed its gate), and further levels where a project needs them | code |
| **Sandbox** | A throwaway Docker container with no network, where every test runs | code |
| **Jev** | A small, measured judge for questions with no plain-code answer ("how hard is this task?") | a model, calibrated |

## A run, from start to finish

Take jq as the example: build a jq interpreter until jq's own tests pass.

**1. Setup (once, by a person and an importer).** The importer turns jq's test suite into a seed: an empty `jqpy` package that fails nearly every test, the visible tests in `tests/acceptance/`, and every fifth test kept apart as a held-out set no agent ever sees. The project file names the goal, the models allowed, the checks and a budget.

**2. The run starts and is scored.** `agentswarm run` opens the board, creates the repository and the level branches, and measures where things stand: 5 of 550 visible tests pass, 2 of 125 held out.

**3. The worklist makes a task.** A controller runs the tests, finds the first test file with failures and posts a task on the board: "Make the failing tests in tests/acceptance/test_jq_03_field_access.py pass; tests that pass now must keep passing." One task is open at a time, so each builds on the last.

**4. A model is chosen.** The router looks up the task's difficulty (Jev rates it) and the ledger of every earlier attempt, across all projects: which model succeeded on tasks like this, and at what cost. It picks the model with the lowest expected cost per success that is likely enough to succeed, and now and then tries a less proven one. In practice it settled on GPT-6.1 Sol for these problems, after the cheaper models failed often enough on CommonMark.

**5. The executor works.** The executor team claims the task (a lease, so no one else takes it), creates a git worktree, and starts a fresh model session, with no memory of earlier tasks. The model gets:

- **instructions:** the executor's role, the same for every task (read before changing, small edits, run the checks, don't touch the acceptance tests);
- **one message of data:** the north star, this task's goal, the project's rules ("standard library only", "keep the code in jqpy"), anything a previous attempt left unfinished, and a map of the repository;
- **tools:** list files, search, read part of a file, edit, write, run the tests, run all checks. Each tool result ends with how many calls are left;
- **a required answer:** a typed summary of the change and whether the task is complete.

It then works like a developer: reads, edits, runs the tests, fixes, until the checks pass or its calls run out (60). Old tool results are cleared from its context when keeping them costs more than it helps.

**6. The checks decide, not the model.** When the model says it is done, plain code runs every check again in the sandbox. Passing work is committed. Failing work gets up to two repair rounds: the same work, plus exactly what broke. Work that passes some tests and breaks none is kept even if unfinished, and the next attempt is told what is left.

**7. The gate merges.** The gate for `raw → experimental` runs the checks on the merged result. The ratchet only lets work in if more tests pass than before and no acceptance test broke. If both hold, the commit is merged into `experimental`, and the board records it.

**8. Outcomes are recorded.** The attempt's model, cost and success go into the routing ledger, so the next choice is better informed. Spend is charged to the task and checked against the day's budget between attempts.

**9. Repeat.** The worklist sees the next failing test file and posts the next task. When no test fails any more, the run ends and is scored again: 550 of 550 visible, 109 of 125 held out, $4.14.

### When a task fails

Failure has a ladder, so a person is the last resort:

1. **Repair:** the same model gets what broke and tries again (twice).
2. **Climb:** the router picks the best model not yet tried on this task.
3. **Split:** when every model has failed it, the decomposer (a planner model) splits the task into smaller steps, told why it failed. Steps are building blocks: only the last must make tests pass, the others must break nothing.
4. **Escalate:** past the split depth, the task is parked for a person, who can retry, drop or change it.

The supervisor watches for stalls (nothing happening for an hour) and an exhausted budget, and escalates those too.

## How agents get their instructions

Every model call has the same shape, whatever the team:

- a **system prompt** with the team's role, the same for every task of that team;
- one **typed message** with the task's data, as JSON, never free text, marked as data so the model doesn't follow instructions found inside it;
- **tools** where the team needs them;
- a **typed answer**, checked against its schema.

There is no conversation between agents and no long-running agent. Each call starts fresh and knows only what its message says: the goal, its task, the rules and the repository. Whatever must carry over (unfinished work, why the last attempt failed) is written into the next task's message from the board. Every prompt is listed in PROMPTS.md.

## What keeps it honest

- **Tests the builder can't change:** acceptance tests are protected; any change to them is undone before the commit.
- **The ratchet:** nothing merges that breaks what worked.
- **Held-out tests:** a fifth of the tests is never shown. Visible and held-out pass rates side by side show whether progress is real (jq: 100% against 87% showed fitting; YAML: 100% against 97%).
- **The sandbox:** model-written code runs only in a container with no network and limited resources.
- **Hygiene checks:** for example no stray files at the project root, and no importing a library that does the task.

## What keeps it cheap

- The router picks the cheapest model expected to succeed, from measured outcomes.
- A day's budget caps every run; spend is checked between attempts.
- A task already solved by earlier work closes without a model call.
- Old tool output is trimmed by each model's own prices.
- Each run reports its cost per test gained.

## What a person does

- Writes the project file and approves each paid run.
- Answers escalations (`agentswarm escalations`, then `resolve` or `drop`).
- Watches, if they like: `agentswarm status` in text, or `agentswarm monitor` for a live web page of every project, its teams, models, spend and plan.

## Where things live

| Folder | What's there |
| --- | --- |
| `examples/<project>/project.yaml` | Each real problem's project file (commonmark, toml, jq, yaml) |
| `src/agentswarm/board/` | The board: the event log and what is read from it |
| `src/agentswarm/teams/` | Executor, decomposer, routing and the other teams |
| `src/agentswarm/control/` | Worklist, supervisor, budgets, escalations, outcomes |
| `src/agentswarm/gating/` | Checks, the ratchet, gates, merging |
| `src/agentswarm/llm/` | The only place that talks to model providers |
| `src/agentswarm/evaluation/` | Importers that turn test suites into seeds, and scoring |
| `~/agentswarm-runs/<project>/` | A run's board, repository and one plain folder per level |
| `~/agentswarm-data/routing.sqlite` | The ledger of every attempt, shared by all projects |
