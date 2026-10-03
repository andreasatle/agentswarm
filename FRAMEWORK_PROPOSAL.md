# Proposal: Loosely Coupled Teams Toward a North Star

*A framework for multi-agent work on code, documents and other git-versioned projects*

| | |
| --- | --- |
| Status | Draft for feedback |
| Date | 2 October 2026 |
| Author | Andreas Atle (design drafted with Claude) |
| Scope | Architecture and design. No implementation yet. |

---

## 1. Summary

Work on a project is driven by independent **teams** (executors, testers, reviewers, a security team, and more). Each team watches a shared, per-project **blackboard** and does one kind of work. Teams never call each other. They react to the state of the project.

Work products (**artifacts**) live as **git branches** and mature through **levels**, e.g. `raw → experimental → candidate → production` for software, or `draft → reviewed → final → published` for a document. Moving an artifact up a level requires passing a **gate**: deterministic checks first (tests, builds, scanners), then a set of narrow judgement calls made by **Jev**, a cheap and calibrated decision model.

**LLMs produce** (code, text, tests, plans). **Jev decides** (is this for me, is it good enough, are we drifting). **Deterministic code controls** (claims, merges, budgets, scheduling). A **supervisor** watches each project for drift and stalls, and escalates to a human when needed.

The design combines three established patterns:

- blackboard architectures
- level-triggered reconciliation loops (as in Kubernetes controllers)
- CI/CD promotion pipelines with merge queues

It is aimed at the failure modes seen in the earlier *forge* attempts: rigid ordering between roles that ended in deadlock, and an expensive LLM critic.

---

## 2. Background: lessons from forge

Forge used two roles, a **planner** that created tasks and a **worker** that executed them, plus an **LLM critic** that looked at a task and its solution and decided whether it was good enough or needed improvement. Git was used for every kind of project, not only code. Two projects exercised it: a software project and a document.

What went wrong, or was costly:

1. **Ordering deadlock.** Deciding whether tests should be written before or after code led to roles waiting on each other. Any design where roles depend on *each other's* output in a fixed order risks cycles.
2. **Rigid decomposition.** The strict plan-then-execute workflow assumes two levels of granularity. Real work sometimes needs none (small goal) and sometimes several (goal → features → tasks).
3. **An LLM as critic.** Every verdict was a full LLM call, and the "confidence" it reported was self-assessed, not calibrated.

What carries over: git as the universal artifact store, and the idea that one engine should serve several kinds of projects.

---

## 3. Goals and non-goals

**Goals**

- **G1. No coordination deadlocks:** no team ever waits on another team.
- **G2. Domain-independent engine:** software and documents are configurations, not separate code.
- **G3. Typed end to end:** the blackboard, findings, LLM outputs, Jev questions and answers are Pydantic models. No string parsing, no dict indexing.
- **G4. Cheap, measurable judgement:** judgement calls are made by Jev, are logged with their outcome, and can be calibrated.
- **G5. Deterministic control:** everything that must be reliable (claims, merges, budgets, promotion rules) is plain code.
- **G6. Testable without models:** every model-backed component has a fake, so the whole system runs offline and deterministically in tests.
- **G7. Multiple projects at once,** sharing resources fairly.

**Non-goals (for now)**

- A general agent framework for arbitrary workflows.
- Real-time collaboration with humans inside the loop. Humans enter via escalations and reviews.
- Fully autonomous production deployment. Promotion to the top level may require a human, configurable per project.
- Distributed execution across machines. A single host is enough to start.

---

## 4. Prior art

| Pattern | What we take from it |
| --- | --- |
| **Blackboard systems** (Hearsay-II, 1970s) | Specialists cooperate through shared state, not by calling each other. A control component decides who acts. |
| **Reconciliation loops** (Kubernetes controllers) | Each controller compares desired and actual state and takes one step to close the gap. They're *level-triggered* (react to state, not to a sequence of events), idempotent, and order-independent, which avoids deadlocks. |
| **Promotion pipelines, GitOps, merge queues** (bors, GitHub merge queue) | Code matures through environments or branches, gates guard each step, and a queue serializes merges so the target branch is always green. |
| **Event sourcing** | State is a fold over an append-only event log. That gives history, replay and audit for free. |
| **Role-based LLM frameworks** (MetaGPT, ChatDev, CrewAI) | Specialized roles work better than one generalist. These frameworks mostly run roles in a *sequence*; we run them as independent loops. |
| **Orchestration toolkits** (LangGraph, AutoGen) | Graph and message-passing execution models. Useful as references; we deliberately avoid explicit control-flow graphs between roles. |
| **Model cascades** (e.g. FrugalGPT) | Try a cheap model first, check, and escalate to a stronger one only when needed. |
| **Mutation testing** | A deterministic way to check that tests actually test something. |

The combination aimed at here (independent teams as reconciliation loops over git maturity levels, with a calibrated gatekeeper) is less common than any of the parts.

---

## 5. Design principles

1. **Teams depend on levels, never on teams.** A team watches one level and acts on artifacts there. Ordering emerges from maturity: an artifact can only move up when its gate passes. No cycles are possible.
2. **Level-triggered, not edge-triggered.** A team looks at *the current state* ("which artifacts at my level need me?"), not at an event stream it must not miss. Missed or duplicated events are harmless.
3. **Producers, judges and checks are different things.** LLMs produce, Jev judges, deterministic checks verify. No component does two of these.
4. **Hard checks before judgement.** If something can be verified mechanically (tests, builds, schema, links), it is, and Jev judges only what remains.
5. **Everything is typed and logged.** Every decision, with its inputs, probabilities and eventual outcome, is data for calibration and debugging.
6. **One blackboard per project.** Judgements are always relative to one north star.
7. **Fail toward escalation.** When unsure, stop and ask (a human, or a stronger model) rather than guess.

---

## 6. Architecture overview

```
                         ┌────────────────────── shared across projects ──────────────────────┐
                         │  Scheduler (budgets, rate limits)   Team catalog   Decision log     │
                         └──────────────▲─────────────────────────▲──────────────────▲────────┘
                                        │ grants                  │ definitions      │ every decision
┌───────────────────────────── project: one north star ───────────┼──────────────────┼────────┐
│                                                                 │                  │        │
│   Blackboard  =  append-only event log  +  derived views        │                  │        │
│   (artifacts, levels, findings, claims)                         │                  │        │
│        ▲   ▲   ▲   ▲                                                                        │
│        │   │   │   │   read state, claim, write events                                      │
│   ┌────┴┐ ┌┴───┐ ┌┴──────┐ ┌┴────────┐      ┌────────────┐                                  │
│   │exec │ │unit│ │deep   │ │security │ ...  │ Supervisor │  drift, stalls, escalation        │
│   │team │ │test│ │test   │ │team     │      └────────────┘                                  │
│   └──┬──┘ └─┬──┘ └──┬────┘ └──┬──────┘                                                      │
│      │      │       │         │      each team: relevant? (Jev) → claim → act (LLM/tools)    │
│      ▼      ▼       ▼         ▼                                                             │
│   git:  raw/*  ──gate──►  experimental  ──gate──►  candidate  ──gate──►  production          │
│                (merge queue per level; gate = hard checks → Jev judge → policy)              │
└──────────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 7. Domain model

All types are Pydantic models; enums are `StrEnum`. Levels and kinds are per-domain configuration, shown here with software values.

```python
class ArtifactId(str): ...                   # e.g. "a-0042"
class TeamName(str): ...


class Level(StrEnum):                        # from the domain configuration
    RAW = "raw"
    EXPERIMENTAL = "experimental"
    CANDIDATE = "candidate"
    PRODUCTION = "production"


class Artifact(BaseModel):
    """One unit of work: a task and the git branch that holds its current state."""
    id: ArtifactId
    task: str                                # what this artifact is meant to achieve
    branch: str                              # e.g. "raw/a-0042-retry-policy"
    level: Level
    parent: ArtifactId | None = None         # set when created by decomposition
    created_by: TeamName


class FindingKind(StrEnum):
    TEST_FAILURE = "test_failure"
    BUILD_FAILURE = "build_failure"
    OUT_OF_SCOPE = "out_of_scope"
    INCOMPLETE = "incomplete"
    SECURITY = "security"
    INCONSISTENT = "inconsistent"
    TOO_LARGE = "too_large"                  # should be decomposed


class Severity(StrEnum):
    INFO = "info"
    MINOR = "minor"
    MAJOR = "major"
    BLOCKER = "blocker"


class Finding(BaseModel):
    """Typed feedback. A rejected gate always produces at least one."""
    artifact: ArtifactId
    level: Level
    team: TeamName
    kind: FindingKind
    severity: Severity
    detail: str                              # where and why; see §11.6 for where it comes from
    resolved_by: ArtifactId | None = None


class Claim(BaseModel):
    """A lease: one team works on one artifact until it releases or the lease expires."""
    artifact: ArtifactId
    team: TeamName
    expires_at: datetime
```

**Events** are a discriminated union, one model per kind. Nothing changes the blackboard except appending an event.

```python
class ArtifactCreated(BaseModel):
    kind: Literal["artifact_created"] = "artifact_created"
    artifact: Artifact

class Claimed(BaseModel):
    kind: Literal["claimed"] = "claimed"
    claim: Claim

class Released(BaseModel):
    kind: Literal["released"] = "released"
    artifact: ArtifactId
    team: TeamName

class CommitPushed(BaseModel):
    kind: Literal["commit_pushed"] = "commit_pushed"
    artifact: ArtifactId
    commit: str

class FindingAdded(BaseModel):
    kind: Literal["finding_added"] = "finding_added"
    finding: Finding

class Promoted(BaseModel):
    kind: Literal["promoted"] = "promoted"
    artifact: ArtifactId
    from_level: Level
    to_level: Level
    gate_decision: DecisionId                # link to the decision log

class Rejected(BaseModel):
    kind: Literal["rejected"] = "rejected"
    artifact: ArtifactId
    level: Level
    findings: list[Finding]
    gate_decision: DecisionId

class Escalated(BaseModel):
    kind: Literal["escalated"] = "escalated"
    artifact: ArtifactId | None
    reason: str

Event = Annotated[
    ArtifactCreated | Claimed | Released | CommitPushed | FindingAdded | Promoted | Rejected | Escalated,
    Field(discriminator="kind"),
]

class Envelope(BaseModel):
    seq: int                                 # position in the log
    at: datetime
    project: ProjectId
    event: Event
```

---

## 8. The blackboard

### 8.1 Event log and views

- **Storage:** one append-only log per project. SQLite with a single `events` table (seq, at, kind, JSON payload) is enough to start. It gives transactions for claims, and replay is just reading in order.
- **Views** are pure functions of the log, rebuilt or incrementally updated:
  - `artifacts_at(level)`: artifacts currently at a level
  - `open_findings(artifact)`: findings not yet resolved
  - `active_claims()`: leases not expired or released
  - `history(artifact)`: everything that happened to it, in order (the state sent to Jev and LLMs)
- **Replay** rebuilds every view from the log, so debugging means replaying up to the event in question.

### 8.2 Claims and concurrency

- A team **claims** an artifact before acting. The claim is a lease with an expiry, written in a transaction that fails if an active claim exists. That gives exactly one team per artifact at a time, without locks between teams.
- If a team crashes, its lease expires and the artifact becomes available again.
- Teams on *different* artifacts never interfere. Teams on the same artifact are serialized by claims.

### 8.3 What goes into a judgement's state

Jev and the LLMs see a **typed context** built from the views, sent as JSON:

```python
class ArtifactContext(BaseModel):
    north_star: str
    artifact: Artifact
    history: list[Event]                     # this artifact's events, oldest first
    open_findings: list[Finding]
    diff_summary: str                        # produced by a tool (git diff --stat, or an LLM summary)
```

Keep it small. Jev reads only what it's given, and its window is limited (state plus the longest question must fit in 32k tokens). Big diffs are summarized, never pasted whole.

---

## 9. Git integration

| Concept | Git |
| --- | --- |
| Project | One repository |
| Level | A long-lived branch per level above `raw`: `experimental`, `candidate`, `production` |
| Artifact at `raw` | A short-lived branch `raw/<artifact-id>-<slug>`, forked from `experimental` |
| Promotion | Merge through the **merge queue** of the target level |
| A team's workspace | A `git worktree` per claim, so teams never share a working directory |

**Merge queue, one per level:** promotions into a level are serialized. Each candidate is rebased onto the current tip, the hard checks rerun on the result, and only then is it merged. The level branch therefore always passes its own checks.

**Conflicts:** a rebase conflict in the queue rejects the promotion with a `Finding(kind=INCONSISTENT)`. An executor team resolves it on a fresh raw branch.

**Demotion and rollback:** if a problem is found at a higher level, the fix goes through the levels like any other change. Reverting a merge on a level branch is an explicit, logged action by the supervisor or a human, never implicit.

**Documents:** to keep line-based merges workable, write one sentence per line and keep each section in its own file. Rendered output is unaffected.

---

## 10. Teams

### 10.1 The team contract

```python
class Team(Protocol):
    name: TeamName
    watches: Level                           # the level this team works on

    def relevant(self, ctx: ArtifactContext) -> Relevance:
        """Cheap: is there something for this team here? Usually a Jev question."""

    async def act(self, ctx: ArtifactContext, workspace: Worktree) -> list[Event]:
        """Expensive: LLM calls, tools, test runs. Returns events; never writes the board itself."""


class Relevance(StrEnum):
    YES = "yes"
    NO = "no"
    UNSURE = "unsure"                        # threshold not met either way: skip now, the supervisor may escalate
```

### 10.2 The loop every team runs

```
loop:
    candidates = artifacts_at(watches) − claimed − recently_handled_by_me
    for each candidate (by priority):
        ctx = build_context(candidate)
        if relevant(ctx) is YES:
            if claim(candidate):                       # transaction; may fail if another team got it
                wait for a scheduler grant
                events = act(ctx, worktree(candidate))
                append(events); release(candidate)
    sleep until the board changes or a timer fires
```

`recently_handled_by_me` (keyed by the artifact's last commit) prevents a team from reacting twice to the same state, so the team is idempotent.

### 10.3 Team catalog

**Software**

| Team | Watches | Does | Produces |
| --- | --- | --- | --- |
| Decomposition | any | Splits artifacts judged too large into child artifacts | `ArtifactCreated` × n |
| Executor | raw | Implements the task on the raw branch; addresses open findings | commits |
| Unit tester | raw | Writes and runs unit tests for the change | commits, findings |
| Integration tester | experimental | Deeper and cross-component tests | findings |
| Adversarial tester (red team) | experimental | Tries to break the change: edge cases, misuse, fuzzing | findings |
| Security | candidate | Scanners, plus an LLM review when Jev flags sensitive changes | findings |
| Reviewer | candidate | Readability, consistency with conventions | findings |

**Document**

| Team | Watches | Does |
| --- | --- | --- |
| Outliner (decomposition) | any | Splits the brief into sections and sections into parts |
| Writer | draft | Writes and revises sections |
| Editor | draft | Clarity, structure, length |
| Consistency checker | reviewed | Terminology and claims across sections |
| Fact checker | reviewed | Verifies claims against sources (LLM with sources; Jev only flags *which* claims need checking) |
| Audience reviewer | final | Fit for the intended reader |

Teams are **definitions** in a shared catalog and are instantiated per project with their own configuration (model, prompts, thresholds).

### 10.4 Decomposition without a planner role

No dedicated planning phase exists. Any artifact can be judged too large:

- Jev Noul: *"Can this task be done in one branch by one team, without further design decisions?"*
- If clearly no, the decomposition team splits it into children. The parent stays open and is resolved when its children reach a level, via a **join check** (§11.4).
- Progress guard, another Noul: *"Is each child clearly smaller than the parent?"* That prevents a split that merely restates the task.
- A depth limit (configurable, e.g. 3) escalates instead of splitting again.

---

## 11. Gates

### 11.1 Definition

```python
class Gate(BaseModel):
    from_level: Level
    to_level: Level
    hard_checks: list[CheckSpec]             # deterministic; run in order, stop at first failure
    judge: type[BaseModel]                   # a jev.py spec: Annotated[NoulAnswer/ScoreAnswer/ChoiceAnswer, Ask(...)]
    policy: PolicySpec                       # thresholds per judge field
    per_item: list[ItemCheck] = []           # questions asked once per item (§11.6, source 3)
    explain_on_reject: bool = False          # call an LLM explainer when rejecting (§11.6, source 4)
    requires_human: bool = False             # e.g. for production
```

### 11.2 Evaluation

```
1. hard checks       → any failure: REJECT with typed findings (no Jev call)
2. Jev judge         → one call, all questions at once
3. policy            → each field compared to its thresholds:
                          all clearly pass        → PROMOTE (enqueue in the merge queue)
                          any clearly fails       → REJECT; each failed field becomes a Finding (§11.6)
                          anything in between     → ESCALATE
4. merge queue       → rebase, rerun hard checks, merge or REJECT(INCONSISTENT)
```

### 11.3 Example judges

```python
class RawToExperimental(BaseModel):
    in_scope: Annotated[NoulAnswer, Ask(
        "Does the change stay within its task, without unrequested modifications?")]
    tests_relevant: Annotated[NoulAnswer, Ask(
        "Do the new tests exercise the behaviour the task describes, rather than trivially passing?")]
    complete: Annotated[NoulAnswer, Ask(
        "Does the change address every part of the task?")]


class CandidateToProduction(BaseModel):
    touches_sensitive: Annotated[NoulAnswer, Ask(
        "Does the change handle secrets, authentication, permissions or untrusted input?",
        yes="It reads, stores or transmits credentials, changes access rules, or parses external input",
        no="None of these")]
    drift: Annotated[ScoreAnswer, Ask(
        "How well does this change serve the north star?",
        levels=("Directly advances it", "Related but not advancing it", "Serves other goals"))]
```

`touches_sensitive` is a **routing** question rather than a pass/fail. When it's yes, the gate requires an extra LLM security review before passing.

### 11.4 Join checks

A parent artifact created by decomposition has its own gate. Hard checks run on the merged result of its children, and a Jev question asks *"Together, do the children accomplish the parent task?"* This is what ensures that the parts add up to the whole.

### 11.5 Policy and thresholds

- Thresholds are **per field and per gate**, stored in configuration, not code.
- Starting values are conservative. In the public evaluation, Choice was overconfident on overlapping categories (at 0.99 it was right about 92% of the time), and Noul was well calibrated. So start with Noul pass ≥ 0.9 and fail ≤ 0.2, and set Choice and Score thresholds stricter.
- Thresholds are tuned from the decision log (§15) once outcomes are known.

---

### 11.6 Where a finding's message comes from

Jev's answers carry no payload. It returns probabilities over options defined in advance: in Rust terms, the enum variant without its data. It can say *that* a property fails and *which* predefined kind of failure it is, but never *where* or *why* in its own words. A finding's `kind` and `severity` come from Jev; its `detail` must come from somewhere else. There are four sources, used together:

| Source | Gives | Cost |
| --- | --- | --- |
| **1. The question itself** | A fixed message per question: a failed `complete` becomes `Finding(kind=INCOMPLETE, detail="Does not address every part of the task")` | None |
| **2. Choice over reasons** | "What is the main problem?" over options such as `missing_tests`, `vague_step`, `wrong_format`; the chosen option's description is the message | One question |
| **3. Per-item questions** | The location: code asks the same question once per item (plan step, section, changed file) and attaches the item to the finding | One call, with one field per item |
| **4. LLM explainer, on reject only** | Free text: which line, what's missing, a suggested fix | One LLM call per *rejection*, not per decision |

**Per-item questions (source 3).** Your code supplies the payload; Jev supplies the variant:

```python
class ItemCheck(BaseModel):
    items: ItemSource                        # e.g. PLAN_STEPS, CHANGED_FILES, SECTIONS
    question: Ask                            # e.g. Ask("Is this step a concrete action?")
    kind: FindingKind                        # the finding emitted when it fails
```

At evaluation time the gate builds one Jev field per item (`step_1`, `step_2`, … via `create_model`, as `jev.py` already does for specs) and sends them in a single call. A call has one state shared by all its questions, so the state is the whole context (the full plan, or the diff) and each question names its item: "Is step 3 a concrete action?". A failure becomes `Finding(kind=VAGUE_STEP, detail=f"step {i}: {step.text}")`.

Naming items in the question works when they're easy to identify in the state (numbered steps, file names, section headings). For large items, such as long files, one call per item with only that item in the state keeps each state small, at the cost of more calls.

**LLM explainer (source 4).** When `explain_on_reject` is set and the gate rejects, an LLM receives the artifact, the failed questions and their probabilities, and writes `detail` for each finding as a structured output. The failed questions focus it on *those* problems rather than a general review. Because most decisions pass, the explainer runs on a minority of artifacts. The Jev verdict is never overridden by the explainer; it only describes.

**Choosing per gate:**

- **Low levels** (raw → experimental): sources 1 and 3 are usually enough. The executor fixes "step 3 is not concrete" without further explanation.
- **High levels** (candidate → production) and **documents:** add source 4. Rejections there are rarer and the fix is less obvious, so a written explanation pays for itself.
- **When the executor repeatedly fails to fix a finding:** switch that artifact to source 4 for its next rejection. The supervisor's stall detection can trigger this.

## 12. Jev's role

### 12.1 Decision catalog

| Where | Question type | Example |
| --- | --- | --- |
| Team relevance filter | Noul | "Does this change need new or updated tests?" |
| Size check | Noul | "Can this be done in one branch without further design decisions?" |
| Gate judgements | Noul, Score | §11.3 |
| Per-item checks | Noul per item | "Is this step a concrete action?" asked for each plan step, in one call (§11.6) |
| Finding triage | Score, Noul | severity; "Is this a real problem rather than a misunderstanding?"; "Is it a duplicate of an open finding?" |
| Model routing | Choice | which LLM tier to use for an action (§13.3) |
| Drift monitor | Score | recent activity against the north star |
| Stall detection | Noul | "Has the artifact meaningfully changed in the last N rounds?" |
| Oscillation guard | Noul | "Does this commit undo a recent commit?" |

### 12.2 Rules for writing questions

- **One narrow property per question.** Narrow yes/no questions were where Jev proved calibrated.
- **Make options exclusive** with "not for…" clauses. Overlapping options caused the overconfidence.
- **Questions about form, not deep correctness.** "Are claims attributed?" is a good question; "is the claim true?" is not. Correctness belongs to hard checks or LLM teams.
- **Typed state.** Send `ArtifactContext` as JSON (tested: Jev accepts objects as state and decided identically to prose in a first comparison).

### 12.3 Shadow mode

Every new Jev decision point starts in **shadow mode**: it is logged but doesn't act. The existing behaviour (no gate, or the old LLM critic) stays in control. Promote a decision point to *active* only after its logged decisions have been compared with outcomes (§15.2).

### 12.4 Fallback

The `Decider` interface has three implementations: `JevDecider`, `LLMDecider` (an LLM with constrained output, uncalibrated, slower) and `FakeDecider`. If Jev is unreachable, the framework switches to `LLMDecider` and marks those decisions in the log, or pauses gates, configurable per project.

---

## 13. LLMs

### 13.1 Worker interface

```python
class Worker(Protocol):
    async def run(self, ctx: ArtifactContext, instruction: Instruction,
                  output: type[T]) -> T: ...
```

- `output` is a Pydantic model. The LLM is called with structured outputs (constrained decoding), so the result is always valid against the schema.
- Implementations: `ClaudeWorker`, `OllamaWorker`, `FakeWorker`.

### 13.2 Instructions come from typed findings

When a gate rejects, its findings become the next instruction:

```python
class Instruction(BaseModel):
    goal: str                                # the artifact's task
    must_fix: list[Finding]                  # from the last rejection
    constraints: list[str]                   # from the project configuration
```

A failed Jev question maps to a specific finding ("not complete: misses part 2 of the task"), which is a much better instruction than "improve this". How specific `detail` is depends on the sources a gate uses (§11.6).

### 13.3 Cascade routing

```
action ─► Jev: which tier? ─► cheap model ─► gate ──pass──► done
                                              │ fail (once)
                                              ▼
                                         strong model ─► gate ──fail──► escalate
```

The router can be cautious because the gate catches wrong guesses. Tier choices and outcomes are logged, and the routing threshold is tuned from them.

### 13.4 Tool use and execution

Executor and tester teams run code produced by LLMs. All execution happens in a **sandbox**: a container or VM per worktree, with no network by default, CPU, memory and time limits, and no access to secrets.

---

## 14. Supervisor and scheduler

### 14.1 Supervisor (one per project, timer-driven)

- **Drift:** every N events, score recent activity against the north star (Jev Score). A sustained "tangential" or "drifted" pauses new raw work and escalates.
- **Stalls:** an artifact stuck at a level for N rounds without new commits, or rejected K times in a row, gets escalated.
- **Leases:** expire stale claims.
- **Budgets:** stops the project when its spend or round budget is exhausted.
- **Escalations** go to a human queue with the artifact's context and the decisions that led there.

### 14.2 Scheduler (shared across projects)

- Holds the **resources**: LLM tokens and spend per project, concurrent LLM calls, Jev rate limits (about 1,200 requests per minute), sandbox slots.
- Teams ask for a **grant** before `act`. The scheduler decides by priority and fairness across projects.
- It makes **no judgements** about content. It is a pure resource manager.

---

## 15. Observability and evaluation

### 15.1 Decision log

Every judgement is recorded:

```python
class Decision(BaseModel):
    id: DecisionId
    project: ProjectId
    point: DecisionPoint                     # e.g. "gate:raw->experimental", "relevance:unit_tester"
    mode: Literal["shadow", "active"]
    state_hash: str                          # the exact input, stored separately
    answers: dict[FieldName, AnswerRecord]   # probabilities and confidence per question
    verdict: Verdict
    model: str                               # e.g. jev-1.13.0 (pin versions in production)
    at: datetime
    outcome: Outcome | None = None           # filled in later
```

(`answers` is keyed by field name because it spans different judge specs. That is the one place where generic storage is unavoidable; it is never indexed by hand-written strings in code.)

### 15.2 Outcomes, i.e. ground truth for free

Each decision gets an outcome later, from what actually happened:

| Decision | Outcome signal |
| --- | --- |
| Gate passed | Did the artifact later get rejected at a higher level, or rolled back? |
| Gate rejected | Did the fix address exactly the reported finding? |
| "Too large" | Did the executor succeed, or was it decomposed anyway? |
| Relevance yes or no | Did the team's work lead to a finding or commit, or did it do nothing? |
| Cheap-tier routing | Did the cheap model's output pass the gate? |

With outcomes attached, the evaluation script from the Jev tutorial (step 9 / step 11 style) gives **calibration per decision point**: reliability tables, the noise level at the sample size, and accuracy versus coverage for choosing thresholds.

### 15.3 Metrics

- **Throughput:** artifacts promoted per level per day.
- **Rejections:** rejection rate per gate, by finding kind.
- **Escalations:** escalation rate, by cause.
- **Cost:** per promoted artifact, split into LLM, Jev and compute.
- **Drift:** the drift score over time.
- **Lead time:** time spent at each level.

---

## 16. Security

- **Keys:** each external service has one client module that is the only reader of its key, is HTTPS-only, and has its host fixed in code. This is the pattern already used for Jev (`jev_client.py`). Keys never enter any LLM or Jev context.
- **Sandboxing:** all LLM-written code runs in a sandbox (§13.4).
- **Untrusted content:** artifacts, findings and anything produced by LLMs (especially red-team output) are **data**. They are never treated as instructions to the framework itself. Prompt construction always puts this content in clearly delimited, typed fields.
- **Secrets in artifacts:** the security gate includes a deterministic secret scanner (e.g. gitleaks) before any promotion.
- **Project isolation:** a team instance only ever sees its own project's blackboard and repository.

---

## 17. Configuration

One file per project (YAML, validated into Pydantic models on load):

```yaml
project: retry-client
domain: software
north_star: >
  An Ollama client that survives transient server failures without
  string-based control flow, with full test coverage of retry behaviour.

levels: [raw, experimental, candidate, production]

teams:
  - {use: executor,           watches: raw,          model_tiers: [small, large]}
  - {use: unit_tester,        watches: raw}
  - {use: decomposition,      watches: any,          max_depth: 3}
  - {use: integration_tester, watches: experimental}
  - {use: red_team,           watches: experimental}
  - {use: security,           watches: candidate}

gates:
  - from: raw
    to: experimental
    hard_checks: [build, unit_tests]
    judge: RawToExperimental
    thresholds: {in_scope: {pass: 0.9, fail: 0.2}, tests_relevant: {pass: 0.9, fail: 0.2}, complete: {pass: 0.9, fail: 0.2}}
  - from: candidate
    to: production
    hard_checks: [build, all_tests, secret_scan, dependency_audit]
    judge: CandidateToProduction
    requires_human: true

budgets: {usd_per_day: 20, max_rounds_per_artifact: 8}
jev_mode: shadow            # shadow | active, per project at first; later per decision point
```

A document project uses the same shape, with `domain: document`, levels `[draft, reviewed, final, published]`, writer, editor and fact-checker teams, and hard checks such as `builds`, `links_resolve` and `style_lint`.

---

## 18. Failure modes and mitigations

| Failure | Example | Mitigation |
| --- | --- | --- |
| **Deadlock** | Tests wait for code, code waits for tests | Impossible by design: teams depend on levels only (§5.1) |
| **Livelock / oscillation** | Executor and tester keep undoing each other | Oscillation-guard Noul; max rounds per artifact; supervisor escalation |
| **Duplicate work** | Two teams fix the same finding | Claims; findings record `resolved_by`; duplicate-finding Noul |
| **Endless decomposition** | Tasks split forever | Depth limit; "each child smaller?" Noul; escalate when unsure |
| **Finding floods** | Red team reports many weak issues | Triage Score and "is it real?" Noul before a finding blocks anything |
| **Test gaming** | Tests that always pass | Mutation testing as a hard check; `tests_relevant` judge |
| **Drift** | Lots of activity, wrong direction | Supervisor drift Score; pause and escalate |
| **Runaway cost** | Retries on an expensive model | Scheduler budgets; cascade starts cheap; max rounds |
| **Merge conflicts** | Parallel raw branches touch the same code | Merge queue with rebase; small artifacts; one-sentence-per-line for documents |
| **Jev outage or change** | API down; `jev-latest` changes behaviour | `LLMDecider` fallback; pinned model versions; decision log flags version changes |
| **Miscalibrated gate** | A gate passes too much | Shadow mode first; outcome-based calibration; per-gate thresholds |
| **Prompt injection between teams** | An artifact contains "ignore previous instructions" | Content is data in typed fields; no team executes instructions found in artifacts |

---

## 19. Build plan

Each phase ends with something that runs and is tested with fakes before any real model is used.

| Phase | Builds | Exit criterion |
| --- | --- | --- |
| **0. Skeleton** | Event log (SQLite), views, claims, one level pair (raw → experimental), git worktrees, merge queue, one fake team | An artifact created by a fake executor is promoted through the merge queue; replay reproduces all views |
| **1. Real producer** | `Worker` with structured outputs; executor team; hard checks (build, tests) | A small real task goes from raw to experimental with no human involvement |
| **2. Jev in shadow** | `RawToExperimental` judge, relevance filters, decision log with outcomes | 50+ logged decisions with outcomes; first calibration report |
| **3. More teams and levels** | Unit tester, integration tester, red team, decomposition with join checks; candidate level; supervisor | A multi-artifact goal reaches candidate; drift and stall escalations are triggered in tests |
| **4. Jev active** | Thresholds from the calibration report; cascade routing | Gate error rates measured against outcomes and within agreed bounds |
| **5. Second domain** | Document configuration and teams | The same engine produces a multi-section document through all levels |
| **6. Multi-project** | Shared scheduler with budgets and fairness | Two projects run concurrently within their budgets |

---

## 20. Open questions for feedback

1. **Name and home.** Should this become the next iteration of `~/Projects/swarm`, or a new project?
2. **Language.** Python with Pydantic, as in the Jev tutorial, or Rust like forge-rs? The type story is good in both. Python is faster to iterate on; Rust gives stronger guarantees for the engine.
3. **Level granularity.** Are four levels right for software? Is `candidate` needed, or can security gate `experimental → production` directly?
4. **Human role.** Where must a human approve? Only production, or also escalations above some severity?
5. **Team autonomy.** May teams create artifacts freely (e.g. the red team opening a fix task), or only findings that the executor acts on?
6. **Unit tests: team or executor?** Separate teams keep producer and checker independent, but double the LLM work at the raw level.
7. **Decomposition.** Is a dedicated decomposition team right, or should "too large" simply be a finding that the executor addresses by splitting?
8. **Event log storage.** SQLite per project, or one database with a project column?
9. **Jev dependency.** How much should depend on Jev specifically? The `Decider` interface keeps it swappable, but thresholds and calibration are per model.
10. **Evaluation.** What would make you call phase 2 a success: agreement with forge's old critic, or outcome-based accuracy?

---

## 21. Glossary

| Term | Meaning |
| --- | --- |
| **Artifact** | A unit of work: a task plus the branch holding its state |
| **Blackboard** | A project's shared state: an event log plus views |
| **Claim** | A team's lease on an artifact |
| **Decision point** | A place where Jev (or a fallback) makes a judgement |
| **Finding** | Typed feedback attached to an artifact; `kind` from the judge, `detail` from one of four sources (§11.6) |
| **Gate** | The rule for moving an artifact up a level: hard checks, then a Jev judge, then a policy |
| **Join check** | The gate that verifies children together accomplish their parent |
| **Level** | A maturity stage, backed by a git branch |
| **Merge queue** | Serializes promotions into a level so the level branch stays green |
| **North star** | The project's goal, against which drift is measured |
| **Shadow mode** | Decisions are logged but don't act |
| **Team** | An independent loop that watches one level and does one kind of work |
