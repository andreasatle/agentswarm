# Project audit — October 19, 2026

AgentSwarm has a coherent architecture, substantial regression coverage, and unusually explicit evaluation practices. Its strongest idea is that models propose work while ordinary code owns coordination and acceptance. The most valuable next investment is strengthening the measurement and operational boundaries already present: the test oracle, spending admission, outcome attribution, and recovery. This review does not establish that historical results were invalid; it establishes specific ways future runs can produce misleading success or cost records.

**Scope and verification.** Reviewed repository revision `9466829`, including the proposal, overview, backlog, prior audit, research roadmap, software-development essay, and representative implementation and tests across configuration, board, workspace, gates, judging, teams, routing, budgets, evaluation, model adapters, CLI, and monitoring. This was a source and local behavior audit, not a line-by-line proof of every module or an independent rerun of the historical model experiments. No new paid experiments were requested. Application code was not changed.

| Check | Result |
| --- | --- |
| `uv run pytest -q` | 753 passed, 2 skipped; 188.64 seconds |
| `uv run ruff format --check .` | 423 files already formatted |
| `uv run ruff check .` | Passed |
| `uv run pyright` | 0 errors, 0 warnings |
| Temporary-repository probes | Five behavioral gaps reproduced, detailed below |

The full suite passes despite the reproduced gaps. That makes the missing tests particularly actionable: several concern interactions between components whose individual behavior is already well tested.

**Strengths worth preserving.**

1. **The architecture has clear authority boundaries.** Typed events, board-derived views, team interfaces, gates, and deterministic controllers make it possible to inspect why work happened. The layout largely matches the proposal. Dependency injection and fakes make these mechanisms testable without buying model calls. See `board/eventlog.py`, `board/blackboard.py`, `teams/runner.py`, and `project.py`.
2. **Publication is substantially safer than in the previous audit.** `gating/merge_queue.py` checks a detached candidate before moving the shared branch. `gating/publication.py` records an intent, performs a compare-and-swap branch update, and reconciles unfinished moves. This is an appropriate response to the fact that Git and SQLite cannot participate in one transaction. Preserve this pattern and apply it to the other cross-store effects below.
3. **Isolation is concrete.** `workspace/sandbox.py` uses disposable copies, excludes Git metadata, defaults to a container without networking, limits memory/CPU/processes, drops capabilities, and runs as a non-root identity where the invoking user is non-root. `workspace/files.py` centralizes path confinement; `workspace/git.py` disables hooks and filesystem monitoring and avoids normal user Git configuration. These are meaningful safeguards, although disk consumption remains incompletely bounded.
4. **Judge evaluation is thoughtfully designed.** Shadow mode, explicit uncertainty, independent outcome labeling, calibration, and separate model/wording cohorts are good mechanisms. `judging/audit.py` reports confidence bounds and unknown outcomes rather than silently counting unknowns as successes. The backlog explicitly acknowledges that the active gate still needs more evidence.
5. **The project measures product behavior.** Official-suite importers, hidden tests, mutation work, and document questions provide stronger evidence than an agent declaring itself done. The document evaluator even attempts to remove questions answerable from the reader model's memory. The roadmap records failures and waste as well as successes.
6. **The workflow has useful recovery paths.** Repair, model escalation, decomposition, human escalation, protected acceptance restoration, and conflict refresh address actual observed failure modes. These should remain explicit policies rather than becoming a large prompt with implicit responsibilities.
7. **The development baseline is strong.** Strict typing, broad test coverage, shared test harnesses, generated prompt documentation, and readable domain classes are valuable. The prior audit has led to real improvements: root ownership, confined file access, exact-merge validation, and publication recovery are implemented rather than merely acknowledged.

**Priority findings.** Severity describes impact under the stated trigger, not an assertion that every normal run encounters it.

| ID | Priority | Finding | Evidence |
| --- | --- | --- | --- |
| A01 | High | A broken ratchet invocation can report success and completion | Reproduced |
| A02 | High | Protected acceptance files do not protect the test execution machinery | Reproduced |
| A03 | High for unattended use | Budget checks do not consistently govern every paid call or reserve concurrent spend | Source-confirmed; overshoot also documented in `docs/STATUS.md` |
| A04 | Medium | Batched attempt events lose or misassign the router's per-attempt cost | Reproduced |
| A05 | Medium | Interrupted routing publication duplicates learning outcomes on restart | Reproduced |
| A06 | Medium | A pause raised by one action does not stop later candidates in that runner step | Reproduced |
| A07 | Medium | Blocking checks inside async team methods stall other projects | Source-confirmed |
| A08 | Medium | Subprocess output is memory-bounded but not disk-bounded; Git has no timeout | Source-confirmed; Git timeout already deferred |

**A01 — Reject incomplete or invalid ratchet measurements.** `gating/checks.py:208` rejects only a missing exit code before parsing outcomes. Exit codes for usage errors, internal errors, and no collected tests are accepted as measurements. `gating/ratchet.py:102` accepts an empty baseline and empty candidate as “nothing left.” In a temporary repository with actual tests, configuring the command as Python exiting with code 4 produced `status="passed"`, `done=true`, and `NOTHING LEFT: every test passes`.

This can turn a misconfigured or broken verifier into an apparent completed task. It does not imply that every candidate-only test crash passes: an already populated passing baseline catches many such cases. The dangerous case includes an invalid or empty baseline, where there is no reliable evidence to compare.

Use an explicit test-run result containing runner status, collection status, expected/collected test identities, and outcomes. Reject infrastructure exits and unexpectedly empty suites; define explicitly whether particular collection errors are intended task failures. Apply the same treatment to `evaluation/score.py`, which also parses output without validating the process result. Regression tests should cover exit codes 2–5, partial collection, missing summaries, and unexpectedly empty suites.

**A02 — Protect the oracle, not just its files.** `teams/executor.py:587` restores `tests/acceptance`, but a builder can alter the surrounding pytest environment. The temporary probe left the implementation and acceptance tests unchanged and added this root `conftest.py`:

```python
def pytest_collection_modifyitems(items):
    for item in items:
        item.obj = lambda: None
```

Before the change, one of three tests passed and the ratchet rejected the work. Restoration reported no changed acceptance paths. Afterward, the ratchet reported `3/3`, `2 newly passing`, and passed. This demonstrates a test-integrity gap without escaping the sandbox. It is not evidence that historical agents used this technique. Additional project checks might reject this particular file, but the central restoration mechanism does not.

Run authoritative acceptance checks with an evaluator-owned configuration and test harness outside the producer's editable tree. Control pytest configuration, plugin loading, discovery, helper imports, and startup hooks; validate test identity and result completeness. Protect any allowed harness files by manifest. For stronger resistance to deliberate test tampering, separate the harness from the candidate process and exchange inputs and outputs across that boundary. Merely switching from text summaries to JSON would not make a producer-controlled test process trustworthy. The same issue matters to hidden scoring because `Scoreboard` runs hidden tests alongside candidate repository contents.

**A03 — Make spending admission a shared boundary.** Current budget checks are distributed across runners and campaigns. `llm/workers.py:139` meters a whole worker run on exit, after potentially many model calls. Executor repair rounds at `teams/executor.py:601` do not check the budget between repairs. `project.py:387` runs unfinished specification/brief creation before the ordinary runners; those workers are not constructed with a budget admission check. Scoring can make additional paid calls. `control/limits.py` reads current totals but does not atomically reserve funds before concurrent projects proceed. Unpriced usage can be recorded as zero dollars.

The documented between-attempt overshoot is therefore only part of the issue. A run cap should specify whether it includes briefing, judging, retries, and scoring, and whether it persists across UTC midnight. The current core budget is daily, whereas CLI/help language also talks about a run's cap.

Introduce one injectable admission/metering service at the model-call boundary. Reserve estimated maximum cost atomically across projects, reconcile actual usage, and reject further calls when admission fails. Persist per-run authorization separately from daily backstops. For capped paid runs, require pricing or an explicit unknown-cost policy. Record usage as calls finish so a process crash loses less accounting. Exact provider charges cannot always be known before execution; state the maximum permitted overrun rather than implying a perfectly hard cap.

**A04 — Give attempts stable identities for cost attribution.** `SpendLedger.record()` appends spend immediately, while `ExecutorTeam.act()` accumulates `AttemptFailed`/`CommitPushed` events until `TeamRunner` publishes the returned batch. `control/outcomes.py:122` assumes spend falls between those attempt markers in board history.

A probe reproduced the actual ordering: `$0.50 small spend`, `$0.30 large spend`, then the routed/failed/committed/promoted event batch. The ledger recorded the small attempt at `$0.50`, but the large successful attempt at `None`. Repeated attempts on the same model can instead concentrate multiple attempts' spend on the first marker. Existing `test_each_attempt_is_recorded_with_its_own_cost` manually interleaves spend and failure events, so it does not exercise the runner's batched ordering.

Add an attempt ID to spend and outcome events and aggregate by that identity. Include an integration test through the real runner with two metered attempts in one action, both same-model and different-model variants. This should precede further claims about how well cost-aware routing selects models.

**A05 — Make routing outcomes idempotent across stores.** `control/outcomes.py:97` writes to the routing SQLite database before `step()` appends `OutcomeRecorded` to the project board. `teams/routing.py:89` always inserts and has no unique outcome identity. Simulating interruption after the ledger write but before the board append, then stepping the recorder again, increased the ledger from one outcome to two.

Use a durable key such as project-instance ID plus settled-event identity, team, and call kind; enforce uniqueness in the routing ledger. A retry should safely find the existing result and complete the board marker. Add fault injection at both sides of this boundary. Consider the analogous shared-spend/board write sequence in `board/spend.py` too; it also spans two stores without reconciliation, although duplicate spend was not separately reproduced in this review.

**A06 — Recheck pause and eligibility between actions.** `teams/runner.py:95` checks the paused flag once, before iterating a precomputed candidate list. In a two-artifact probe, the first action returned `Paused`; both actions still ran and the step returned 2. The next runner step would honor the pause, but it is too late for the second action in the current batch.

Recheck project pause, artifact eligibility, and budget immediately before each action, including after waiting for a scheduler slot. Preserve the deliberate policy allowing non-pausable teams to continue. Add a test where the first candidate pauses and the second worker is never called.

**A07 — Keep blocking checks off the event loop.** `ExecutorTeam.act()` and `_attempt()` directly call `WorktreeToolbox.check_report()`, which synchronously runs sandbox processes. Awaiting these async team methods does not make their synchronous portions concurrent. `Project.step()` correctly offloads promoters/controllers, and LangChain can offload synchronous tools, but those do not cover the executor's direct preflight and final validation calls.

A lengthy check in one project therefore delays other projects' async progress. Move synchronous orchestration/check sections to controlled worker threads or introduce an async sandbox/check interface. Keep board access serialized rather than scattering concurrent calls over the same connection. Add a two-project test with a deliberately blocked check and verify the other project continues. Scheduler admission should also cover direct specification, campaign, and evaluation calls if `max_model_calls` is intended as a universal limit.

**A08 — Bound disk and wall time as well as retained output.** `workspace/sandbox.py:93` sends all output into a temporary host file and truncates only when reading it after termination. Memory retained by the orchestrator is bounded; bytes written to disk are not. The writable worktree copy is also a host mount without an application-level quota. A noisy or write-heavy candidate can exhaust disk before the timeout. No disk-filling experiment was run.

Use a bounded output drain/ring buffer, explicit output limits, and an appropriate writable-storage quota for the supported execution environment. Add timeouts and bounded output to Git subprocesses (`workspace/git.py`), distinguishing infrastructure failure from merge conflict. Test with small artificial limits rather than filling a real filesystem. Git timeouts already appear in the backlog; this audit reinforces that priority rather than presenting it as newly discovered.

**Evaluation and research improvements.**

- **Separate product conformance from evidence for the swarm.** The reported parser results support that useful programs can be produced under these setups. They do not yet show that multi-agent coordination is cheaper or more effective than a strong single-agent loop with identical tools, feedback, and budgets. The roadmap already makes this a future study and candidly describes TOML as weak evidence for the swarm. Preserve that distinction in introductory claims.
- **Create immutable run manifests.** Record the framework commit, resolved config, prompt hashes, model identifiers, seed/spec/test hashes, split IDs, sandbox image digest, initial routing-ledger state, and any human intervention. Store per-run cost by phase and include failed attempts. The board is useful evidence, but its event history alone does not reconstruct every changing input to a run.
- **Use stronger independent validation.** Every-fifth-case splits are reproducible but do not ensure semantic independence between near-duplicate cases. Add grouped splits, fresh adversarial examples, and eventually a separate problem untouched during framework tuning. Report repeated-run variation when affordable. Historical model pretraining exposure to public suites remains distinct from keeping tests hidden during a run.
- **Version document evaluation cohorts.** `evaluation/questions.py:169` stores `memorized.json` beside the questions without binding it to the reader model, prompts, voting policy, or question-file hash. Reusing that directory with a new evaluator silently reuses the old exclusion list. Key this cache by evaluator and dataset identity; report both original and retained denominators. Treat infrastructure errors separately from genuinely unanswered questions so provider reliability does not silently become a document-quality score.
- **Make document release criteria explicit.** The status document reports substantial remaining citation overstatement after review. Question-answering performance, source support, coverage, readability, and editorial approval are separate dimensions. For publication where factual reliability matters, use claim-level support checks and human approval with the remaining uncertainty visible. The existing backlog recognizes much of this work.

**Maintainability and operation.**

- **Add CI now.** No repository CI workflow was found. Automate the existing passing formatter, lint, type, and deterministic tests. Separate marked slow tests and explicit live-provider runs; include a container integration job. The suite took about three minutes locally, so this is practical. CI and slow-test organization are already overdue backlog items.
- **Add schema evolution and replay fixtures.** Stored event envelopes are deserialized directly into today's strict models. Before more long-lived runs accumulate, introduce a schema version, migrations/upcasters, and representative older-board fixtures. Back up a board before migration and test reopening it with the new code. The backlog already names schema versioning.
- **Refactor concentrated responsibilities incrementally.** `cli.py` is 983 lines, `teams/executor.py` 812, and `project.py` 420 with a large assembly method. Extract cohesive command groups, attempt execution, and project assembly collaborators inside the existing packages. Preserve the explicit dependency model; avoid adding a generic workflow/plugin abstraction before a real use requires it.
- **Specify portable support.** `workspace/lock.py` requires `fcntl`, and the sandbox code assumes POSIX process-group and UID/GID behavior. State supported operating systems explicitly. If distributing the project publicly, choose and add a license; no tracked license file was found. These are packaging decisions, not reasons to rewrite the runtime.
- **Measure before optimizing persistence.** Incremental blackboard folding is a good foundation. Outcome recording still revisits artifact histories, the routing ledger loads rows into Python, and monitoring reconstructs views on requests. Benchmark representative long-run boards before adding indexes or materialized snapshots. Introduce idempotency keys and schema ownership first.
- **Reconcile documentation rather than adding another parallel roadmap.** `docs/BACKLOG.md` still describes Phase 8 at 43% and lists conflict refresh as deferred, while later results and `ExecutorTeam.sync()` show substantial subsequent progress. Distinguish dated experimental history from current status. Qualify README claims such as “nothing to deadlock”; distinguish default Docker isolation from configurable local/network-enabled execution; explain that JSON-delimited model input is an instruction-handling aid, not a security boundary. The essay's conceptual explanation is clear, but its test-protection claims should be revised alongside A02.

**Recommended implementation order.**

1. Fix A01 and A02 together, with verifier-failure and test-harness-tampering regression tests; add CI using the checks that already pass.
2. Implement universal spending admission, per-run authorization, and immediate pause enforcement (A03, A06).
3. Introduce stable attempt/outcome identities, repair cost attribution, and make routing publication idempotent (A04, A05).
4. Address blocking execution, output/disk bounds, Git timeouts, and durable-schema recovery (A07, A08).
5. Freeze run manifests and evaluator cohorts, reconcile current documentation, and then expand comparative experiments or research-source capabilities.

The recommendation is to preserve the architecture and harden its boundaries. The project has enough working mechanisms to justify that investment; the next major gains are in ensuring that “passed,” “cost,” “paused,” and “recovered” mean exactly what users expect.
