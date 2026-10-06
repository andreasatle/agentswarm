# AgentSwarm Tutorial: Teams, Levels and a Calibrated Judge, One Layer at a Time

Oct 3, 2026 · Andreas Atle (built with Claude)

## How to use this tutorial

Read **Part 0** first: it explains what AgentSwarm is for and every piece you need, before any command.
Then work through the steps in order. Each one adds a single idea to the previous ones and shows you
where it lives in the code.

**Every step has a script in this folder**, so nothing needs copying: run it from anywhere, for example
`tutorial/03_first_run.sh`, or `uv run tutorial/01_board.py` for the Python steps. Each script prints every
command before it runs it, so you see exactly what it does, and keeps its project under
`~/agentswarm-runs/tutorial/` (set `AGENTSWARM_RUNS` to put it elsewhere, still under your home folder).
A script that creates a project starts its own folder afresh each time; later steps reuse earlier ones. [REFERENCE.md](../docs/REFERENCE.md) has every config key, event and command in
tables; this document explains why things are the way they are.

| Step | New idea | Script | Needs |
| --- | --- | --- | --- |
| 1 | The board: events in, views out | `uv run tutorial/01_board.py` | nothing |
| 2 | Claims: one team at a time | `uv run tutorial/02_claims.py` | nothing |
| 3 | Levels are branches; a first run | `tutorial/03_first_run.sh` | nothing |
| 3b | The same run from Python, at the project level | `uv run tutorial/03b_project_api.py` | nothing |
| 4 | A real executor in a sandbox | `tutorial/04_executor.sh` | Ollama, Docker |
| 5 | Jev judges the gate, in shadow | `tutorial/05_shadow.sh` | Ollama, Docker, Jev |
| 6 | Ground truth without labelling | `tutorial/06_ground_truth.sh`, then `tutorial/06_label.sh` | Ollama, Docker, Jev |
| 7 | Analyzers, findings and the router | `tutorial/07_findings.sh` | step 5 |
| 8 | Release trains, human gates, escalations | `tutorial/08_escalations.sh [ARTIFACT]` | step 5 |
| 9 | Decomposition and joins | `tutorial/09_decomposition.sh` (offline tests) | nothing |
| 10 | The supervisor | `tutorial/10_supervisor.sh` (offline tests) | nothing |
| 11 | Making Jev act: tuning, active points, audit | `tutorial/11_jev_acts.sh` | step 6 |
| 12 | A second domain: documents | `tutorial/12_documents.sh`, then `tutorial/12_publish.sh` | Ollama, Docker, Jev |
| 13 | Several projects, money and limits | `tutorial/13_swarm.sh [live]` | nothing (live: Ollama, Docker, Jev) |
| 14 | What keeps it safe | | |
| 15 | Does the swarm help? Measuring before optimizing | `tutorial/15_measure.sh [live]` | nothing (live: Ollama, Docker, Jev, Anthropic key) |

Steps 1–3b, 9, 10, the quickstart swarm in step 13 and step 15's offline part run anywhere, offline. Status:
the build plan and a hardening phase from an independent audit are done. Phase 8 is in progress: measuring
whether the swarm actually beats simpler setups (step 15). Architecture work is paused until it has an answer.

---

## Part 0 — What AgentSwarm is, and what you need

### The problem it solves

Earlier attempts (forge) used a planner and a worker that waited on each other, and an LLM critic that
judged every result. Two things went wrong: roles waiting on each other in a fixed order **deadlocked**,
and the critic was **expensive and its confidence meaningless** (self-reported, not calibrated).

AgentSwarm is built so neither can happen:

- **Teams never call each other.** Each team watches one *level* of maturity and reacts to the state of
  the work there. Ordering comes from the levels, so there is nothing to wait on and no cycle.
- **Three kinds of work are kept apart.** Language models *produce* (code, tests, text). Jev, a small
  calibrated decision model, *decides* (is this complete, is this in scope, is this worth testing). Plain,
  tested code *controls* (claims, merges, promotions, budgets).
- **Every decision is logged and measured.** Each of Jev's answers is stored with its exact input, and
  later gets an *outcome*: what turned out to be true. That is what lets you trust Jev's probabilities,
  and tune when to act on them.

### The picture

```
                     ┌──── one project: one north star ────────────────────────────────────┐
  you: a task ──►    │  Blackboard = append-only event log + views                          │
                     │      ▲ read state, claim, append events                               │
                     │  ┌───┴──────┐ ┌───────────┐ ┌──────────┐ ┌─────────┐ ┌────────────┐   │
                     │  │decomposer│ │ executor  │ │integration│ │red team │ │ supervisor │   │
                     │  │  (raw)   │ │  (raw)    │ │ (exper.)  │ │(exper.) │ │router, joins│  │
                     │  └──────────┘ └─────┬─────┘ └─────┬────┘ └────┬────┘ └────────────┘   │
                     │                     ▼             ▼           ▼                       │
                     │  git:  raw/a-0001 ─gate─► experimental ─train─► candidate ─human─► production
                     │        (hard checks, then Jev, then a policy)                         │
                     └───────────────────────────────────────────────────────────────────────┘
```

### What you need

| Piece | Why | How |
| --- | --- | --- |
| `uv` and Python 3.13 | The project and its tools | `uv sync --all-extras` in this folder |
| A model that makes real tool calls | The executor and analyzers | `ollama pull gemma4:e4b` (any LangChain provider works) |
| Docker | Runs code a model wrote, isolated | Docker Desktop or Colima, then `docker build -t agentswarm/sandbox-python:3.13 -f docker/sandbox-python.Dockerfile docker` |
| A Jev API key | The judge | `TYPESAFE_API_KEY=...` in this project's `.env` (git-ignored; the CLI loads it) |

Project roots (`--root`) must be under your home folder: Colima and Docker Desktop only share those with
containers.

**Typed end to end.** Every event, finding, config section, model output and Jev answer is a Pydantic
class. You write `decision.ruling.verdict`, never `data["ruling"]["verdict"]`; a reply that doesn't fit
fails where it arrives, naming the field.

---

## Step 1 — The board: events in, views out

Everything that happens in a project is an **event** appended to its log, and nothing else changes state.
What you read (which artifacts are at which level, which findings are open) are **views**, folded from the
log. Run `uv run tutorial/01_board.py`; this is all of it, one named value per line so you can inspect
any of them:

```python
from agentswarm.board.blackboard import Blackboard
from agentswarm.board.eventlog import InMemoryEventLog
from agentswarm.core.clock import FakeClock
from agentswarm.core.domain import (
    Artifact,
    ArtifactCreated,
    ArtifactId,
    CommitPushed,
    Level,
    ProjectId,
    TeamName,
)

# The pieces of a board: a clock, a project id, a log of events, and the board over that log.
clock = FakeClock()
project = ProjectId("demo")
log = InMemoryEventLog(project, clock)
board = Blackboard(log)

# An artifact: one task on its own branch at the lowest level.
artifact_id = ArtifactId("a-0001")
raw = Level("raw")
human = TeamName("human")
task = Artifact(
    id=artifact_id, task="Add farewell", branch="raw/a-0001-farewell", level=raw, created_by=human
)

# Appending an event is the only way to change anything; it returns the new views.
created = ArtifactCreated(artifact=task)
views = board.append([created])
at_raw = views.artifacts_at(raw)
ids_at_raw = [artifact.id for artifact in at_raw]
print("at raw:", ids_at_raw)  # ['a-0001']

pushed = CommitPushed(artifact=artifact_id, commit="c0ffee")
views = board.append([pushed])
unjudged = views.unjudged_commit(artifact_id)
version = views.version(artifact_id)
print("unjudged commit:", unjudged)  # c0ffee
print("version:", version)  # 2: the artifact changed twice

# Keep the views the board has built up, event by event, then fold the whole log again into new views.
# Two separate objects with the same content: the log alone is enough to recreate the state.
before = board.current()
replayed = board.rebuild()
separate = replayed is not before
same = replayed == before
print("rebuilt views are a new object:", separate)  # True
print("replay gives the same views:", same)  # True
```

Three things to notice:

- **Replay is free.** Throw the views away and fold the log again: you get exactly the same state. A real
  project keeps its log in `blackboard.sqlite`, so you can replay any project, up to any event.
- **"Unjudged commit"** is a view, not a flag someone sets: a commit stays unjudged until a gate promotes,
  rejects or escalates the artifact. Gates look for exactly that.
- **Version** changes only when the artifact itself changes (created, committed, promoted, rejected,
  sized, retried). Teams remember the version they last handled, so each team acts **once per change**,
  never on its own output, and never in a loop. This one rule replaces all coordination between teams.

Where: `board/eventlog.py`, `board/blackboard.py`, `core/domain.py`.

## Step 2 — Claims: one team at a time

Before a team works on an artifact, it **claims** it: a lease with an expiry. Run
`uv run tutorial/02_claims.py`; the core of it:

```python
# ... the same board and artifact as in step 1, then:
# Claims go through a registry over the board. A lease lasts ten minutes.
claims = ClaimRegistry(board, clock)
lease = timedelta(minutes=10)
executor = TeamName("executor")
red_team = TeamName("red_team")

# 1. One team at a time: while the executor holds the artifact, the red team is refused.
mine = claims.claim(artifact_id, executor, lease)
print("executor's claim:", mine)  # a Claim with an id and an expiry

refused = claims.claim(artifact_id, red_team, lease)
print("red team's claim while the executor holds it:", refused)  # None: taken

# 2. The normal case: the executor finishes and gives the claim back at once, long before it expires.
assert mine is not None
claims.release(mine)
theirs = claims.claim(artifact_id, red_team, lease)
print("red team's claim right after the release:", theirs)  # a new Claim, no waiting

# 3. The fallback: the red team crashes and never releases. Once its lease runs out, the executor can
#    take the artifact over again.
eleven_minutes = timedelta(minutes=11)
clock.advance(eleven_minutes)
again = claims.claim(artifact_id, executor, lease)
print("executor's claim after the red team's lease expired:", again)  # a new Claim

# 4. If the crashed red team comes back with a result, it is thrown away: someone else holds it now.
assert theirs is not None
views = board.current()
version = views.version(artifact_id)
published = claims.publish(theirs, version, [])
accepted = published is not None
print("red team's late result accepted:", accepted)  # False
```

A claim is appended **only if the log hasn't moved** since the registry checked that the artifact was free
(optimistic concurrency; SQLite's `BEGIN IMMEDIATE` underneath). Two teams racing for the same artifact can't
both win, and a crashed team's work is picked up again when its lease runs out. No locks between teams.

Each claim has an id, and a team publishes its result through the registry, which checks that nobody
took the artifact over meanwhile: if the lease ran out and another team claimed it, the late result is
thrown away instead of overwriting newer work. Across processes there is one rule: only one process may
have a project open (a lock file in the project root).

Where: `board/claims.py`. The race is tested in `tests/board/test_claims.py` by forcing the bad interleaving.

## Step 3 — Levels are branches; a first run

A project's **levels** are its maturity ladder. The lowest level holds one branch per artifact
(`raw/a-0001-...`); every level above is one shared branch with the level's name. Work moves up only
through a **gate**, one level at a time, and that is all the ordering there is.

Run the quickstart project (a fake executor, no model, no Jev, no Docker):

```bash
tutorial/03_first_run.sh
```

You'll see each artifact's own commit, merged into `experimental` with a merge commit. What happened, in
order (`run` repeats project *steps* until nothing happens):

1. The **team runner** for `fake_executor` found two artifacts at `raw` it hadn't handled, claimed each,
   checked out its branch in its own worktree (`worktrees/a-0001`), let the team act, appended its
   `CommitPushed`, and released the claim.
2. The **promoter** for the gate `raw → experimental` saw two unjudged commits. For each: claim, check out
   *exactly that commit* (detached, so the branch never moves), run the **hard checks** (here: every note
   has text) in the sandbox.
3. Passing artifacts went to the **merge queue**: merge onto `experimental`'s tip in a detached checkout,
   run the checks again **on the merged result**, and only if they pass move the `experimental` branch.
   So `experimental` always passes its own checks, and never shows an unchecked merge.
4. The branch moved in three recorded steps: `PromotionStarted`, the move itself (only if the branch hadn't
   moved meanwhile), then `Promoted` with the merge commit. If the process dies in between, the next run
   finds the open record and settles it from where the branch actually is. Later teams find an artifact's
   own change in its merge commit (`merge^1..merge`, what the merge brought into the level), even after
   other work lands.

Try breaking it: edit the check to always fail and run again with a new root; the artifacts get
`Rejected` with a blocker finding, and the fake executor tries again.

Where: `workspace/git.py`, `gating/promotion.py`, `gating/merge_queue.py`, `teams/runner.py`, `project.py`.
`agentswarm config examples/quickstart/project.yaml` prints the whole configuration.

## Step 3b — The same run, from Python

Steps 1 and 2 called the board's basic operations by hand: `append`, `current`, `claim`. A real project
never does. Each layer bundles the one below it:

| Layer | You call | It does for you |
| --- | --- | --- |
| Command line | `agentswarm run`, `status`, `resolve`, `swarm` | open, add tasks, run until idle, print, close |
| Project | `Project.open(config, root)` | builds everything from the YAML: the log and views, git, the sandbox, teams, gates, judges, router, supervisor |
| | `project.create_artifact(task)` | picks the next id, builds the `Artifact`, appends `ArtifactCreated` |
| | `project.step()`, `project.run_until_idle()` | one round of every team, gate and controller; or rounds until nothing happens |
| Team runner | (inside a step) | claim, check out a worktree, let the team act, publish its events, release, clean up |
| Board | `append`, `current`, `claim` | the basic operations of steps 1 and 2 |

Run `uv run tutorial/03b_project_api.py`. After finding the example config and its own folder, it does
this:

```python
# Load and check the configuration, then open the project: the log and views, git, sandbox, teams, gates.
config = ProjectConfig.load(config_path)
project = Project.open(config, root)
try:
    # One call builds the Artifact, picks its id and appends ArtifactCreated.
    first = project.create_artifact("Write a note")
    second = project.create_artifact("Write another note")
    print("created:", first.id, second.id)

    # Run until a round takes no action. Teams run asynchronously, hence asyncio.run.
    run = project.run_until_idle()
    rounds = asyncio.run(run)
    print("rounds that took action:", rounds)

    # Read the state: the views, as in step 1.
    views = project.views()
    first_now = views.artifact(first.id)
    second_now = views.artifact(second.id)
    print(first.id, "is at", first_now.level)  # experimental
    print(second.id, "is at", second_now.level)  # experimental

    history = views.history(first.id)
    kinds = [type(event).__name__ for event in history]
    print(first.id, "history:", kinds)  # created, claimed, commit, released, claimed, promoted, released

    # The content is in git: the level branch now holds both notes.
    repository_files = project.repository.files_at("experimental")
    print("files on experimental:", repository_files)
finally:
    project.close()  # releases the project's lock and closes its databases
```

Nothing here can be forgotten by accident: `append` returns the state after your events and every read
catches up by itself; the runner and gates release claims and remove worktrees in `finally` blocks, even
when a team crashes; `Project.open` checks the configuration before it writes anything. The one thing to
remember with the Python API is `project.close()`, which releases the project's lock and databases (the
command line does it for you).

Where: `project.py` (`Project`), `teams/runner.py` (`TeamRunner`).

## Step 4 — A real executor in a sandbox

Now a model does the work. The greeter evaluation project has the executor and one gate, nothing else:

```bash
tutorial/04_executor.sh
```

**The model.** `models: {coder: {model: "ollama:gemma4:e4b"}}`. The string is `<provider>:<model>`, built
with LangChain's `init_chat_model`, so changing provider is a config change (`openai:...`,
`anthropic:...`), never a code change. The model must make **real tool calls**: `qwen2.5-coder` on Ollama
writes them as text and fails here.

**The executor's loop** is LangChain's `create_agent` with a toolbox: list, read, write and delete files
(confined to the worktree: no absolute paths, `..`, `.git` or links pointing out) and run the checks. It
ends with a structured `ExecutorReport` (a summary and whether it thinks it's done). Small models often end
with plain text instead; the worker then asks once more for just the structured answer. If the checks
still fail, or the model says it isn't done, the work is committed and **escalated**, not thrown away.

**The sandbox.** Hard checks run code a model wrote, so they run in Docker: a fresh container per run, no
network, read-only root, dropped capabilities, CPU/memory/process limits, your uid, no keys in the
environment. What is mounted is a **throwaway copy** of the worktree without its `.git`: whatever the code
writes is discarded with the copy, so it can never reach git on your machine. Output is capped, and a
timeout kills the container.

A check ends in one of three ways: **passed**, **failed** (it ran and found a problem: for pytest, exit 1)
or **error** (a timeout, a missing tool, a crash of the runner). Errors still block a gate, but they never
count as evidence that the code is wrong.

**Model settings that fail silently.** Ollama runs every model with a 4096-token window unless told
otherwise, and drops the start of a longer conversation, instructions included; it also ignores
`max_tokens`. The first Phase 8 runs lost a third of their answers to this. Models now get
`context_window` (32k by default for Ollama) and `max_tokens` passed under the name each provider reads.
[MODELS.md](../docs/MODELS.md) explains every setting, and every prompt a model sees is in
[PROMPTS.md](../docs/PROMPTS.md) (`agentswarm prompts`).

Where: `teams/executor.py` (`ExecutorTeam`, `WorktreeToolbox`), `llm/workers.py` (`LangChainWorker`: the
only place LangChain is imported, with `llm/models.py`), `workspace/sandbox.py`.

## Step 5 — Jev judges the gate, in shadow

Hard checks prove what can be proven mechanically. What remains is judgement: does the change stay in
scope, do the tests exercise the behaviour, is every part of the task done? That is **Jev's** job, through a
**judge**:

```python
class ChangeReadiness(BaseModel):
    in_scope: Annotated[NoulAnswer, Ask("Does the change modify only what the task asks for? ..."),
                        Fails(FindingKind.OUT_OF_SCOPE, "The change modifies things the task did not ask for")]
    tests_relevant: ...
    complete: ...
```

Each question is a **Noul** (Jev returns the probability of yes) phrased so **yes is good**, with
`Fails(...)` saying what a clear "no" means as a finding. Jev sees a typed state: the north star, the task,
the findings it was supposed to fix, and the **real diff** (truncated to 60,000 characters).

The **policy** is plain code: per question, `p ≥ pass` passes, `p ≤ fail` fails, anything between is unsure;
any failure rejects (one finding per failed question), else any unsure escalates, else promote. Thresholds
are per question in the gate config.

**Shadow mode.** A new decision point starts in shadow (§12.3): Jev is asked and every decision is logged,
but the system behaves as if there were no judge. Nothing it says can block work until its answers have
been compared with what turned out to be true. Run the greeter project (software preset, two analyzers,
Jev in shadow; it needs the key):

```bash
tutorial/05_shadow.sh
```

Every decision records its exact input, Jev's answers, the ruling, the mode, the Jev version that answered,
and a **judge version**: a hash of the questions' wording. Changing a word starts a new version. Reports
group decisions by **cohort** (decision point, judge version and the concrete Jev version) and never mix
them, because answers to old wording, or from another model, say nothing about this one.

Where: `judging/judges.py`, `judging/policy.py`, `judging/judgement.py`, `judging/decisions.py`,
`jev/` (the client: HTTPS only, base URL fixed in code, the only reader of the key, retries for timeouts
and busy answers).

## Step 6 — Ground truth without labelling

To trust Jev's probabilities, you need outcomes: for each answer, what was actually true. Labelling by hand
doesn't scale, so every question gets an **automatic** source:

| Question | Truth from |
| --- | --- |
| `complete` | **hidden acceptance tests** per task, which the executor never sees |
| `tests_relevant` | **mutation testing**: break the changed code in small ways; real tests notice |
| `in_scope` | **planted faults**: scripted runs that write the reference solution, cleanly or with an unrelated edit, so the truth is known by construction |

A **task suite** lists tasks with hidden tests and reference solutions. Each reference is itself tested:
it must pass its hidden tests, and the untouched seed must fail them, or the ground truth would be wrong.
`evaluate` runs every task in four variants (the real model, the clean reference, reference plus an
out-of-scope edit, reference with an `assert True` test):

```bash
tutorial/06_ground_truth.sh
```

It prints one line per run and then the **calibration report**: per question, ten bins of Jev's
probability against how often the truth was yes, the expected calibration error, the Brier score, and how
often the default thresholds would decide and be right. What the first real run showed (56 decisions):
`tests_relevant` near ideal (ECE 0.07); `complete` right whenever it decided but under-confident;
`in_scope` the weak question, which led to sharper wording. It also exposed two flaws in *our* ground
truth (trivial tests left a task's "add tests" part undone; mutation testing never touched `if`
conditions), both fixed. Measuring the measurement is part of the job.

A person checks the automatic sources now and then, without being anchored by Jev's answer:

```bash
tutorial/06_label.sh
```

Where: `evaluation/` (suite, run, variants in `faults.py`, `mutation.py`), `judging/calibration.py`,
`judging/labelling.py`. Outcome sources rank human > planted > hidden tests = mutation = team result.

## Step 7 — Analyzers, findings and the router

Above the lowest level, teams never commit: only the merge queue writes level branches. They are
**analyzers**: they examine a merged artifact (in a detached checkout of its merge commit) and report
**findings**. The software preset has two at `experimental`: the **integration tester** (does the change
work with the code around it?) and the **red team** (does it break on hostile input?).

- **Evidence first.** An analyzer may report only problems backed by a test it wrote and ran that fails.
  Claims without one are dropped, so weak findings never block anything. A test that couldn't run at all
  (an error, not a failure) is no evidence either.
- **Relevance filters.** Before working, each analyzer asks Jev whether the change is worth its effort. In
  shadow mode it works anyway, and whether it found something becomes the automatic outcome of that
  decision. In the live run above, Jev said "not worth it" twice (0.08 and 0.15), the analyzers found
  nothing, and both decisions were scored right.
- **Sign-offs.** An analyzer ends with `SignedOff` for the version it examined; a gate above can require it.
- **The finding router.** Teams only report; plain code acts. Each open major or blocker finding above the
  lowest level becomes one **fix artifact** at the lowest level (its task carries the finding and the
  failing test), and the finding is resolved when the fix reaches the finding's level. Minor findings stay
  on the board for a person and are not routed.

To see what step 5's analyzers did (sign-offs, findings, fix artifacts):

```bash
tutorial/07_findings.sh
```

Where: `teams/analyzer.py`, `teams/relevance.py`, `control/router.py`.

## Step 8 — Release trains, human gates, escalations

`experimental` is one line of merge commits, one per artifact. A level above moves as a **prefix** of the
level below: `candidate` fast-forwards to one of those commits, carrying every artifact up to it in one
batch. The promoter takes the longest ready prefix (signed off by the required teams, no blocking findings,
unclaimed), runs the hard checks on its last commit, and moves the branch with a compare-and-swap. A
prefix never conflicts, and the promoted commit is exactly the one that was tested. The price: one artifact
that isn't ready holds back the ones after it.

The preset's `production` gate has `requires_human: true`: instead of promoting, it **escalates**. Every
escalation goes to a queue a person works through, without models or a Jev key:

```bash
tutorial/08_escalations.sh          # what waits for a person
tutorial/08_escalations.sh a-0001   # accept a-0001 at its gate, then run again
```

- `--retry`: teams look again (the executor treats it like a rejection).
- `--accept`: approve the current state at its gate. It replaces *judgement* (Jev, the human requirement, a
  failed join), never the hard checks, and it covers one gate only.
- `--drop`: retire the artifact (its merged code stays; reverting is a separate action).

While an escalation is open the artifact is **parked**: no team works on it, the router makes no fixes for
it, and a release train stops before it (except for the human gate's own request for approval). A dropped
artifact is left alone the same way, but its already-merged code still rides the train. Run
`agentswarm run` again after resolving; the next step acts on it. `agentswarm status` works even while a
project runs: it only reads the board.

Where: `gating/train.py`, `control/escalations.py`.

## Step 9 — Decomposition and joins

Some tasks are too big for one branch. The **decomposer** asks Jev "can this be done in one branch by one
team, without further design decisions?". If clearly not, a model splits it into child tasks, and Jev checks
that each child is clearly smaller (so a split can't just restate the task). The parent becomes an
**umbrella**; children get ids like `a-0003.1`, unique without asking anyone. When all children have reached
the next level, a **join gate** runs that level's hard checks and asks Jev whether the parts add up to the
whole; passing moves the umbrella up.

In shadow mode nothing is split (the question is only logged), so decomposition needs its points active:

```yaml
teams:
  - {use: decomposer, watches: raw, model: coder}
jev: {mode: shadow, active: ["size:*", "split:*", "join:*"]}
```

The whole flow (a goal split in two, children by train, umbrella by join, all the way to `candidate`) runs
offline, with scripted models and judges, in the tests this script runs:

```bash
tutorial/09_decomposition.sh
```

Where: `teams/decomposer.py`, `gating/join.py`, `tests/test_project.py`.

## Step 10 — The supervisor

A plain-code loop that runs every project step and escalates, once per state:

- an artifact with **no event for 30 minutes** (`supervisor.stall_after_minutes`);
- **3 rejections in a row** (`supervisor.max_rejections`);
- **more commits than** `budgets.max_rounds_per_artifact`.

It also watches for **drift**: after every 5 merged changes it asks Jev whether the recent work advances the
north star. With `drift` active, a clear "no" **pauses** the teams that start new work (analyzers and
gates carry on) until a person runs `agentswarm resume`.

Its rules run offline in:

```bash
tutorial/10_supervisor.sh
```

Where: `control/supervisor.py`.

## Step 11 — Making Jev act

Shadow mode collects evidence; the point is eventually to let Jev act where it has earned it.

1. **Per decision point.** `jev.active: ["gate:raw->experimental"]` makes that one point act while
   everything else stays in shadow, so you promote points one at a time, as their data allows.
2. **Thresholds from data.** `tune` finds, per question, the lowest pass and highest fail threshold that are
   right at least 95% of the time, with Wilson lower bounds, and prints YAML for the gate. The step's
   script does it on step 6's decisions, then the audit below:

   ```bash
   tutorial/11_jev_acts.sh   # tune, then the harder suite with the gate active, then the audit
   ```

3. **Audit.** Run a suite with the gate active and measure how often it was wrong (`agentswarm evaluate
   … --active "gate:*"`, then `agentswarm audit`).
   The bounds: false promotions ≤ 5%, false rejections ≤ 10%, escalations ≤ 30%, each judged by its 95%
   upper confidence limit, so a tiny sample is never "within bounds".
4. **When Jev is down.** Retries for timeouts and busy answers; optionally `jev.fallback: <model>`, a
   language model answering yes/no/unsure. Its decisions are marked `llm:` and never used for calibration.
5. **Cheap first.** `model_tiers: [small, large]` on the executor: each rejection moves it up a tier, and
   every commit records which model made it.

*Results:* tuned on the first two suites, then audited with the gate active, the gate made one wrong
promotion in 41 and no wrong rejection in 158. On 16 held-out tasks it had never been tuned on
(`examples/greeter/evaluation/heldout/`) it made no wrong ruling at all. False rejections and escalations
are within their bounds even by the cautious upper limit. False promotions aren't *proven* to be at or
below 5% yet: that takes about 73 known promotions without an error, and each task yields about one. Every
suite you add moves that number.

Two habits the reports enforce: `tune` writes thresholds exactly and comments a block out when any question
isn't ready, and `audit` prints one block per *cohort* (judge wording and model), never pooling them.

Where: `judging/judgement.py` (`ModeTable`), `judging/tuning.py`, `judging/audit.py`,
`judging/deciders.py`.

---

## Step 12 — A second domain: documents

Nothing in steps 1–11 is about code except the teams' instructions and the evidence they accept. The
`document` preset swaps those and keeps everything else:

| Software | Documents |
| --- | --- |
| raw → experimental → candidate → production | draft → reviewed → final → published |
| executor, decomposer | writer, outliner |
| integration tester, red team (evidence: a failing test) | consistency, fact and audience reviewers (evidence: a verbatim quote) |
| `ChangeReadiness` at the first gate | `SectionReadiness` (`on_topic`, `clear`, `complete`) |
| pytest as the hard check | the project's own `tools/check_doc.py` |

```bash
tutorial/12_documents.sh
```

A reviewer's problem only counts if it quotes at least 12 characters that really are in the file it names,
so a model can't invent a complaint. The run stops at the human publishing gate:

```bash
tutorial/12_publish.sh
```

The script approves it as a person would, runs the project again, and it is promoted to `published`. In the live run, the reviewers found a UK/US
spelling mismatch and a piece of jargon; both were minor, so they stayed on the board for a person rather
than becoming fix tasks.

Where: `teams/documents.py`, `core/presets/document.yaml`, `examples/handbook/`.

## Step 13 — Several projects, money and limits

**Spend is an event.** Every model run reports its tokens (counted per call, so a run that fails halfway
still records what it used), priced from `models.<name>.usd_per_million`, and lands on the board as `Spent`.
Reports start with it: `spent: $1.23 (4 unpriced model runs)`.

**Budgets.** `budgets.usd_per_day: 5` stops a project's teams from starting new actions once today's (UTC)
spend reaches $5; the supervisor escalates once, and work resumes the next day. Gates keep judging: Jev is
cheap, and the money budgets are about is in team actions.

**A swarm.** One process, several projects, one scheduler that caps concurrent model calls, Jev requests per
minute and sandbox runs:

```bash
tutorial/13_swarm.sh          # two quickstart projects
tutorial/13_swarm.sh live     # the greeter and the handbook together
```

Each project keeps its own board, decision log, repository and budget. If one project fails, it is reported
and the others finish; the command exits 1. `examples/swarm-live.yaml` runs the greeter and the handbook
together with real models.

Where: `board/spend.py`, `control/budget.py`, `control/scheduler.py`, `swarm.py`.

## Step 14 — What keeps it safe

An independent audit ([CODEX_AUDIT.md](../CODEX_AUDIT.md)) looked for the places where happy-path tests
don't reach, and Phase 7 closed them:

- **Code a model wrote can't reach the host.** Commands run on a throwaway copy of the checkout without
  `.git`; host git runs with hooks off and without your keys; every file the framework or a tool reads or
  writes stays inside the project, links included.
- **A crash can't lose a promotion.** A level branch moves in three recorded steps, and the next step repairs
  a move that was interrupted.
- **One owner.** A second process can't open a running project (`status` still works: it only reads the
  board). A worker whose claim was taken over has its result thrown away.
- **Evidence means evidence.** A test that couldn't run is never proof of a bug, a hidden-test outage is
  never ground truth, and parked or dropped work is left alone by every controller.

Where: [design/phase-7-hardening.md](../docs/design/phase-7-hardening.md), and section 15 of the reference.

## Step 15 — Does the swarm help? Measuring before optimizing

```bash
tutorial/15_measure.sh          # offline: the prompts, and the experiment's ground truth checked
tutorial/15_measure.sh live     # the experiment on two tasks
```

Everything so far shows that AgentSwarm *runs*. The question it was built for is different: **do
decomposition, specialized teams and independent checks beat simpler setups, and where?** The research
behind this is collected in [RESEARCH.md](../docs/RESEARCH.md). Its strongest finding sets the bar: one
model making several independent attempts, with tests picking the winner, beats most elaborate setups at
the same cost. A swarm has to beat *that*.

**The tasks.** Toy tasks can't show it: one model call solves them. The inventory suite
(`examples/inventory/`) is a small tool with several modules, and 25 tasks in five levels: one function;
several interacting functions; a multi-file change; a vague feature that needs design (categories, undo,
CSV import); and features across modules where integration and edge cases decide (orders that reserve
stock, FIFO valuation, kits). Each has hidden tests and a reference solution, and a test checks that every
reference passes and the untouched seed fails. Writing the references caught mistakes in the hidden tests,
and the models later caught a bug in a reference: ground truth needs checking too.

**The experiment** (`examples/goldilocks/complexity.yaml`) compares four setups with fixed models:

| Setup | What it tests |
| --- | --- |
| Claude Haiku, one attempt | the baseline |
| gemma4-e4b, one attempt | how big the gap is |
| gemma4-e4b, up to 3 attempts (`attempts: 3`), checks pick | what extra compute alone buys |
| AgentSwarm on gemma4-e4b: the same plus a decomposer and three narrow red teams | the hypothesis |

The swarm is the 3-attempt setup *plus* its own parts, so any difference comes from those parts. Getting
it there was instructive. Jev's untuned thresholds first kept the decomposer from splitting at all; once
it split, it split three levels deep into 57 tasks that stalled behind one slow model: the over-decomposition
the research warns about. The setup now splits once at most.

**Measuring single calls.** Whole-project runs mix the model with everything around it. `agentswarm probe`
asks each model the same one-shot prompts (write this code; find the bug in this change), scores them with
plain code, and counts invalid JSON, cut-off and missing answers separately. `agentswarm rate` has Jev rate
every task's difficulty. Routing between models is built (`agentswarm routing`), but stays unused until the
experiment shows there is something worth routing.

Where: [design/phase-8-goldilocks.md](../docs/design/phase-8-goldilocks.md), `evaluation/` (`compare.py`,
`probe.py`, `difficulty.py`, `imports.py`), [MODELS.md](../docs/MODELS.md).

---

## What is engine, and what is configuration

| Engine (code, the same for every project) | Configuration (per project) |
| --- | --- |
| board, events, views, claims | levels and their names |
| gates, merge queue, trains, joins | which checks run where; thresholds |
| policy, decision log, outcomes, reports | which teams watch which level, with which models |
| router, supervisor, escalation queue | which judge sits at which gate; which points are active |
| teams as classes (executor, analyzers, decomposer) | constraints, budgets, supervisor limits, presets |

The document domain (step 12) is the proof: only the right-hand column changed, plus three small reviewer
classes and one judge.

## What's still ahead

- **Phase 8's answer:** the complexity experiment on all 25 tasks, reported honestly, including where the
  swarm loses. What happens next depends on it.
- **Phase 9, conventions that hold** (layout, lint and type checks as hard checks), **Phase 10, robustness
  for high-level goals** (attempts selected by checks, protected tests, a clarifier, verified decompositions)
  and **Phase 11, project types as data**: planned, paused until Phase 8 answers.
- **Proving the gate's false-promotion rate:** more evaluation tasks, until about 73 known promotions.
- The backlog's deferred items ([BACKLOG.md](../docs/BACKLOG.md)).

## Where to read more

- [REFERENCE.md](../docs/REFERENCE.md): every config key, event, team, judge, report and command.
- [design/](../docs/design/): one document per phase, each ending with *every decision made during
  implementation* and why, including the bugs the tests caught.
- [BACKLOG.md](../docs/BACKLOG.md): what was deliberately postponed, why, and when it's due.
- [FRAMEWORK_PROPOSAL.md](../FRAMEWORK_PROPOSAL.md): the original design.
