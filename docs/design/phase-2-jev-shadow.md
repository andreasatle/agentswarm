# Phase 2: Jev in shadow mode

Status: **in progress.** Decided: Q1 (port the client), Q2 (automatic outcomes; manual labels only
where nothing else works), Q3 (relevance filters deferred to Phase 3, recorded in `docs/BACKLOG.md`).
Step 1 used the recommendations for Q4 and Q5. Proposal sections: §11 (gates), §12 (Jev), §15 (decision log).

**Exit criterion (§19):** 50+ logged gate decisions with outcomes, and a first calibration report.

## Where we start

- `Gate.evaluate` runs the hard checks, then calls `Decider.judge(point, ctx)`, which returns a *verdict*.
  The only decider is `FakeDecider`, which always promotes.
- Jev returns *probabilities*, not verdicts. In the proposal a verdict comes from **Jev's answers + a
  policy with thresholds** (§11.2). So the decider interface has to change.
- The context sent to a judge has only `git diff --stat`. Jev can't judge "do the tests exercise the
  behaviour" from file names, so the judge needs the actual diff (truncated to a budget, §8.3).

## Design

```
Gate.evaluate
  1. hard checks ──fail──► REJECT (no Jev call)                       (unchanged)
  2. JudgeState  = artifact context + real diff (truncated)            (new)
  3. Answers     = Decider.decide(state, RawToExperimental)            (new interface)
  4. Ruling      = Policy.rule(answers, thresholds)                    (new, pure code)
  5. DecisionLog.record(point, mode, state, answers, ruling)           (new)
  6. shadow mode → act as before (promote); active mode → act on the ruling
```

### New and changed classes

| Class | Module | Responsibility |
| --- | --- | --- |
| `JevClient` | `jev/client.py` | The only reader of `TYPESAFE_API_KEY`. HTTPS only, base URL fixed in code, model version from config (pinned, e.g. `jev-1.13.0`). Sends one request, parses the response. Ported from `~/Projects/Jev/jev_client.py` + `jev_api.py`. |
| `Ask`, `Option`, `NoulAnswer`, `ScoreAnswer`, `ChoiceAnswer` | `jev/spec.py` | Typed question specs, as in your `jev.py`. |
| `QuestionSet` | `jev/spec.py` | `QuestionSet.of(spec)` builds the questions object from a spec's `Annotated` fields (your `questions_of`, as a class). |
| `Decider` (changed) | `judging/deciders.py` | `decide(state, spec) -> Answered[M]` (answers + the model version that answered). Implementations: `JevDecider`, `FakeDecider` (scripted answers). |
| `RawToExperimental` | `judging/judges.py` | The §11.3 judge spec: `in_scope`, `tests_relevant`, `complete` (Noul). Each field also says which `FindingKind` and message a failure produces (§11.6, source 1). |
| `JudgeCatalog` | `judging/judges.py` | Spec classes by name, since the config refers to `judge: RawToExperimental`. |
| `Policy` | `judging/policy.py` | Pure code: thresholds per field → `Ruling` (PROMOTE / REJECT with findings / ESCALATE). Noul: `p ≥ pass` passes, `p ≤ fail` fails, between is unsure. |
| `JudgeState` | `judging/judges.py` | What Jev sees: north star, task, open findings, diff (truncated to a character budget). |
| `DecisionStore`, `SqliteDecisions` | `judging/decisions.py` | The decision log in `decisions.sqlite`: every decision with its full state and a state hash, and outcomes per question. (Planned as `DecisionLog` with a separate `states` table; see step 4.) |
| `Outcome` | `judging/decisions.py` | The ground truth for **one question** of one decision: the true answer, and its source (`hidden_tests`, `mutation`, `planted`, `human`). |
| `EvalTask`, `TaskSuite` | `evaluation/suite.py` | Small tasks on a seed project, each with **hidden acceptance tests** the executor never sees, and optionally a **planted fault**. |
| `MutationCheck` | `evaluation/mutation.py` | Breaks the changed code in known ways and reruns the executor's tests in the sandbox. Tests that still pass don't exercise the behaviour. |
| `Variant`, `ScriptedExecutor` | `evaluation/faults.py` | Scripted runs that write a task's reference solution, cleanly or with a planted fault (out-of-scope edits, trivial tests). The variant is the label. (Planned as `FaultyExecutor` wrapping the model executor; see step 6.) |
| `EvaluationRun` | `evaluation/run.py` | Runs every task in the suite through a fresh project (executor → gate with Jev in shadow), then computes and records the outcomes. |
| `CalibrationReport` | `judging/calibration.py` | Per decision point and question: reliability bins, ECE, sample size, a warning when n is small, and **which outcome source** the numbers rest on. Port of your `09_calibration.py`, text output. |
| CLI | `cli.py` | `agentswarm evaluate` (run the suite), `agentswarm calibrate`, `agentswarm label` (spot checks only). |

### Shadow vs active (§12.3)

`jev_mode: shadow` (the default) logs every ruling but the gate behaves exactly as today. `active` makes
the gate act on the ruling. Per project for now; per decision point later.

### Outcomes: automatic wherever possible

Decision: **no routine manual labelling.** Each question gets its ground truth from a source that needs
no human:

| Question | Outcome source | How |
| --- | --- | --- |
| `complete` | **hidden acceptance tests** | Each suite task carries tests the executor never sees. Run them in the sandbox on the gated commit: pass ⇒ complete. |
| `tests_relevant` | **mutation testing** | Apply known mutations to the code the change added; rerun the change's own tests. Most mutants caught ⇒ relevant. |
| `in_scope` | **planted faults** | Scripted runs write the reference solution, with or without an unrelated edit. Known by construction. |
| any | **human** (`agentswarm label`) | Only a spot check of about 20 decisions, to confirm the automatic sources agree with human judgement. |

Two consequences:

- Outcomes are **per question**, not per decision: one decision can have `complete` from hidden tests and
  `in_scope` from a planted fault.
- The calibration is only as representative as the suite. Synthetic tasks and planted faults are not the
  mix of mistakes a real project makes. Real-project outcomes (§15.2) take over as later phases add
  higher levels, testers and rollbacks.

## Not in Phase 2

- **Relevance filters** (§19 lists them): the only team, the executor, decides relevance from history,
  deterministically. A Jev relevance filter only makes sense once tester/reviewer teams exist (Phase 3).
- **`LLMDecider` fallback** (§12.4): if Jev is unreachable in shadow mode, the gate logs the failure and
  carries on. The fallback matters once Jev is active (Phase 4).
- **Score/Choice in policies:** the first judge uses Noul only; the policy rejects other answer types
  at load time rather than guessing thresholds.
- **Per-item checks and the LLM explainer** (§11.6, sources 3 and 4).

## Steps (one commit each, reviewed before the next)

1. `JevClient` + typed specs, tested offline with `httpx.MockTransport`; one opt-in live test.
2. `RawToExperimental`, `JudgeCatalog`, `Policy`: pure code, no I/O.
3. New `Decider` interface; `Gate` builds `JudgeState` with the real diff; shadow/active.
4. `DecisionLog`, `Outcome`, `OutcomeRecorder` (storage only; outcomes added by hand in tests).
5. `TaskSuite` with hidden tests + 10–20 tasks; `EvaluationRun` with hidden-test outcomes.
6. `MutationCheck` and `FaultyExecutor`: the other two automatic outcome sources.
7. `CalibrationReport`, `agentswarm evaluate/calibrate/label`; run the suite to get 50+ decisions.

## Decisions made during implementation

*After step 5 the package was reorganised into subpackages (see the layout in `AGENTS.md`); module
paths in this document use the new layout.*

Recorded as they happen, for the end-of-phase tutorial.

**Step 1 (Jev client).** Ported as classes (`JevClient`, `QuestionSet`). Differences from the tutorial
code: errors raise `JevError` instead of `SystemExit`; the state is always a Pydantic model (no plain
text); `ChoiceQuestion` is not generic (same JSON; the option type is still checked on the answer); the
model version is a constructor argument instead of `JEV_MODEL`.

**Step 2 (judge and policy).** Questions are phrased so yes is good, and each carries `Fails(kind,
message)`. A failed `tests_relevant` becomes an `INCOMPLETE` finding (no dedicated kind yet). A clear
failure wins over an unsure question (reject, not escalate), because it gives the executor something
concrete to fix. `Verdict` moved to `policy.py`. Threshold YAML keys stay `pass`/`fail`
(`validation_alias`), and `fail < pass` is enforced.

**Step 3 (gate wiring).**
- `Decider.decide(state, spec) -> Answered[M]` replaces the verdict-returning decider. `JevDecider` maps
  `JevError` to `DeciderError`; `FakeDecider` plays scripted answers.
- `GateJudgement` = judge + policy + decider + decision store + mode. A `Gate` without one checks only.
- The `Decision` record and a `DecisionStore` interface (`record`, `all`) arrived here, not in step 4,
  because promotions link to decision ids; step 4 adds SQLite storage and outcomes behind it.
- `Promoted.gate_decision` and `MergeRequest.decision` are optional: a gate without a judge has none.
- The judge sees `JudgeState`: north star, task, open findings, and `git diff <to_level>...HEAD`
  truncated to 60,000 characters with a note. History is left out to keep the state small.
- Shadow mode: the decision is recorded and linked to the promotion, but the gate promotes as if it had
  no judge. If the decider is unreachable, shadow mode logs a warning and promotes; active mode
  escalates ("judge unavailable").
- Active-mode rejections are `MAJOR`; hard-check rejections stay `BLOCKER`.
- `requires_human` applies after everything else, in both modes.
- `ProjectServices` bundles the injectable collaborators (team and judge catalogs, workers, decider,
  decision store, clock, lease) so `Project.open(config, root, services)` stops growing. If a gate names
  a judge and no decider is given, Jev is built from the environment at open time, so a missing key
  fails before any work starts.
- The project config's `jev_mode` became a `jev:` section (`model`, `mode`, `timeout_s`).

**Step 4 (decision log).**
- `SqliteDecisions` keeps the log in `decisions.sqlite` next to the blackboard (a separate file: the
  log is not part of the blackboard, and §6 puts it in the shared layer later). Each decision is one row
  with indexed columns (id, point, artifact, mode, verdict, state hash) plus the full JSON. The full
  state (with its diff) is stored inline rather than in a separate table keyed by hash: each decision
  judges a different commit, so de-duplication would rarely save anything. The hash is still stored.
- Outcomes are never overwritten. Every outcome is kept; `Outcomes.strongest` picks per question by
  source strength (human > planted > hidden tests = mutation), then the latest. This replaces the
  planned `OutcomeRecorder`, which is not needed.
- An outcome must name a stored decision and one of its judge's questions.
- Dropped the "merge queue failed after a pass" signal: failing checks on the merged result say the
  change conflicts with other work, not which question Jev got wrong, so it is not ground truth for any
  single question.
- `Project` owns and closes what it opened (log, decision log, Jev client).

**Step 5 (task suite and hidden tests).**
- The suite is a YAML file (`examples/greeter/evaluation/suite.yaml`) naming a project config and 14
  tasks, each with precise task text and a hidden test file. `TaskSuite.project_config()` puts the
  suite's judge on the gate out of the lowest level and forces Jev into shadow mode, so an evaluation
  never changes what gets promoted.
- Every task runs in a fresh project under `workdir/<task id>`; a used workdir is refused, never reused.
  All tasks share one decision log, so one calibration report covers the run. A task's decisions are
  the ones recorded while it ran (tasks run one at a time).
- Hidden tests run in a throwaway detached checkout of the judged commit, under `_hidden/`, in the
  project's sandbox. They never enter the repository (tested against git history).
- **The hidden tests are themselves tested:** each task has a reference solution in `reference/`; a test
  checks that the reference passes and the untouched seed fails. Without this, a wrong hidden test
  would silently corrupt the ground truth.
- Five tasks are deliberately multi-part or have edge cases (`farewell-and-shout`, `robust-hello`,
  `languages`, `hello-many`, `empty-name-error`) so that `complete` is not trivially true.

**Step 6 (mutation testing and planted faults).**
- **Changed from the design: faults are planted on reference solutions, not on the model's output.**
  "Clean model runs are in scope" would be an assumption, not ground truth: the model can wander on its
  own. So each task also runs through `ScriptedExecutor` in three variants: `reference` (in scope,
  relevant tests), `out-of-scope` (adds `scratch/notes.py` and an unrelated function), and
  `trivial-tests` (a test that only asserts `True`). Their truths are known by construction. The model
  executor's run (`executor` variant) gets `complete` from hidden tests and `tests_relevant` from
  mutation testing; its `in_scope` stays unknown. 14 tasks × 4 variants = 56 decisions, with true and
  false examples for every question.
- In scripted runs the hidden tests double as the change's unit tests (they test the behaviour by
  definition), so they do appear in those repositories. Model-executor runs never see them.
- **Bug found and fixed: decisions now record their `base`** (the merge base with the target level at
  judging time). Outcomes are computed after the run, when the gate may already have merged the change
  into the level; diffing against the level then shows nothing. Mutation testing now diffs against the
  recorded base.
- Mutation testing (`evaluation/mutation.py`) is Python-only and small on purpose: comparison and
  arithmetic swaps, flipped booleans, changed strings and integers, `return x` → `return None`, on
  changed lines of changed non-test files, at most 12 mutants per file. Only the change's own test files
  run; they must pass unmutated first, or there is no answer. `tests_relevant` is true when the change
  added tests and they kill at least half the mutants. A change with no tests is not relevant.
- Known disagreement: in the `out-of-scope` variant, mutation testing also mutates the planted unrelated
  function, which no test covers, so its score can drop. The planted truth wins (planted > mutation).
- The suite's `hidden_tests_command` became `test_command`, since mutation testing uses it too.

**Step 7 (calibration, CLI).**
- `CalibrationReport` (`judging/calibration.py`) ports `09_calibration.py` (ten bins, ECE, small-n
  warning) and adds, per decision point and question: the Brier score, coverage and accuracy at the
  policy thresholds (what Phase 4 tunes), which outcome sources the truths came from, and a warning when
  every truth is the same. Each question uses its strongest outcome; questions without one are counted,
  not guessed.
- `SpotCheck` (`judging/labelling.py`) samples decisions that already have automatic outcomes and asks a
  person each question **before** showing Jev's probability or the automatic truth, to avoid anchoring;
  it then reports agreement with the automatic sources. The sample is reproducible (seeded).
- CLI: `agentswarm evaluate SUITE --workdir DIR [--variant ...]` (prints one line per run, then the
  calibration report; the log is `DIR/decisions.sqlite`), `agentswarm calibrate DECISIONS`, and
  `agentswarm label DECISIONS [--limit N]`. `main` loads `.env` from the working directory without
  overriding variables already set. Output goes through injected `show`/`ask`, so commands are tested.
- Test helpers used by more than one test module live in `tests/support/` (no test imports another).

**First real run (scripted variants, 42 decisions) and what it changed.**
- Jev separated the variants clearly: mean p(tests_relevant) 0.03 for trivial tests vs 0.91 otherwise;
  mean p(in_scope) 0.13 for out-of-scope vs 0.75 for reference.
- It also exposed a flaw in our ground truth: Jev rated the `trivial-tests` variant incomplete (mean
  p(complete) 0.22), correctly, since every task says "Add tests". Hidden tests only check the code
  part of a task. Fix: `trivial-tests` is now incomplete by construction (a planted truth, which
  outranks hidden tests). For model-executor runs, the "add tests" part is covered by `tests_relevant`,
  not by `complete`; that limitation stands.
- Mutation testing had no operator for `if` conditions, so `empty-name-error`'s reference scored 0.00.
  Added: negate an `if` condition (or remove its `not`).
- Jev also rated trivial tests lower on `in_scope` (0.25) than the reference (0.75): the questions are
  not fully independent. Sharpening the wording is Phase 4 work (`docs/BACKLOG.md`).

## Questions for review

1. **Copy or depend?** Port the Jev client into `agentswarm/jev/` as classes (recommended), rather than
   importing from `~/Projects/Jev`, which is a tutorial, not a package.
2. ~~Outcomes from human labels~~ **Decided:** automatic outcome sources; human labels only for spot checks.
3. ~~Defer relevance filters~~ **Decided:** deferred to Phase 3; tracked in [`docs/BACKLOG.md`](../BACKLOG.md).
4. **Key handling:** `TYPESAFE_API_KEY` and nothing else from the environment; the CLI loads a `.env`
   in the working directory (as in the Jev tutorial). The Jev model version goes in the project config.
5. **Synchronous Jev calls** for now (gates are synchronous); async when the scheduler arrives (Phase 6).
