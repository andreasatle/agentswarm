# Phase 2: Jev in shadow mode

Status: **in progress.** Decided: Q1 (port the client). Step 1 used the recommendations for Q4 and Q5;
Q2 and Q3 are still open. Proposal sections: §11 (gates), §12 (Jev), §15 (decision log).

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
| `OutcomeRecorder` | `decisions.py` | Fills outcomes from later events where it can (below), and accepts manual labels. |
| `CalibrationReport` | `calibration.py` | Per decision point and field: reliability bins, ECE, sample size, a warning when n is small. Port of your `09_calibration.py`, text output (PNG optional). |
| CLI | `cli.py` | `agentswarm decisions` (list), `agentswarm label` (record outcomes by hand), `agentswarm calibrate`. |

### Shadow vs active (§12.3)

`jev_mode: shadow` (the default) logs every ruling but the gate behaves exactly as today. `active` makes
the gate act on the ruling. Per project for now; per decision point later.

### Outcomes: the honest part

§15.2 gets outcomes "for free" from later events, but most of those signals need things that don't exist
yet (higher levels, rollbacks, tester teams). In Phase 2 the available signals are:

| Signal | Means |
| --- | --- |
| Judge said pass, merge-queue checks then failed | the pass was wrong on that point |
| Judge said reject (shadow) and the artifact was promoted anyway | not ground truth: shadow mode promotes regardless |
| A human labels the decision | ground truth |

So in Phase 2, **most outcomes will be human labels**: `agentswarm label` shows each unlabeled decision
(task, diff, Jev's probabilities) and asks yes/no per question. 50 decisions × 3 questions is about an
hour of labelling.

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
4. `DecisionLog` + `OutcomeRecorder` + `agentswarm decisions/label`.
5. `CalibrationReport` + `agentswarm calibrate`; generate 50+ decisions on the greeter example.

## Questions for review

1. **Copy or depend?** Port the Jev client into `agentswarm/jev/` as classes (recommended), rather than
   importing from `~/Projects/Jev`, which is a tutorial, not a package.
2. **Outcomes from human labels** in Phase 2, as above. Acceptable?
3. **Defer relevance filters** to Phase 3?
4. **Key handling:** `TYPESAFE_API_KEY` and nothing else from the environment; the CLI loads a `.env`
   in the working directory (as in the Jev tutorial). The Jev model version goes in the project config.
5. **Synchronous Jev calls** for now (gates are synchronous); async when the scheduler arrives (Phase 6).
