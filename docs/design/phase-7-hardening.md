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
