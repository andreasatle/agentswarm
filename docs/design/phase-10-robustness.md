# Phase 10: Robustness for high-level goals

Status: **planned**, after Phase 8's measurements and Phase 9. Derived from [RESEARCH.md](../RESEARCH.md);
principles are cited by ID (P1–P12).

## The goal

The user's priority: **robustness and getting to the correct answer, especially when goals are high level.**
Cost and how heavy the system looks on toy problems matter less. The research says where the leverage is:
checks the model can't influence (P1, P12), several attempts selected by those checks (P2, P3), small checked
steps with voting (P7), and catching bad specifications and splits early (P4, P11).

## Rules for this phase

- **Each step is a hypothesis.** It is built only if Phase 8's measurements, or a probe experiment run for it,
  show it helps; otherwise it goes to the backlog with the result. This is the scope rule applied to
  research findings: published evidence says what to try first, our measurements decide what stays.
- **Every comparison is at equal cost** against the baseline "one model, n attempts, checks select" (P8).
- **Not in this phase:** agents debating each other (P2: no better than voting at equal compute), mixing model
  vendors only for diversity (P10: their errors are correlated), and parallel sub-agents on work that shares
  decisions (P5).

## Steps

1. **Several attempts, selected by checks (P2, P3).** An executor can make `attempts: n` independent
   attempts at a task, each in its own worktree. A selection step keeps one, in this order of evidence:
   - the hard checks pass;
   - the most tests written by *other* teams pass (critics' failing tests, acceptance tests; CodeT-style
     agreement between candidates and tests);
   - Jev's readiness question, as the tie-breaker.

   The other attempts are recorded, not merged. Measured first in probes (`produce` with n samples, selected
   by hidden tests for the ceiling and by generated tests for the realistic case).
2. **Red-flag and resample (P7).** A reply that is invalid, cut off or abnormally long is discarded and the
   call repeated, up to a limit, before the team escalates. MAKER found this raises per-step reliability and
   lowers correlated errors. The signals already exist (`InvalidAnswer`, `CutOff`).
3. **Checks out of the producer's reach (P12).**
   - Acceptance tests are written by a separate test-writing team, from the task, before the executor
     starts; the executor sees them but can't change them.
   - A hard check fails when a change deletes or weakens existing tests (fewer tests, removed assertions,
     skips added).
   - Measured with planted "impossible" tasks (tests that contradict the task): how often does an executor
     pass by cheating, before and after.
4. **Ask before building (P11).** For a new top-level goal, a clarifier lists what is underspecified (inputs,
   success criteria, scope). If anything is, the questions go to the human as an escalation before work
   starts, and the answers become the artifact's constraints. Measured on deliberately vague tasks from the
   suites: success with and without the clarifier.
5. **Verified decompositions (P4, P7, P11).** The split is the riskiest step for a high-level goal:
   - several candidate splits are proposed and compared (vote, or Jev's split check on each);
   - splits above a size or depth go to the human for approval;
   - Jev's split check is calibrated against join outcomes (did the children add up to the parent?).
6. **Small steps with voting where the path is known (P7).** Where a task's steps are fixed (a known
   procedure, a migration, applying a change to many files), each step is one call, voted on until one
   answer leads by k. Measured on a long, decomposable task where a single agent fails.
7. **Judge hygiene (P9).** A judge never scores its own model's work: Jev's fallback model must differ from
   the producer's (checked when the configuration loads), and when answers are compared their order is
   randomized and their models hidden.
8. **Consistency as a release criterion (P6).** Evaluations repeat tasks k times and report pass^k; a setup is
   only called reliable on its pass^k, not its best run.

## Order and dependencies

Steps 1–3 come first: they rest on the strongest evidence and on checks we already have. Steps 4–5 address
high-level goals directly and need the escalation channel, which exists. Step 6 needs a task whose
decomposition is known; step 8 is measurement only and can start any time.

## Decisions made during implementation

(none yet)
