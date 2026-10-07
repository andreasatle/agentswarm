# Acceptance checks the producer can't write or change

Status: **in progress** (started 2026-10-06, decided with the user). Moves Phase 10's step 3 forward, ahead of routing per role, because routing learns from outcomes and an outcome is only as honest as the check behind it.

## The problem

Outside the evaluation suites there are no hidden tests: the gate runs the tests the executor wrote for its own work. A model that writes weak tests, or tests that confirm its own mistakes, passes the gate, and a router learning from gate outcomes would learn to prefer it. The research is direct: reliability comes from external verification (RESEARCH.md P1), selection needs a trustworthy verifier (P3), and producers game checks they can see or edit (P12).

## The principle (every domain)

Whoever does the work doesn't decide what counts as done. A separate team writes the acceptance checks from the task, before the producer starts; the producer sees them but can't change them; the gate runs them. Only the evidence differs by domain: tests for code, and later a checklist checked by quoting reviewers and Jev for documents.

## Design (software first)

1. **The acceptance team** (`use: acceptance_tester`) acts on each new leaf artifact at the lowest level, before the executor: not on umbrellas (their children get their own) and not on fix artifacts (the tests they must satisfy are already merged). It writes `tests/acceptance/<artifact>/test_acceptance.py` from the task and the current files, in one call with a typed answer, the way produce probes work.
2. **The tests must fail before the work.** The team runs them on the current code: a test that already passes tests nothing new. If every test passes, or they can't even run, the attempt is discarded and retried, then escalated.
3. **They are committed to the artifact's branch** and recorded in an `AcceptanceWritten` event (the commit, and a hash of each file). The executor waits for that event, like it waits for sizing when the project has a decomposer.
4. **The producer can't change them.** Before the executor commits, plain code restores the acceptance folder exactly as recorded, and notes it when the model had touched it. The gate's unit tests run them with everything else; once merged they stay as regression tests.
5. **Their quality is measured where the truth is known.** A new probe kind, `accept`, asks a model to write acceptance tests for an evaluation task, and scores them with plain code: they must pass on the reference solution (else they demand something the task didn't ask), fail on the seed, and catch planted bugs. That gives a model's quality as an acceptance writer before any project relies on it.

## Steps

1. The `accept` probe kind, and a probe run on the inventory suite to see which models write acceptance tests that are right.
2. The acceptance team, the `AcceptanceWritten` event, the executor waiting for it and protecting the folder.
3. An experiment: the executor alone against the executor with acceptance checks, on the inventory suite, scored by the hidden tests; also how often acceptance tests and hidden tests disagree.
4. Documents: a checklist per section, checked by quoting reviewers and Jev; when a document project needs it.

## Known limits

- A producer could still sidestep the tests through test configuration (a `conftest.py`, pytest settings). Protecting configuration files and a separate acceptance check that runs only that folder are follow-ups, due if a run shows it happening.
- Acceptance tests are written by a model and can be wrong; step 1 measures how often, and a gate failure caused by a wrong acceptance test is the cost of that.

## Decisions made during implementation

**Steps 1-2 (2026-10-06).**
- **One prompt, two uses.** The acceptance team (`teams/acceptance.py`) owns its instructions and typed request and answer; the `accept` probe uses exactly those, so measuring the probe measures what the framework runs.
- **A test that can't import yet counts as failing.** Before the work, an acceptance test usually fails at `from inventory import search`, which pytest reports as a collection error (exit 2). The team first checks that the file compiles, then counts exit codes 1 and 2 as failing (`CommandCheck.run_on` takes `failure_exit_codes` for this).
- **No usable tests is recorded, not a dead end.** After two tries that test nothing or don't compile, the team records `AcceptanceWritten` without files and with the reason, so the executor isn't blocked and the gap is visible.
- **Protection by restoring, not by refusing.** Before every commit, the executor puts `tests/acceptance/` back as it is in the last commit (`Worktree.restore`: changes undone, new files there removed) and records `AcceptanceRestored` when it had to. That protects a fix artifact from changing its parent's merged acceptance tests too. The executor's instructions say the folder is fixed.
- **Wiring:** `use: acceptance_tester` watches the lowest level; executors wait for its `AcceptanceWritten` (not on fix artifacts), as they wait for sizing in a planned project.

**Step 1 results (2026-10-06): accept probes on the 25 inventory tasks.** Claude Sonnet wrote correct acceptance tests for 22/25 tasks and caught 48/48 planted bugs ($0.99); Claude Haiku 19/25 and 46/48 ($0.43). Every failure was a *wrong requirement* (the tests fail on the reference: they demand something the task doesn't ask for), never tests that check nothing. That is the failure that matters in use, since a wrong acceptance test blocks correct work; Sonnet writes the acceptance tests in the step 3 experiment. Local models are probed when the GPU is free.
