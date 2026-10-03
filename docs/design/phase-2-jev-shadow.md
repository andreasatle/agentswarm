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
| `Decider` (changed) | `deciders.py` | `decide(state, spec) -> Answered[M]` (answers + the model version that answered). Implementations: `JevDecider`, `FakeDecider` (scripted answers). |
| `RawToExperimental` | `judges.py` | The §11.3 judge spec: `in_scope`, `tests_relevant`, `complete` (Noul). Each field also says which `FindingKind` and message a failure produces (§11.6, source 1). |
| `JudgeCatalog` | `judges.py` | Spec classes by name, since the config refers to `judge: RawToExperimental`. |
| `Policy` | `policy.py` | Pure code: thresholds per field → `Ruling` (PROMOTE / REJECT with findings / ESCALATE). Noul: `p ≥ pass` passes, `p ≤ fail` fails, between is unsure. |
| `JudgeState` | `judges.py` | What Jev sees: north star, task, open findings, diff (truncated to a character budget). |
| `DecisionLog` | `decisions.py` | A `decisions` table (plus a `states` table keyed by hash) in the project's SQLite file. Records every decision; outcomes are filled in later. |
| `Outcome` | `decisions.py` | The ground truth for **one question** of one decision: the true answer, and its source (`hidden_tests`, `mutation`, `planted`, `merge_queue`, `human`). |
| `OutcomeRecorder` | `decisions.py` | Attaches outcomes to logged decisions; never overwrites an automatic outcome with a weaker one. |
| `EvalTask`, `TaskSuite` | `evaluation/suite.py` | Small tasks on a seed project, each with **hidden acceptance tests** the executor never sees, and optionally a **planted fault**. |
| `MutationCheck` | `evaluation/mutation.py` | Breaks the changed code in known ways and reruns the executor's tests in the sandbox. Tests that still pass don't exercise the behaviour. |
| `FaultyExecutor` | `evaluation/faults.py` | Wraps an executor: does the task, then makes a known out-of-scope edit (and variants: drops part of the task, writes a trivially passing test). The fault is the label. |
| `EvaluationRun` | `evaluation/run.py` | Runs every task in the suite through a fresh project (executor → gate with Jev in shadow), then computes and records the outcomes. |
| `CalibrationReport` | `calibration.py` | Per decision point and question: reliability bins, ECE, sample size, a warning when n is small, and **which outcome source** the numbers rest on. Port of your `09_calibration.py`, text output. |
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
| `in_scope` | **planted faults** | `FaultyExecutor` adds a known unrelated edit to some runs; clean runs are in scope. Known by construction. |
| any (pass) | **merge queue** | Jev said pass but the checks failed on the merged result ⇒ wrong on that point (rare; a bonus signal). |
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

## Questions for review

1. **Copy or depend?** Port the Jev client into `agentswarm/jev/` as classes (recommended), rather than
   importing from `~/Projects/Jev`, which is a tutorial, not a package.
2. ~~Outcomes from human labels~~ **Decided:** automatic outcome sources; human labels only for spot checks.
3. ~~Defer relevance filters~~ **Decided:** deferred to Phase 3; tracked in [`docs/BACKLOG.md`](../BACKLOG.md).
4. **Key handling:** `TYPESAFE_API_KEY` and nothing else from the environment; the CLI loads a `.env`
   in the working directory (as in the Jev tutorial). The Jev model version goes in the project config.
5. **Synchronous Jev calls** for now (gates are synchronous); async when the scheduler arrives (Phase 6).
