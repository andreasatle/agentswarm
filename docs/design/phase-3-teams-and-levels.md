# Phase 3: More teams and levels

Status: **in progress.** All five questions decided as recommended (the user chose to let the
implementation proceed on the recommendations). Proposal sections: §9 (git), §10 (teams), §11.4 (join checks),
§12.1 (relevance filters), §14.1 (supervisor).

**Exit criterion (§19):** a multi-artifact goal reaches `candidate`; drift and stall escalations are
triggered in tests. Added here: relevance decisions are logged with automatic outcomes, and an
escalated artifact can be resolved by a human.

Phase 3 is the largest phase, so it comes in two parts with their own checkpoints:

- **3a, teams and the candidate level:** clean-ups, findings that can be resolved, analyzer teams with
  Jev relevance filters, fix routing, and promotion above `experimental`.
- **3b, decomposition and supervision:** splitting large goals, join checks, the supervisor, and the
  human escalation queue.

## Where we start

- One team (the executor) at the lowest level; one gate (`raw → experimental`) with a Jev judge in shadow
  mode; 56 calibrated decisions.
- Levels are per project already (no level name is hard-coded), but two leftovers are software-flavoured:
  the judge is named `RawToExperimental`, and every project spells out its whole ladder.
- Findings have no identity, so nothing can say "this finding is fixed".
- Promotion only exists into the second level; above it, level branches mix many artifacts, and
  "promote artifact X to candidate" is undefined (backlog).
- Escalated artifacts stay parked forever (backlog).

## 3a. Teams and the candidate level

### Clean-ups from the level discussion

- **Judge named for what it asks.** `RawToExperimental` becomes `ChangeReadiness` ("is this change in
  scope, tested, and complete?"). It can sit at any project's first gate, including a document
  project's `draft → reviewed`. `JudgeCatalog` keeps `RawToExperimental` as an alias, so the Phase 2
  decision logs still load.
- **Domain presets.** `domain: software` supplies default `levels`, `checks`, `teams` and `gates` from a
  preset file in the package (`core/presets/software.yaml`); the project's own keys replace the
  preset's, key by key (a project that lists `gates` replaces all preset gates, no deep merging, so
  the result is easy to predict). `agentswarm config CONFIG` prints the effective configuration.
  The document preset arrives in Phase 5.

### Findings that can be resolved

- `Finding` gets an `id` (`f-0001`, sequential per project, like artifact ids).
- New event `FindingResolved(finding, resolved_by)`. The views close a finding when it is resolved.
  Promotions and rejections at the lowest level keep their Phase 2 behaviour (they replace or close the
  gate's own findings).

### Promotion above the base level: a release train

The decision that unblocks the candidate level. Levels above the base move as **prefixes** of the level
below:

```
experimental:  ──M1──M2──M3──M4──M5──►   (first-parent line: one merge commit per artifact, from the queue)
candidate:     ──M1──M2──M3              (candidate is always some commit on experimental's line)
                          └─ "promote to M5" = fast-forward candidate to M5, moving a-4 and a-5 together
```

- The merge queue already makes `experimental` a linear sequence of merge commits, one per artifact.
  Promoting to `candidate` means fast-forwarding `candidate` to one of those commits. Every artifact
  between the old and new tip moves up together, with one decision.
- **Why not per artifact?** An artifact's branch forked from `experimental` with all the work before it;
  merging it into `candidate` alone would silently drag that work along, or conflict. A prefix never
  conflicts, and the promoted commit is exactly the one that was tested.
- **The gate above the base:** hard checks run on the target commit itself (integration tests); then a
  deterministic policy: every artifact in the batch has the required **sign-offs** (below) and no open
  `MAJOR`/`BLOCKER` findings. The promoter picks the longest ready prefix. One artifact that is not ready
  holds back the ones after it (head-of-line blocking), which is the usual trade-off of release trains;
  the supervisor escalates it if it stays stuck.
- A Jev judge at this gate (§11.3 `CandidateToProduction`: sensitive-change routing, drift as a Score)
  needs Score policies, so it stays in Phase 4. The gate config already allows adding one later.

### Analyzer teams and sign-offs

- Teams above the lowest level never commit: only the merge queue writes level branches. They are
  **analyzers**: they inspect an artifact's change on the level branch and produce findings.
- New event `SignedOff(artifact, team)`: an analyzer has examined the artifact's current version (with
  or without findings). A gate lists the sign-offs it requires: `requires: [integration_tester,
  red_team]`.
- **Integration tester** (watches `experimental`): a model writes integration tests for the change in a
  throwaway checkout of the level commit and runs them in the sandbox. Failures become findings with the
  test code in their detail. (Keeping passing tests in the repository would need a commit; noted in the
  backlog, not done here.)
- **Red team** (watches `experimental`): a model tries to break the change (edge cases, misuse, bad
  input) the same way: write a failing test, run it, report what failed.
- Both use the existing `Worker` and toolbox, with a read-only variant of the toolbox plus a "run this
  test file" tool.

### Jev relevance filters (the backlog item)

- Each analyzer asks Jev one Noul question before claiming: integration tester: "Does this change alter
  behaviour that other parts of the code depend on?"; red team: "Does this change handle input, state or
  resources that could be misused or fail at the edges?".
- Logged as decisions at points like `relevance:red_team`, in the same decision log, shadow or active per
  project `jev.mode`. Shadow: the team acts anyway. Active: a clear "no" signs off without work; unsure
  acts.
- **Outcome, automatically (§15.2):** if the team acted, the truth is whether it produced a finding. In
  shadow mode every judgement gets an outcome, which is exactly why shadow mode is valuable here.

### Fix routing (proposal question 5)

- Teams only emit findings; they never create artifacts. A deterministic **`FindingRouter`** turns each
  open blocking finding on an artifact above the lowest level into a fix artifact at the lowest level
  (task: "Fix in <artifact>: <finding>", `parent` = the artifact). When the fix artifact reaches the
  finding's level, the router appends `FindingResolved`.
- At the lowest level nothing changes: the executor addresses findings on its own artifact directly.

### The unit tester (proposal question 6)

Recommendation: **no separate unit tester team in Phase 3.** The executor already writes tests, and the
gate's `tests_relevant` question is the best-calibrated one we have (ECE 0.07, right whenever it
decided). A second team at the lowest level would double the model work and race the executor for the
same branch. Revisit if `tests_relevant` rejections pile up in practice (backlog).

## 3b. Decomposition and supervision

### Decomposition (proposal question 7)

- A **decomposition team** watches the lowest level. Before the executor claims a new artifact, Jev is
  asked "Can this task be done in one branch by one team, without further design decisions?" (§10.4).
  Ordering without coupling: the executor's relevance requires that the artifact has been sized (a
  `Sized` event, or the decomposition team's sign-off), so teams still depend on state, not on each
  other.
- If clearly too large: a model splits the task into child artifacts (structured output: a list of
  child tasks), checked by a second Noul ("Is each child clearly smaller than the parent?"). The parent
  becomes an **umbrella** that does no work itself. Depth limit (config, default 3); beyond it, escalate.
- **Join check (§11.4):** when every child of a parent has reached a level, a join gate evaluates the
  parent there: hard checks on the level tip, and a Jev Noul "Together, do the children accomplish the
  parent task?". Passing promotes the umbrella to that level.

### Supervisor (§14.1)

A deterministic loop per project, run every step like the promoters:

- **Stalls:** an artifact with no new event for N steps, or rejected K times in a row, is escalated.
- **Round budget:** `budgets.max_rounds_per_artifact` (already in config) escalates instead of retrying.
- **Drift:** every N decisions, a Jev Noul about the recent tasks and diffs: "Does this recent work
  advance the north star?". A clear no appends `Paused`: no new artifacts are created until a human
  resumes. (The proposal's Score version waits for Score policies in Phase 4.)
- **Escalation queue:** `agentswarm escalations ROOT` lists open escalations with their context and the
  decisions that led there; `agentswarm resolve ROOT ARTIFACT --retry | --accept | --drop` appends
  `EscalationResolved`. Retry clears the escalation so teams pick the artifact up again; accept lets a
  human promote it past the gate; drop retires the artifact.

## Steps (one commit each)

**3a**
1. `ChangeReadiness` (with alias) and domain presets; `agentswarm config`.
2. Finding ids, `FindingResolved`, `SignedOff`; views and tests.
3. Analyzer base class, read-only toolbox, Jev relevance filter with decisions and team-result outcomes.
4. Integration tester and red team.
5. `FindingRouter`.
6. Release-train promotion above the base level; the greeter project gains `candidate`.

**3b**

7. Decomposition team, umbrella artifacts, join gate.
8. Supervisor: stalls, round budget, drift, pause.
9. Escalation queue and `agentswarm escalations` / `resolve`.
10. End to end: a multi-artifact goal reaches `candidate` (fakes in tests, then a live run).

## Not in Phase 3 (to `docs/BACKLOG.md`)

- A Jev judge at the gate above `experimental`, Score policies, drift as a Score: Phase 4.
- Keeping the integration tester's passing tests in the repository.
- Per-item checks and the LLM explainer (§11.6): still not needed by any judge.
- `in_scope` truth for model runs; a harder task suite: Phase 4 (calibration).

## Questions (decided as recommended)

1. **Release train above the base level** (prefix fast-forward, batch decisions, head-of-line
   blocking), rather than per-artifact promotion. Agree?
2. **Teams emit findings only;** a deterministic router creates fix artifacts (proposal question 5).
3. **No unit tester team in Phase 3;** the executor writes tests and the judge checks them (question 6).
4. **A dedicated decomposition team** gated by a Jev size question, with umbrella parents and join checks
   (question 7).
5. **Presets replace key by key** (no deep merge). Simple to predict, a little more to type when only one
   team changes. Agree?
