# AgentSwarm Tutorial: Teams, Levels and a Calibrated Judge, One Layer at a Time

Oct 3, 2026 · Andreas Atle (built with Claude)

## How to use this tutorial

Read **Part 0** first: it explains what AgentSwarm is for and every piece you need, before any command.
Then work through the steps in order. Each one adds a single idea to the previous ones and shows you
where it lives in the code. [REFERENCE.md](REFERENCE.md) has every config key, event and command in
tables; this document explains why things are the way they are.

| Step | New idea | Needs |
| --- | --- | --- |
| 1 | The board: events in, views out | nothing |
| 2 | Claims: one team at a time | nothing |
| 3 | Levels are branches; a first run | nothing |
| 4 | A real executor in a sandbox | Ollama, Docker |
| 5 | Jev judges the gate, in shadow | Jev key |
| 6 | Ground truth without labelling | Ollama, Docker, Jev |
| 7 | Analyzers, findings and the router | Ollama, Docker, Jev |
| 8 | Release trains, human gates, escalations | Jev |
| 9 | Decomposition and joins | Jev |
| 10 | The supervisor | |
| 11 | Making Jev act: tuning, active points, audit | Jev |

Steps 1–3 run anywhere, offline, in seconds. Status: Phases 0–3 of the build plan are done; Phase 4 is in
its final step; Phases 5 (documents) and 6 (several projects) are designed but not built. Where a step
touches unfinished work, it says so.

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
log. Paste this into `uv run python`:

```python
from agentswarm.board.blackboard import Blackboard
from agentswarm.board.eventlog import InMemoryEventLog
from agentswarm.core.clock import FakeClock
from agentswarm.core.domain import Artifact, ArtifactCreated, ArtifactId, CommitPushed, Level, ProjectId, TeamName

clock = FakeClock()
board = Blackboard(InMemoryEventLog(ProjectId("demo"), clock))
task = Artifact(
    id=ArtifactId("a-0001"), task="Add farewell", branch="raw/a-0001-farewell",
    level=Level("raw"), created_by=TeamName("human"),
)
views = board.append([ArtifactCreated(artifact=task)])
print([a.id for a in views.artifacts_at(Level("raw"))])     # ['a-0001']

views = board.append([CommitPushed(artifact=task.id, commit="c0ffee")])
print(views.unjudged_commit(task.id), views.version(task.id))   # c0ffee 2
print(board.rebuild() == board.current())                    # True: replay gives the same views
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

Before a team works on an artifact, it **claims** it: a lease with an expiry. Continue in the same session:

```python
from datetime import timedelta
from agentswarm.board.claims import ClaimRegistry

claims = ClaimRegistry(board, clock)
mine = claims.claim(task.id, TeamName("executor"), timedelta(minutes=10))
print(mine is not None)                                                    # True
print(claims.claim(task.id, TeamName("red_team"), timedelta(minutes=10)))  # None: taken
clock.advance(timedelta(minutes=11))                                        # the executor crashed...
print(claims.claim(task.id, TeamName("red_team"), timedelta(minutes=10)) is not None)  # ...True: lease expired
```

A claim is appended **only if the log hasn't moved** since the registry checked that the artifact was free
(optimistic concurrency; SQLite's `BEGIN IMMEDIATE` underneath). Two teams racing for the same artifact can't
both win, and a crashed team's work is picked up again when its lease runs out. No locks between teams.

Where: `board/claims.py`. The race is tested in `tests/board/test_claims.py` by forcing the bad interleaving.

## Step 3 — Levels are branches; a first run

A project's **levels** are its maturity ladder. The lowest level holds one branch per artifact
(`raw/a-0001-...`); every level above is one shared branch with the level's name. Work moves up only
through a **gate**, one level at a time, and that is all the ordering there is.

Run the quickstart project (a fake executor, no model, no Jev, no Docker):

```bash
uv run agentswarm run examples/quickstart/project.yaml --root ~/agentswarm-runs/quickstart --task "Write a note" --task "Write another note"
```

```bash
git -C ~/agentswarm-runs/quickstart/repo log --oneline --graph --all
```

You'll see each artifact's own commit, merged into `experimental` with a merge commit. What happened, in
order (`run` repeats project *steps* until nothing happens):

1. The **team runner** for `fake_executor` found two artifacts at `raw` it hadn't handled, claimed each,
   checked out its branch in its own worktree (`worktrees/a-0001`), let the team act, appended its
   `CommitPushed`, and released the claim.
2. The **promoter** for the gate `raw → experimental` saw two unjudged commits. For each: claim, check out
   *exactly that commit* (detached, so the branch never moves), run the **hard checks** (here: every note
   has text) in the sandbox.
3. Passing artifacts went to the **merge queue**: merge onto `experimental`, run the checks again **on the
   merged result**, keep the merge only if they pass. So `experimental` always passes its own checks.
4. The board recorded `Promoted`, including the merge commit. Later teams find an artifact's own change in
   that merge commit (`merge^1...merge^2`), even after other work lands.

Try breaking it: edit the check to always fail and run again with a new root; the artifacts get
`Rejected` with a blocker finding, and the fake executor tries again.

Where: `workspace/git.py`, `gating/promotion.py`, `gating/merge_queue.py`, `teams/runner.py`, `project.py`.
`agentswarm config examples/quickstart/project.yaml` prints the whole configuration.

## Step 4 — A real executor in a sandbox

Now a model does the work. The greeter evaluation project has the executor and one gate, nothing else:

```bash
uv run agentswarm run examples/greeter/evaluation/project.yaml --root ~/agentswarm-runs/greeter-1 --task "Add a function farewell(name) to greeter.py that returns 'Goodbye, <name>!', with tests."
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
network, read-only root, dropped capabilities, CPU/memory/process limits, only the worktree mounted, your
uid, no keys in the environment.

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
uv run agentswarm run examples/greeter/project.yaml --root ~/agentswarm-runs/greeter-2 --task "Add two functions to greeter.py: farewell(name), which returns 'Goodbye, <name>!', and shout(name), which returns the hello greeting in upper case. Add tests for both."
```

```bash
uv run agentswarm calibrate ~/agentswarm-runs/greeter-2/decisions.sqlite
```

Every decision records its exact input, Jev's answers, the ruling, the mode, the Jev version that answered,
and a **judge version**: a hash of the questions' wording. Changing a word starts a new version, and no
report ever mixes versions, because answers to old wording say nothing about new wording.

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
uv run agentswarm evaluate examples/greeter/evaluation/suite.yaml --workdir ~/agentswarm-runs/eval-a
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
uv run agentswarm label ~/agentswarm-runs/eval-a/decisions.sqlite --limit 20
```

Where: `evaluation/` (suite, run, variants in `faults.py`, `mutation.py`), `judging/calibration.py`,
`judging/labelling.py`. Outcome sources rank human > planted > hidden tests = mutation = team result.

## Step 7 — Analyzers, findings and the router

Above the lowest level, teams never commit: only the merge queue writes level branches. They are
**analyzers**: they examine a merged artifact (in a detached checkout of its merge commit) and report
**findings**. The software preset has two at `experimental`: the **integration tester** (does the change
work with the code around it?) and the **red team** (does it break on hostile input?).

- **Evidence first.** An analyzer may report only problems backed by a test it wrote and ran that fails.
  Claims without one are dropped, so weak findings never block anything.
- **Relevance filters.** Before working, each analyzer asks Jev whether the change is worth its effort. In
  shadow mode it works anyway, and whether it found something becomes the automatic outcome of that
  decision. In the live run above, Jev said "not worth it" twice (0.08 and 0.15), the analyzers found
  nothing, and both decisions were scored right.
- **Sign-offs.** An analyzer ends with `SignedOff` for the version it examined; a gate above can require it.
- **The finding router.** Teams only report; plain code acts. Each open major or blocker finding above the
  lowest level becomes one **fix artifact** at the lowest level (its task carries the finding and the
  failing test), and the finding is resolved when the fix reaches the finding's level.

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
uv run agentswarm escalations examples/greeter/project.yaml --root ~/agentswarm-runs/greeter-2
```

```bash
uv run agentswarm resolve examples/greeter/project.yaml --root ~/agentswarm-runs/greeter-2 a-0001 --accept --note "reviewed"
```

- `--retry`: teams look again (the executor treats it like a rejection).
- `--accept`: approve the current state at its gate. It replaces *judgement* (Jev, the human requirement, a
  failed join), never the hard checks, and it covers one gate only.
- `--drop`: retire the artifact (its merged code stays; reverting is a separate action).

Run `agentswarm run` again after resolving; the next step acts on it.

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

Where: `teams/decomposer.py`, `gating/join.py`. The whole flow (a goal split in two, children by train,
umbrella by join, all the way to `candidate`) is in `tests/test_project.py`.

## Step 10 — The supervisor

A plain-code loop that runs every project step and escalates, once per state:

- an artifact with **no event for 30 minutes** (`supervisor.stall_after_minutes`);
- **3 rejections in a row** (`supervisor.max_rejections`);
- **more commits than** `budgets.max_rounds_per_artifact`.

It also watches for **drift**: after every 5 merged changes it asks Jev whether the recent work advances the
north star. With `drift` active, a clear "no" **pauses** the teams that start new work (analyzers and
gates carry on) until a person runs `agentswarm resume`.

Where: `control/supervisor.py`.

## Step 11 — Making Jev act

Shadow mode collects evidence; the point is eventually to let Jev act where it has earned it.

1. **Per decision point.** `jev.active: ["gate:raw->experimental"]` makes that one point act while
   everything else stays in shadow, so you promote points one at a time, as their data allows.
2. **Thresholds from data.** `tune` finds, per question, the lowest pass and highest fail threshold that are
   right at least 95% of the time, with Wilson lower bounds, and prints YAML for the gate:

   ```bash
   uv run agentswarm tune ~/agentswarm-runs/eval-a/decisions.sqlite
   ```

3. **Audit.** Run a suite with the gate active and measure how often it was wrong:

   ```bash
   uv run agentswarm evaluate examples/greeter/evaluation/hard/suite.yaml --workdir ~/agentswarm-runs/eval-b --active "gate:*"
   ```

   ```bash
   uv run agentswarm audit ~/agentswarm-runs/eval-b/decisions.sqlite
   ```

   The bounds: false promotions ≤ 5%, false rejections ≤ 10%, escalations ≤ 30%, each judged by its 95%
   upper confidence limit, so a tiny sample is never "within bounds".
4. **When Jev is down.** Retries for timeouts and busy answers; optionally `jev.fallback: <model>`, a
   language model answering yes/no/unsure. Its decisions are marked `llm:` and never used for calibration.
5. **Cheap first.** `model_tiers: [small, large]` on the executor: each rejection moves it up a tier, and
   every commit records which model made it.

*Status:* the Phase 4 runs (shadow evaluation of both suites, tuning, an active evaluation, the audit
against these bounds) are in progress; their results will be added here.

Where: `judging/judgement.py` (`ModeTable`), `judging/tuning.py`, `judging/audit.py`,
`judging/deciders.py`.

---

## What is engine, and what is configuration

| Engine (code, the same for every project) | Configuration (per project) |
| --- | --- |
| board, events, views, claims | levels and their names |
| gates, merge queue, trains, joins | which checks run where; thresholds |
| policy, decision log, outcomes, reports | which teams watch which level, with which models |
| router, supervisor, escalation queue | which judge sits at which gate; which points are active |
| teams as classes (executor, analyzers, decomposer) | constraints, budgets, supervisor limits, presets |

A document project (Phase 5) is meant to be only the right-hand column: the same engine with document
teams, a document judge and a document preset. The handbook example's checker is already in
`examples/handbook/`.

## What's still ahead

- **Phase 4** results: the active evaluation and audit (above).
- **Phase 5, documents** ([design](design/phase-5-documents.md)): quotes as evidence instead of tests; writer,
  outliner, consistency, fact and audience reviewers; a `document` preset; a handbook through
  `draft → reviewed → final → published`.
- **Phase 6, several projects** ([design](design/phase-6-multi-project.md)): spend recorded on the board,
  a shared scheduler with fair grants and rate limits, budgets enforced, `agentswarm swarm`.

## Where to read more

- [REFERENCE.md](REFERENCE.md): every config key, event, team, judge, report and command.
- [design/](design/): one document per phase, each ending with *every decision made during
  implementation* and why, including the bugs the tests caught.
- [BACKLOG.md](BACKLOG.md): what was deliberately postponed, why, and when it's due.
- [FRAMEWORK_PROPOSAL.md](../FRAMEWORK_PROPOSAL.md): the original design.
