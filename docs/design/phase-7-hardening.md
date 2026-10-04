# Phase 7: Hardening before live runs

Status: **in progress** (from the independent audit in [CODEX_AUDIT.md](../../CODEX_AUDIT.md), 2026-10-03).

The remaining live runs for Phases 5–6 and the out-of-sample test for Phase 4 come after this phase,
because several of the audit's findings would make those runs unsafe or their results untrustworthy:

- F01: model-written code can reach the host.
- F05: analyzers are shown the wrong diff whenever branches overlap, which is the normal case in a swarm.
- F13: an infrastructure outage gets counted as ground truth.

## How the findings were assessed

Three findings were checked against the code and confirmed:

- **F05:** `Worktree.diff_between` runs a two-dot `git diff base head` on `merge^1`/`merge^2`.
- **F04:** `Released` carries only the artifact and the team.
- **F06:** `Swarm._run` catches only `RuntimeError`.

The rest are accepted on the strength of the audit's evidence. Its probes were concrete and reproducible.

## Decisions on the audit's open questions

- **Q1, owners per project:** one owner process per project root, enforced by an OS file lock on the
  root. This is simpler and enough for Phase 6, which runs several projects, not several processes on one
  project. Multi-owner fencing goes to the backlog.
- **Q5, how strict the budget is:** it stays a soft limit checked before each team action. Gates and
  the LLM fallback may spend after it is exhausted. Usage the provider doesn't report is recorded as
  *unknown*, not zero. The reference says all of this.
- **Q6, idle versus waiting:** `run_until_idle` reports *why* it stopped (done, waiting on a claim,
  paused, over budget, escalated), so the swarm and the CLI can tell finished from parked.
- **Q9, what audit certifies:** `GateAudit` audits the judge's rulings, not the actual promotions. Its
  report says so in its heading.
- **Q2 and Q3** (what a sign-off at an upper level certifies; what proves a finding fixed) are deliberate
  choices from Phase 3. Each gets a paragraph in the reference. A structured, recheckable reproducer goes
  to the backlog.

## Steps

1. **Sandbox and filesystem trust boundary (F01, F02, F14 partly).**
   - Checks run on an exported copy of the tree with no `.git`, so nothing the sandbox writes reaches
     host Git.
   - Host Git runs with hooks disabled (`core.hooksPath=/dev/null`), with explicit `--git-dir` and
     `--work-tree`, and in a scrubbed environment.
   - One `ConfinedFiles` capability does every host-side read and write for all toolboxes. It resolves
     paths, refuses symlinks that escape the root, bounds reads before allocating, and excludes `.git`.
   - Output captured from sandboxes is bounded.
2. **Diff semantics (F05, F09).** Typed references separate an artifact's integrated change (`merge^1`
   to `merge`) from an umbrella's joined snapshot. Recursion follows umbrella identity first. A test
   covers branches whose lifetimes overlap.
3. **Ownership (F04).**
   - A lock on the project root.
   - Claims get an id; a release names the claim id, so a late release can't remove a newer claim.
   - Context is re-read after claiming.
   - A claim enters cleanup as soon as it is acquired.
4. **Recoverable promotion (F03).**
   - A candidate is checked detached first, then published with compare-and-swap (`update-ref` with the
     old value).
   - A `PromotionIntended` event is recorded before the ref moves and completed by `Promoted`.
   - On startup, open intents are reconciled from where the refs actually are.
   - A failure-injection test reopens the project and checks that the refs and the board agree.
5. **Lifecycle predicates (F07, F16).**
   - One `Lifecycle` class answers the eligibility questions: may work, may review, may join, may route,
     may promote. Deliberate exceptions (human gates accept; dropped work rides the train) are named in
     it.
   - A shadow split ruling never blocks.
6. **Swarm shutdown and spend (F06, F17).**
   - Each project's errors are captured; every project is awaited and its threads drained before
     anything closes.
   - Spend from the calls that succeeded is metered even when a later call fails.
   - `status` shows spend and project-wide escalations.
7. **Evidence quality (F10, F11, F12, F13).**
   - Typed execution status separates a test failure from an error running it. An unavailable result is
     *unknown*: never a finding, never an outcome.
   - Calibration and audit cohorts include the concrete model and judge version, and keep fallback
     decisions apart.
   - Tuned thresholds are emitted losslessly and round-tripped through validation. They are marked
     ready only when every field is covered and ready.
   - Evaluation picks out the gate decisions explicitly and rejects shapes it doesn't support.
8. **Startup (F15).** `status` reads only the board. `Project.open` validates before any side effects,
   runs under an `ExitStack`, and checks the root's identity and ladder against the config.

Each step adds the failure-mode tests the audit lists (§4).

## After this phase

These come next, in this order:

- Rerun the Phase 4 audit on cohort-separated data, plus a held-out task set.
- The Phase 5 handbook live run.
- The Phase 6 swarm live run.
- Documentation.

## Deferred to the backlog

- F08: deterministic conflict refresh before retry.
- The rest of F14: process-group cleanup and timeouts on Git.
- Fencing for several owners of one project.
- Event schema versioning.
- Indexed board queries.
- CI.

## Decisions made during implementation

**Step 1 (trust boundary).**
- **`RunCopy`:** both sandboxes run commands on a throwaway copy of the worktree, without `.git`
  and `__pycache__`, with symlinks copied as links. The copy sits next to the worktree, so Colima can
  mount it. Whatever a command writes is discarded, which is what checks, hidden tests and mutation
  runs need anyway. Local and Docker now behave the same. Each run costs one copy; the projects are
  small.
- **Host Git** runs with `core.hooksPath=/dev/null`, `core.fsmonitor=false` and a minimal environment
  (PATH, HOME, LANG and the identity), so provider keys no longer reach git.
- **Worktrees** run git with `--git-dir/--work-tree` recorded when the worktree is created, so a
  replaced `.git` file is never followed. This is defence in depth: with `RunCopy`, nothing untrusted
  writes the worktree any more.
- **`workspace/files.py:ConfinedFiles`** backs `Worktree.read/write/delete`, so every host-side access
  is confined: model tools and framework scratch writes (`_analysis`, `_hidden`, mutations) alike. It
  refuses `.git` anywhere in a path and links that lead outside, and bounds reads. The executor's
  separate `_safe` check and the reviewers' weaker string checks are gone.
- **`Process`** sends output to a temporary file and keeps its first and last 64 KB; a timeout kills
  the whole process group (or the container). Docker's control commands now have timeouts.
- **Tests:**
  - the audit's probes: a forged `.git` pointer with a hook, a hook in the repository, symlinks through
    both reviewer toolboxes;
  - a command's writes are discarded;
  - bounded output;
  - a timeout kills child processes.

**Step 2 (diff semantics).**
- **An artifact's change is now `merge^1..merge`**, what its merge brought into the level, conflict
  resolution included. It was `merge^1` against `merge^2`, which showed work that landed earlier as
  deleted. Analyzers, relevance, context stats, drift and join all use `Repository.merged_change`, so
  the fix is in one place.
  - Test: two branches fork before either merges.
- **Umbrellas no longer get a `merge_commit`.** The blackboard records one only for artifacts that
  merge; an umbrella's `Promoted.commit` is a level snapshot. This also fixed two consequences of the
  same overloading:
  - the release train's commit→artifact map could map a child's merge commit to its umbrella;
  - analyzers could "review" an umbrella against a level-tip diff.
- **`_children_diff`** checks for an umbrella before looking for a merge. A separate typed "joined
  snapshot" field wasn't needed: nothing reads it.
- The phase-3 design doc keeps its original wording as history; the tutorial is updated.

**Step 3 (ownership).**
- **`workspace/lock.py:RootLock`:** `Project.open` takes an exclusive, non-blocking `flock` on
  `<root>/.agentswarm.lock`, which records the holder's pid, and releases it on close. The OS drops it
  if the process dies. A second owner gets `ProjectLocked`. Commands that only read or append to the
  board don't take it, so `status` (now board-only, which also covers F15's status part),
  `escalations`, `resolve` and `resume` still work beside a running project.
- **Claims have ids** (`c-` + 12 hex). `Released` names the claim, and the fold removes a claim only
  if the id matches. Older logs, with no ids, keep the old team-based rule, so replays don't change.
- **Fenced publication:** `ClaimRegistry.publish(claim, version, events)` appends with an expected head
  only if no other claim is live and the artifact's version is still the one seen at claim time. A
  lease that merely expired, with nobody else having touched the artifact, still publishes.
  - The runner re-reads the version right after claiming and gives the claim back if it moved.
  - A discarded result is logged. Its git commit may stay on the artifact branch unannounced, which
    is harmless: only announced commits are judged.
- **No heartbeats or renewal:** with one owner per root, nothing else can act during a long run.
- **The promoter adds a claim to its cleanup list before judging**, so an exception still releases it.
- **`Swarm.open`** checks for distinct names and roots before opening anything, and closes the projects
  it already opened if a later one fails.

**Step 4 (recoverable promotion).**
- **`gating/publication.py:LevelPublisher`** is now the only code that moves a level branch. It runs
  three steps:
  1. append `PromotionStarted(branch, previous, target, promotions)`;
  2. compare-and-swap with `update-ref` (`Repository.fast_forward`);
  3. append the promotions with `PromotionSettled(moved=True)` in one transaction.
- **A failed swap** is settled as `moved=False`.
- **`recover()`** runs at the start of every project step, before the promoters. An open move whose
  branch reached its target is recorded as promoted. One whose branch didn't move is settled as not
  having happened: the commit is still unjudged, so the gate judges it again.
- **The intent events are project-wide** (no subject), one per branch move, which matches how a train
  moves several artifacts at once. Artifact histories and their tests are unchanged.
- **The merge queue merges in a detached worktree** at the level tip and moves the branch only after
  the post-merge checks pass. A check exception or a crash no longer leaves an unchecked merge on the
  level. Each result is recorded as it happens, not after the whole batch, so a failure later in the
  batch can't strand earlier promotions.
- **The release train** publishes its prefix through the same protocol. A hard-check finding on a later
  commit goes in the same append as the promotions.
- **Joins move no branch** and are unchanged.
- **Tests:** crash after the move, crash before the move, the queue leaving the level alone on failed
  checks, and the normal path.

**Step 5 (lifecycle).**
- **`BoardViews.active(id)`** is the one rule: an artifact is active unless an escalation about it is
  open (it waits for a person) or it was dropped. This is a single view method, not the separate
  `Lifecycle` class planned: there are only two states to honour, and every caller already holds the
  views.
- **Where it is used:**
  - Every team runner skips inactive artifacts, without marking them handled, so a retry picks them up
    again. Before, this held only until a restart, through the in-memory handled set.
  - The router creates no fixes for inactive artifacts.
  - Joins skip dropped umbrellas. Parked umbrellas were already skipped.
- **The release train's exceptions are explicit:**
  - a dropped artifact's merged code rides along, as before;
  - an open escalation holds the train, unless it is the human gate's own request for approval.
    `Escalated` gained `approval_for: Level`, so the train recognises its own request without parsing
    the reason text.
- **F16:** a split check in shadow mode never blocks; the split goes ahead when the size check is
  active.
- **Tests:** runner, router, train and decomposer each get one case.

**Step 6 (swarm shutdown, spend).**
- **A failing project is reported, never raised.** `Swarm._run` catches any exception: a project
  still busy at the round limit is `StillBusy`, a `RuntimeError` subclass (no longer every
  `RuntimeError`); anything else is `failed`, logged with its traceback. `gather` therefore always
  waits for every project.
- **`agentswarm swarm` closes the projects after `asyncio.run` returns.** `asyncio.run` shuts down the
  default executor, which waits for every `to_thread` worker, so nothing closes under a running
  thread. The command exits 1 if any project failed.
- **`Tokens` is now a LangChain callback** that counts each model call as it completes, for agent runs
  and the final-answer call alike. The meter runs in a `finally`, so a run that fails after some
  successful calls still records them (F17). This replaces summing the agent's final messages.
- **Reports start with the spend so far** (and how many model runs were unpriced), the last five
  project-wide escalations (such as the budget) and PAUSED. `status`, `run` and `swarm` all show it.
- **Gate and fallback spend** after a budget is exhausted stays allowed, by design (see Q5).
- **Tests:** a failing project beside a healthy one, partial-failure metering, and the report header.

**Step 7a (failure versus error, F13).**
- **`CheckResult` carries `CheckStatus`** (passed, failed or error) instead of a bool. A check config
  can list `failure_exit_codes`, the exits meaning "ran and found a problem". Any other non-zero exit,
  a timeout or a command that can't start is an error. Without the list, every non-zero exit is a
  failure, as before.
  - The software preset and the greeter evaluation set `[1]` for pytest; pytest's 2–5 are collection
    errors, interruptions and usage errors.
- **Gates still fail closed**: an error blocks the promotion like a failure.
- **What an error no longer counts as:**
  - analyzer evidence: the tool tells the model "ERROR … this is not evidence", and the claim is
    dropped;
  - a hidden-test outcome: no `complete` outcome is recorded, and the report shows `error`;
  - a mutation result: any mutant run that errors gives no score at all, rather than a wrong one. A
    timeout still kills the mutant, by the usual convention.
- Task suites have `test_failure_exit_codes`, default `(1,)`.
- **Housekeeping:** a line in step 6's test was over the length limit because I edited it after linting;
  it is fixed here.

**Step 7b (exact thresholds, F11).**
- **`TuningReport.thresholds_yaml`** writes every threshold with Python's round-trip `repr`, as
  selected. Two decimals could merge 0.904/0.901 into an invalid pass = fail, or admit answers the
  measured policy excluded.
- **A block is ready to paste only when** every question the judge asks has a tuned row, every row is
  ready, and each pair validates as `Threshold`. Otherwise it is commented out with the reasons, so
  `yaml.safe_load` finds nothing to paste by accident. The report knows each judge's questions from the
  catalog.
- **The report ends with a caveat:** "ready" is in-sample, so confirm on tasks the thresholds weren't
  tuned on. That held-out run is already planned after this phase.

**Step 7c (cohorts, F10).**
- **A cohort is one decision point, judge wording and concrete model.** Calibration and tuning group by
  cohort, and tuned thresholds come out per point and model.
  - LLM-fallback decisions were already left out of calibration.
- **`GateAudit.cohorts`** audits each wording and model separately, fallback decisions included as
  their own cohorts. `audit` and `evaluate --active` print one block per cohort, and a pooled audit
  exists only through `GateAudit.of` with no filters, labelled "pooled".
- **The audit report says it audits rulings, not promotions** (Q9).
- **`label` offers only decisions asked with the judge's current wording**, so a person's answer is
  always to the question that was judged. Persisting each decision's full question spec wasn't needed:
  a person can only be asked today's questions anyway.
- **Test drafts** now carry the judge's current version.
