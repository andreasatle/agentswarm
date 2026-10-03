# Independent engineering audit

Date: 2026-10-03. Scope: repository architecture, implementation, tests, configuration, examples, and design documentation. Review only; this document is the only repository file created or modified by this audit.

## 1. Executive summary

AgentSwarm has a coherent architecture: typed events drive a blackboard; independently defined teams produce changes or findings; deterministic controllers route fixes and promote Git snapshots; Jev supplies separately recorded judgements. The software/document reuse is substantive, and the offline test infrastructure exercises real Git, SQLite, and the LangChain agent adapter rather than mocking every boundary.

The principal weaknesses are at boundaries that ordinary happy-path tests do not exercise: untrusted worktree contents versus host operations, Git changes versus event-log transactions, leases versus actual ownership, and evaluation results versus their provenance. These warrant attention before expanding autonomous or concurrent operation.

Most urgent:

1. Sandboxed code can replace writable worktree Git metadata, after which host Git can execute attacker-controlled hooks. A harmless local probe confirmed the host execution step.
2. Reviewer tools follow symlinks outside their worktree, exposing host files to model tools.
3. Promotions can move Git refs without durably recording matching events; restart does not reliably reconcile the two.
4. Lease expiry does not fence old workers, and a late release can erase a newer lease belonging to the same team.
5. Diffs between a merge's two parents attribute unrelated concurrent changes to the artifact being reviewed.
6. The new swarm failure path can close resources while another project's thread is still using them.

These are existing issues, not objections to missing future features. The scheduler, multi-project execution, higher-level judgement extensions, and operational reporting are still evolving; those are assessed separately below.

### Review baseline and verification

The repository was being changed by another agent throughout the audit. Initial HEAD was `adfbc6c`; scheduler/budget edits were already present. During review, those edits and new tests were committed as `f210991`, followed by swarm integration in `b462bde`. Both were inspected before finalizing this document. Final observed HEAD was `7469dbc`, a backlog-only update recording remaining live validation, held-out evaluation, and documentation work. Findings cite class/method names and approximate line anchors, which are more useful than treating this live checkout as an immutable release.

Two reproduced issues were independently fixed by `b462bde` while this audit was being written: budget admission once per batch, and fallback spend failing SQLite's thread restriction. Both probes were rerun successfully against the updated code. They are **not outstanding findings** below. Remaining budget accounting and execution-context lifecycle concerns are described separately.

Commands used the existing virtual environment directly to avoid dependency synchronization or lockfile changes:

| Verification | Result |
| --- | --- |
| `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider -m 'not docker and not live and not jev'` | 346 passed, 3 deselected, 131.16 seconds |
| Same pytest invocation targeting subsequently added `tests/control/test_scheduler.py` | 6 passed |
| Targeted recheck after `b462bde`: `tests/test_swarm.py tests/control/test_scheduler.py tests/test_cli.py` | 17 passed; overlaps earlier runs |
| `.venv/bin/ruff check --no-cache .` | Passed |
| `.venv/bin/ruff format --check --no-cache .` | Passed; no formatting performed |
| `.venv/bin/pyright` | 0 errors, warnings, or information messages |

The full suite describes the files present when it ran, not a final frozen snapshot containing every later concurrent edit. Ruff and Pyright were also rerun successfully after `b462bde`. No Docker execution or real model/Jev requests were made. Additional reproduction snippets used temporary directories outside the repository, harmless sentinel files, real Git/SQLite, and existing test helpers. No tests were added to the project.

## 2. Architecture assessment

### Components and execution path

`Project.open()` is the composition root. It constructs the SQLite event log and derived board, repository/worktrees, sandbox/checks, workers, judgement/decision services, teams, promoters, router, and supervisor. `Project.step()` currently visits teams, promoters, the finding router, and supervisor in that order.

`ArtifactFactory` creates work at the lowest level. `TeamRunner` builds context, checks relevance, claims an artifact, checks out its branch or original merge snapshot, runs its team, and appends returned events. Producers commit work; analyzers emit findings and version-specific sign-offs. Decomposition creates child artifacts and an umbrella.

At the first gate, `Promoter` judges the announced commit and queues it for merging. `MergeQueue` merges onto the shared level and reruns checks. Higher gates use `TrainPromoter`, advancing a prefix of the lower branch after required sign-offs and checks. `JoinPromoter` advances umbrellas when children have reached the next level. `FindingRouter` creates fix artifacts and resolves their original findings when fixes reach the relevant level.

`Judgement` separates question specifications, decision providers, threshold policy, mode selection, and decision persistence. Evaluation adds hidden-test, mutation, planted, or human outcomes; calibration and tuning consume those outcomes. The new scheduler limits resources, while the budget reads spend events from the board. `Swarm` runs project loops with `asyncio.gather`; gates, routing, and supervision now run through `asyncio.to_thread`.

### Strengths

- Package responsibilities largely match the proposal. `control/` is a justified addition for cross-artifact deterministic behavior.
- Tagged Pydantic events and exhaustive dispatch make the main state transitions readable. Append transactions and optimistic head checks give claim acquisition a sound starting point.
- Announced commit IDs are used for initial judgement, avoiding accidental judgement of a later branch tip.
- Provider-specific construction stays behind the LLM adapter; project presets do not force a provider.
- Real and fake collaborators make meaningful offline tests possible. Document teams reuse producer/analyzer/decomposer behavior with different instructions and evidence.
- Shadow mode, explicit outcomes, judge wording versions, and uncertainty intervals are unusually good foundations for evaluating model decisions.
- The release-train prefix choice is documented and defensible. Ordinary head-of-line blocking is an intentional trade-off, not by itself a defect.

### Architectural weaknesses

The board is presented as the source of truth, but Git refs, in-memory handled-version sets, cached check results, and external model calls have independent lifecycles. There is no durable protocol that ties these effects together after interruption. Likewise, a claim records temporary permission but does not enforce ownership of later writes.

Eligibility rules are distributed among runners, individual teams, three promoters, and controllers. Their different interpretations of escalated, dropped, approved, and umbrella artifacts already produce inconsistent behavior. A small shared set of lifecycle predicates would reduce that inconsistency without introducing a general workflow framework.

Interfaces are strongest around models and checks. Git remains a concrete dependency and execution-context boundaries are less explicit: synchronous gates call asynchronous workers through a thread, while spend callbacks mutate the board. This is now a correctness issue, not just an aesthetic concern.

## 3. Findings

Severity reflects the consequence in the stated scenario. “Existing issue” means implemented behavior is defective independently of unfinished features; it does not mean every normal run triggers it. Confidence distinguishes demonstrated behavior from conclusions supported only by tracing code.

### F01 — Writable worktree metadata can turn sandbox output into host execution

**Severity: Critical · Confidence: High · Classification: Existing issue**

**References:** `workspace/sandbox.py:131`, `DockerSandbox.command()`; `workspace/git.py:Git.attempt()`, `Worktree.commit()`; `teams/executor.py:ExecutorTeam.act()`.

The Docker mount makes the entire worktree writable, including its `.git` pointer file. Code executed in checks can replace that pointer and create another Git directory/configuration/hooks inside the mount. Subsequent host Git commands trust those files. Ignoring global and system Git configuration does not disable repository-local hooks or configuration. `Git` also inherits the host environment, including provider credentials if present.

**Evidence:** In a disposable worktree, a probe replaced `.git` with a pointer to another repository inside that worktree and installed a harmless pre-commit hook that wrote a sentinel. Calling the existing `Worktree.commit()` executed the hook on the host. This validates the host half of the path; the Docker flags establish that sandbox processes can write the relevant files. A full Docker exploit was not run.

**Why it matters:** Untrusted tests or generated code can cross the intended container boundary without a container-runtime exploit. Host execution has much greater authority than a sandboxed check.

**Suggested improvement:** Keep authoritative Git metadata outside the writable execution boundary and perform host Git operations with explicit trusted metadata/worktree paths. Disable hooks and external Git execution features deliberately. Prefer exporting a plain tree for checks and importing only validated file changes. Protecting the `.git` file alone is insufficient if an attacker can replace entries in its writable parent directory.

**Unfinished-work relationship:** Present in the existing sandbox/Git/executor integration; unrelated to the scheduler being unfinished.

### F02 — Reviewer file access is not confined to the project

**Severity: High · Confidence: High · Classification: Existing issue**

**References:** `teams/analyzer.py:63`, `AnalysisToolbox.read_file()/run_test()`; `teams/analyzer.py:257`, `ReadingToolbox.read_file()`; compare `teams/executor.py:110`, `WorktreeToolbox._safe()`.

Both reviewer readers reject textual `..` and absolute paths but follow symlinks without checking the resolved destination. These reads happen on the host, not in Docker. `AnalysisToolbox.run_test()` also writes through an existing `_analysis` directory without ensuring it is not a symlink outside the worktree. The equivalent risk exists for framework scratch writes such as `_hidden/`.

**Evidence:** A temporary `link.txt` pointing to a sentinel outside the checkout was read successfully through both reviewer toolboxes. The executor toolbox already contains a stronger resolved-path check, so protection differs by role.

**Why it matters:** Repository content or sandbox-created symlinks can expose host files through model tool replies or redirect framework writes. Plain path-string validation is not a security boundary.

**Suggested improvement:** Put confinement in a shared filesystem capability used by all host readers/writers, including scratch areas. Explicitly exclude Git metadata. Where concurrent untrusted writers are possible, use symlink-resistant operations or separate immutable snapshots, rather than relying only on a resolve-then-open check.

**Unfinished-work relationship:** Existing software and document reviewer behavior.

### F03 — Git promotion and event publication are not recoverable as one operation

**Severity: High · Confidence: High · Classification: Existing issue**

**References:** `gating/merge_queue.py:38–82`, `MergeQueue.process()/_merge()`; `gating/promotion.py:54`, `Promoter.step()`; `gating/train.py:124`, `TrainPromoter._promote()`.

The merge queue changes the live level branch before returning events; the promoter appends them only after processing the whole queue. The train similarly updates the upper ref before appending `Promoted` events. A crash, database failure, or later exception can leave successful Git changes without board records. First-level merges also become visible before their post-merge checks pass; a check exception does not reset the branch to its previous tip.

**Evidence:** Moving `candidate` to the checked target while omitting its event simulated the interruption window. The next train step returned zero actions, leaving the artifact at `experimental` in the board permanently: there were no remaining Git commits to rediscover. In another probe, a lost first-level promotion followed by another merge caused retry to assign the later merge commit to the earlier artifact.

**Why it matters:** Restarts can lose level transitions or associate evidence with the wrong change. “Replay the board” cannot repair unrecorded external effects. This undermines the claimed idempotent reconciliation behavior.

**Suggested improvement:** Check a detached candidate first, then publish with compare-and-swap. Persist a promotion intent containing artifact IDs, old/new refs, and decision IDs, and reconcile unfinished intents on startup. Make completion idempotent. No distributed transaction is required, but an explicit recovery protocol is.

**Unfinished-work relationship:** Existing single-project crash/failure paths, not merely future concurrency.

### F04 — Leases do not fence writes or uniquely identify ownership

**Severity: High · Confidence: High · Classification: Existing issue; greater exposure under concurrent execution**

**References:** `core/domain.py:Claim/Released`; `board/claims.py:ClaimRegistry`; `board/blackboard.py:83`; `teams/runner.py:113`; `workspace/git.py:217`; `gating/promotion.py:Promoter.step()`.

Claims identify only artifact, team, and expiry; releases identify only artifact and team. There is no generation token, renewal, or ownership check before publishing a worker's result. Long-running work can outlive the ten-minute lease. A successor can then acquire the same artifact while the old worker continues. Reused worktree paths can be forcibly removed as “stale” even while an old worker is active.

**Evidence:** Acquire a one-second executor lease, advance the fake clock, acquire a new executor lease, then release the first lease: the new lease disappears. The existing late-release test uses two different team names, which misses this case. `TeamRunner` also builds context before claiming and does not revalidate it afterwards. The merge queue is serialized only within one object, with no per-level ownership across project instances. An exception in `_judge()` occurs before its claim is added to the promoter's cleanup list.

**Why it matters:** The advertised exactly-one-worker property ends when a lease expires; stale work can overwrite new state. Multiple CLI processes targeting one project root are not explicitly prevented.

**Suggested improvement:** Either enforce one owner process per project root now, or implement claim IDs/generations, conditional release, heartbeat/renewal, and fenced publication. Revalidate state after claiming. Use unique worktree identities and a per-level publication guard. Ensure every successfully acquired claim enters cleanup immediately.

**Unfinished-work relationship:** Same-team release and exception cleanup are existing bugs. Requirements for multi-process ownership should be settled before broader concurrency, rather than assuming Phase 6 automatically supplies them.

### F05 — The “artifact's own change” includes unrelated reverse changes

**Severity: High · Confidence: High · Classification: Existing issue**

**References:** `workspace/git.py:197`, `Repository.merged_change()`; `board/context.py`; `teams/analyzer.py:Analyzer.act()`; `control/supervisor.py:Supervisor._change()`; `gating/join.py`.

`merged_change()` returns the merge's first and second parents, and callers perform a two-tree diff. If A and B branched from the same base and A landed first, the first parent of B's merge contains A, while the second parent (B's branch) does not. Comparing those parents therefore shows A's additions as deletions by B.

**Evidence:** Two independently branched artifacts added `a.txt` and `b.txt`. After promotion, B's reported diff included deletion of `a.txt`, even though the actual merged tree contained both files.

**Why it matters:** Analyzers, relevance decisions, umbrella judgement, context statistics, and drift checks receive incorrect evidence. Sequentially created branches in existing tests largely avoid this case.

**Suggested improvement:** Define whether each consumer wants the artifact branch delta or the change actually integrated. For the latter, compare the merge's first parent with the merge commit itself. Represent base/head semantics explicitly and test overlapping lifetimes of nonconflicting branches.

**Unfinished-work relationship:** Existing Git diff semantics, exercised without concurrent threads.

### F06 — Swarm failure can close resources still in use by sibling projects

**Severity: High · Confidence: High · Classification: Existing issue in newly implemented Phase 6 work**

**References:** `swarm.py:Swarm.run()/_run()/close()`; `cli.py:Cli.swarm()`; `project.py:Project.step()`.

`Swarm._run()` catches only `RuntimeError`. A Git error, SQLite error, or other exception propagates from `asyncio.gather`, which does not wait for all successful siblings to finish when one raises. `Cli.swarm()` immediately executes `swarm.close()` in its `finally` block. Sibling projects may still be running model calls or blocking promotion steps in worker threads. Cancellation of an awaiting task also does not stop its underlying `to_thread` operation.

**Evidence:** A probe used the real `Swarm.run()` with two lightweight project doubles: one raised `ValueError` after the other's worker thread started. Applying the CLI's `finally: swarm.close()` pattern closed the healthy project's resources while its worker was still active. The thread was then released and drained safely by the probe. The new concurrency test covers successful projects and a budget stop, not an exception during another project's promotion.

**Why it matters:** One project failure can cause secondary closed-database failures in otherwise healthy projects or interrupt publication after Git has moved, compounding F03. The change to `check_same_thread=False` removes the earlier fallback exception but does not establish a shutdown protocol.

**Suggested improvement:** Define isolation of project failures and structured shutdown. Either capture per-project errors and await every project, or explicitly cancel and drain siblings while waiting for in-flight blocking operations to finish before closing resources. Test a project failing while another holds an active promotion operation. Avoid treating every `RuntimeError` as merely a round-limit condition.

**Unfinished-work relationship:** New swarm code landed during review; this concrete cleanup path is already implemented. Broader scheduler policy remains a separate evolving concern.

### F07 — Escalation and retirement are not consistent lifecycle barriers

**Severity: High · Confidence: High · Classification: Existing issue**

**References:** `teams/runner.py:_candidates()`; `teams/analyzer.py:Analyzer.relevant()`; `teams/decomposer.py:Decomposer.relevant()`; `gating/train.py:102`; `gating/join.py:51`; `control/router.py:_create_fixes()`.

The runner does not exclude open escalations, and analyzers/decomposers do not consistently do so themselves. Their in-memory handled set can hide this until restart. A nonhuman train checks sign-offs, findings, level, and claims, but not an open escalation. Join iteration uses all artifacts, including dropped umbrellas, and the router can create new fixes for dropped artifacts.

**Evidence:** A signed-off artifact with an explicit open escalation was promoted by a normal train in a temporary fixture. Static tracing shows an unsized escalated decomposer can run again after recreating the runner, while a dropped umbrella can still be selected by the join loop.

**Why it matters:** A person may believe work is parked or retired while controllers continue it. An unresolved escalation can remain visible even after promotion.

**Suggested improvement:** Define shared lifecycle predicates for working, reviewing, joining, routing, and promoting. Make deliberate exceptions explicit: a human gate must allow its accepted state, and dropped code may intentionally ride a release train, but these are different from restarting dropped work.

**Unfinished-work relationship:** The human escalation workflow is implemented and documented; these are inconsistencies in that existing contract.

### F08 — Conflict rejection gives the executor no supported way to refresh its branch

**Severity: Medium · Confidence: High · Classification: Existing issue**

**References:** `gating/gate.py:Gate.conflict_finding()`; `workspace/git.py:Repository.ensure_branch()/checkout_artifact()`; `teams/executor.py:WorktreeToolbox.tools()`.

Conflict feedback tells the executor to rebase onto the target. The next attempt checks out its unchanged old branch, and its tools expose only file operations and checks—no target snapshot, merge, or rebase. Rejections therefore commonly repeat without giving the model the information or operation it was asked to use.

**Why it matters:** Parallel artifacts touching the same lines can exhaust retries even when an ordinary three-way merge could resolve them. Editing files to an accidentally mergeable result is not a dependable recovery mechanism.

**Suggested improvement:** Make branch refresh/conflict preparation a deterministic repository operation before retry, with conflict information exposed safely to the producer. Preserve prior attempts, and distinguish content rejection from integration conflict.

**Unfinished-work relationship:** Conflict recovery is described as supported; there is no corresponding deferred backlog item.

### F09 — Nested umbrella evidence collapses to one child's merge

**Severity: Medium · Confidence: High · Classification: Existing issue**

**References:** `gating/join.py:115`, `JoinPromoter._children_diff()`; `board/blackboard.py:Promoted` handling.

Joining an umbrella records the target tip as its first `merge_commit`. Later, an ancestor's `_children_diff()` checks for a merge commit before checking whether that child is itself an umbrella. The recursion branch is therefore bypassed for an already joined umbrella; its parent sees the tip merge's delta rather than the changes of all grandchildren.

**Why it matters:** The join judge can approve or reject a decomposed parent without seeing most of the implementation. A level-tip snapshot and a single-artifact merge are different concepts stored in one field.

**Suggested improvement:** Recurse on umbrella identity before interpreting a merge, or introduce separate typed references for an artifact integration merge and an umbrella's joined snapshot. Cover depth greater than one and unrelated commits sharing the target level.

**Unfinished-work relationship:** Nested decomposition is already allowed by `max_depth` and described in the join documentation.

### F10 — Calibration and labelling do not preserve the full decision provenance

**Severity: Medium · Confidence: High · Classification: Existing issue**

**References:** `judging/calibration.py:139`, `LabelledAnswers.of()`; `judging/audit.py:68`, `GateAudit.of()`; `judging/labelling.py:80`, `SpotCheck.run()`; `judging/decisions.py:DecisionDraft`.

Calibration groups by point, wording version, and field, but not concrete model version. A log spanning changes behind `jev-latest` pools different models. Gate audit groups only by point and active mode, mixing wording versions, model versions, fallback decisions, and threshold policies. Spot checking retrieves the current judge from the catalog and asks its current questions even for decisions made with old wording.

**Why it matters:** Metrics may be attributed to a model/policy that never produced all the observations. A human label can silently answer a different question than the one originally judged. The Phase 4 design explicitly promises version-separated audits.

**Suggested improvement:** Define an evaluation cohort including concrete model, judge identity/version, and relevant policy/task-distribution metadata. Offer explicit aggregate reports separately. Persist the asked question specification or make historical specifications addressable; otherwise refuse to label incompatible old versions.

**Unfinished-work relationship:** Version-aware calibration is implemented; these omissions remain independently of unfinished evaluation runs. Generalizing to more projects will increase their effect.

### F11 — Tuning output can change or invalidate the selected policy

**Severity: Medium · Confidence: High · Classification: Existing issue**

**References:** `judging/tuning.py:166`, `TuningReport.thresholds_yaml()`; `ThresholdTuner.passing()/failing()/tune()`.

Threshold selection uses full precision, but YAML emission rounds to two decimals. Distinct valid thresholds can become equal, and rounding a pass boundary downward can admit observations that the measured policy excluded. Emission also checks for available passing/failing sides rather than `ready`, and cannot verify that every field in the judge has a supported tuning row.

**Evidence:** A valid selected pass of `0.904` and fail of `0.901` emitted `pass: 0.90, fail: 0.90`; `Threshold` rejected that YAML. This probe used the actual report emitter and validator.

**Why it matters:** The configuration pasted by a developer may fail to load or implement different precision from the report. “Not ready” rows can still produce apparently usable YAML.

**Suggested improvement:** Serialize thresholds losslessly and validate the exact generated policy. Gate ready-to-use output on complete field coverage and readiness, or label exploratory output explicitly. Separately validate tuning arguments and evaluate selected thresholds on held-out tasks before using “ready” as an operational recommendation; in-sample Wilson figures do not account for threshold selection.

**Unfinished-work relationship:** Existing tuner/export behavior, not a request to finish the ongoing live calibration experiment.

### F12 — Evaluation assumes every recorded decision is the same software gate

**Severity: Medium · Confidence: High · Classification: Existing issue under supported-looking configuration**

**References:** `evaluation/run.py:EvaluationRun.run_task()/_record()/_scripted_catalog()`; `evaluation/suite.py:TaskSuite.project_config()`.

The run takes every newly recorded decision, then attaches `complete`, `tests_relevant`, and planted software outcomes to each. Size, relevance, split, join, and drift decisions do not share those fields; `Outcomes.check()` correctly rejects them. A project with the normal multi-team preset can therefore fail evaluation. Scripted variants replace the catalog with only an executor even if configured teams require other entries.

**Why it matters:** The generic project/judge API conceals a much narrower evaluator. Moving from the special greeter evaluation configuration to a real project can produce failure or misattribution, especially once artifacts split.

**Suggested improvement:** Explicitly validate the supported evaluation shape up front, or select target gate decisions and supply per-question outcome providers. Include project/run/task identity in shared decision records instead of relying only on list slicing and sequential execution.

**Unfinished-work relationship:** Generic document evaluation may reasonably be deferred. Clear validation and correct filtering are needed for the existing software evaluation interface regardless.

### F13 — Execution failure is treated as evidence of a product defect

**Severity: Medium · Confidence: High · Classification: Existing issue**

**References:** `workspace/sandbox.py:SandboxResult`; `gating/checks.py:CommandCheck.run_on()`; `teams/analyzer.py:AnalysisToolbox.failed()`; `evaluation/suite.py:HiddenTests.run()`; `evaluation/mutation.py:MutationTesting._passes()`.

The sandbox distinguishes timeouts and inability to execute, but downstream `CheckResult` collapses these into `passed=False`. An analyzer can cite a failed run caused by a broken runner, collection/import error, or missing infrastructure as accepted defect evidence. Hidden tests can label a task incomplete for an infrastructure outage; mutation testing counts any subsequent failed execution as a killed mutant.

**Why it matters:** Operational outages can create blocking findings and fix tasks or contaminate calibration data. Fail-closed promotion is sensible; turning the same outage into definitive ground truth is not.

**Suggested improvement:** Preserve typed execution status, separate infrastructure errors from completed test failures, and treat unavailable evidence as unknown/escalated. For analyzer findings, distinguish a meaningful assertion failure from a test that simply cannot run. Consider baseline comparison when claiming the change caused the failure; no expensive general proof system is necessary.

**Unfinished-work relationship:** Existing evidence and outcome-generation paths.

### F14 — Resource limits do not bound host-side output and reads

**Severity: Medium · Confidence: High · Classification: Existing issue**

**References:** `workspace/sandbox.py:LocalSandbox.run()/DockerSandbox.run()`; `workspace/git.py:Git.attempt()`; tool `read_file()` methods; `gating/checks.py:CommandCheck.OUTPUT_LIMIT`.

Commands capture all stdout/stderr into host memory, and file tools read complete files before slicing to their advertised limit. Docker memory limits apply to the container, not the Python process capturing its output. Git subprocesses and Docker availability/cleanup commands also lack timeouts. Local subprocess timeout does not provide process-group cleanup for descendants.

**Why it matters:** A noisy or hung generated check can exhaust or stall the orchestrator despite sandbox limits. Post-read truncation only bounds what is displayed.

**Suggested improvement:** Stream into bounded buffers, bound reads before allocation, set operation-specific timeouts, and terminate owned process groups or containers reliably. Keep the documented distinction that local mode is for trusted execution.

**Unfinished-work relationship:** Existing resource-management behavior; scaling up makes it more visible.

### F15 — Project inspection and startup have excessive side effects and weak identity checks

**Severity: Medium · Confidence: High · Classification: Existing issue**

**References:** `cli.py:169`, `Cli.run()` for `status`; `project.py:84`, `Project.open()`; `workspace/git.py:Repository.open_or_create()`; `board/eventlog.py:SqliteEventLog`.

`status` opens the full project: it can create a new repository/root and initialize model/Jev dependencies just to read the board. Missing credentials or provider packages can prevent inspection. Startup opens resources before validating all teams/judges, without an exception-safe cleanup stack. Existing roots are accepted without verifying that their recorded project identity and level branches match the supplied configuration; event reading does not reject a different stored project ID. The new `Swarm.open()` also lacks cleanup for previously opened projects when a later open fails, checks duplicate names only after opening them all, and does not reject two differently named entries sharing one resolved root.

**Why it matters:** A mistyped inspection path mutates disk, read-only diagnosis depends on service availability, invalid configuration leaks resources during repeated opens, and pointing a different configuration at a root can mix project identities or silently change runtime policy.

**Suggested improvement:** Reuse the board-only approach already used by `Escalations` for status. Validate catalog/config compatibility before side effects, use an `ExitStack` or equivalent during construction, and persist/validate root identity and structural configuration separately from intentionally editable policy.

**Unfinished-work relationship:** Existing CLI and composition-root behavior.

### F16 — Mixed shadow/active decomposition still acts on a shadow split ruling

**Severity: Medium · Confidence: High · Classification: Existing issue**

**References:** `teams/decomposer.py:Decomposer.act()`; `judging/judgement.py:ModeTable`; `tests/teams/test_decomposer.py`.

With `size:*` active and `split:*` shadow, a negative active size ruling reaches the split check. The code tests the split ruling without consulting `check.mode`; a negative or unsure shadow ruling escalates the artifact. Gate, size, relevance, and join paths otherwise explicitly honor decision modes.

**Why it matters:** A developer enabling one decision point for a calibration trial inadvertently gives another shadow point control of work. Existing decomposition tests apply one mode to both judgements and do not exercise this combination.

**Suggested improvement:** Define the intended behavior for this combination explicitly. Honor shadow split results as nonblocking, or require active split validation as a configuration prerequisite for active decomposition and explain that restriction up front.

**Unfinished-work relationship:** Per-point modes are already implemented and documented.

### F17 — Failed model runs lose spend, and budget stops lack a visible project summary

**Severity: Medium · Confidence: High · Classification: Existing issue in newly implemented Phase 6 work**

**References:** `teams/runner.py:91`, `TeamRunner.step()`; `llm/workers.py:LangChainWorker.run()`; `control/budget.py`; `control/supervisor.py:_budget_check()`; `cli.py:Report.render()`.

Usage is recorded only after the agent invocation returns. If several model calls succeed and a later call raises, `LangChainWorker.run()` converts the exception to `WorkerError` without reporting usage from those earlier calls. No per-call callback or failure-path accounting compensates for it. Gate/drift/fallback calls are not governed by the runner's budget check; the updated Phase 6 design explicitly permits gates to continue, so that alone is not counted as a bug.

**Evidence:** The exception path exits before `_report(Tokens.of(...))`. The original batch-admission probe did expose a separate bug, but `b462bde` fixed it: rerunning three $1 actions under a $1 budget now admits exactly one. That resolved issue is not included in this finding.

**Why it matters:** Failing runs can consume money that never reaches the persisted budget. The budget escalation has `artifact=None`, but board escalation views and normal status output omit project-wide escalation/spend events, so the reason for stopping is also difficult to see through the CLI.

**Suggested improvement:** Meter completed model calls even when a later call fails, and expose project-wide budget state in status/escalations. Clearly describe the gate-spend exception, including an LLM fallback that may cost more than Jev. A hard dollar cap would additionally need reservations; that stronger policy need not be assumed here.

**Unfinished-work relationship:** Spend and budget features are newly implemented. This is a concrete accounting/reporting gap, not criticism of an unimplemented strict financial cap.

## 4. Testing assessment

The test strategy is strong for a project at this stage. Shared helpers create real repositories and worktrees, SQLite tests check transactional claim races, fake clocks make lease/supervisor cases deterministic, and fake chat messages run through the real LangChain agent loop. The document end-to-end test crosses decomposition, reviews, release trains, joins, and human approval. Mutation/reference tests verify useful ground-truth assumptions rather than only checking mocked return values.

The main gap is composition of failure modes, not lack of unit-test count. The passing suite should not be read as validation of the host security boundary or crash recovery.

High-value additions, in order:

1. Sandbox-to-host metadata tampering; symlinks through every reader and framework scratch directory; bounded output.
2. Failure injection after a Git ref changes but before its event commits, including failure on a later item in a merge batch. Reopen and assert ref/board convergence.
3. Same-team lease replacement, stale publication, lease expiry during a check, and simultaneous promoters on different artifacts targeting one level.
4. Two artifact branches created before either is merged; assert the exact reviewed delta and absence of unrelated deletions.
5. Real SQLite plus a metered fallback worker; partial-success-then-error usage accounting.
6. Restart with an escalated planner/analyzer, dropped umbrellas, and unresolved escalation before train promotion.
7. Nested decomposition, mixed decision modes, full conflict-repair lifecycle, and upper-level findings followed through an actual fix and train. Router unit tests currently simulate promotion events directly.
8. Threshold YAML round-trip at close boundaries, cohort separation by model/wording/policy, old-version human labels, and evaluation with relevance/decomposition enabled.
9. Preserve the new multi-artifact budget and overlapping-project coverage, and extend it to a failing sibling, cancellation, and draining in-flight threads before resource closure.

Some assertions intentionally verify event order, which is reasonable where order is contractual. Avoid extending those into exact incidental event-count tests as spend and recovery events evolve. Preserve end-state invariants alongside sequence assertions.

The new sandbox-slot test verifies that three commands succeed but does not measure their peak simultaneous execution; it would not detect removal of the semaphore. The scheduler model-slot test does measure peak concurrency, a better pattern to reuse.

The suite took just over two minutes, reaching the backlog's stated point for considering a slow-test split. Keeping a fast deterministic subset and a full evaluation/integration run is appropriate; do not remove the real Git/SQLite coverage for speed. No repository CI workflow was present under `.github` when inspected. Docker/live checks were not run, so provider compatibility and actual container behavior remain unverified by this audit.

## 5. Simplification opportunities

- **One safe filesystem capability:** consolidate duplicate reviewer `list/read/show` behavior and enforce confinement below the toolbox layer. This directly addresses F02 and reduces divergent security fixes.
- **One definition of lifecycle eligibility:** share small predicates for escalation, retirement, approval, and umbrella handling. Keep role-specific exceptions explicit rather than creating an elaborate generic state-machine engine.
- **Explicit execution-context ownership:** prefer usage/results returned to the project owner over callbacks that can mutate SQLite from arbitrary threads. Align judgement/worker async interfaces as Phase 6 develops.
- **Separate snapshot meanings:** distinguish source-branch delta, integrated merge, current level snapshot, and umbrella aggregation. Overloading `merge_commit` saves a field but creates incorrect downstream assumptions.
- **Keep evaluation intentionally scoped:** either validate a software-gate-only evaluator or introduce a small outcome-provider interface. Avoid a superficially generic API backed by hard-coded field names.
- **Reduce redundant data boilerplate selectively:** many modules repeat identical `Frozen` base classes. A shared base is reasonable if their contracts really match, but this is optional cleanup, far below correctness work.
- **Retain useful small abstractions:** the `PromotionLoop`, `Worker`, `Sandbox`, clock, judgement, and catalog abstractions have concrete uses. There is no evidence that flattening them would improve the project. Likewise, simple document subclasses containing instructions and finding kinds are appropriate reuse.

No substantial dead subsystem was established. The old judge-name alias supports historical logs and should not be removed as “dead code.” The unused-looking direct `langgraph` dependency and future-facing configuration knobs merit checking before removal, not speculative cleanup.

## 6. Suggested improvements

### Address soon

- Close the host/sandbox filesystem and Git trust gaps (F01–F02).
- Make promotion publication recoverable and avoid publishing unchecked live refs (F03).
- Fix lease identity/cleanup and explicitly prevent unsupported multiple owners (F04).
- Correct integrated diff semantics and make swarm failure cleanup safe (F05–F06).
- Enforce escalated/dropped lifecycle behavior centrally (F07).
- Preserve exact threshold values in emitted configuration and record spend from partially failed runs (F11, F17).

### Address before the architecture stabilizes

- Decide the authoritative snapshot, ownership, and recovery contracts; test them across process restart.
- Provide a deterministic conflict retry path and correct nested umbrella evidence (F08–F09).
- Preserve evaluation provenance and validate evaluator capabilities (F10–F12).
- Distinguish failed checks from unavailable evidence and bound host resource consumption (F13–F14).
- Separate inspection from runtime initialization, validate root identity, and make startup exception-safe (F15).
- Specify and test mixed decision modes (F16).

### Longer-term improvements

- Add incremental/indexed board and decision queries if history size warrants it. `Supervisor` repeatedly loads all decisions, histories are scanned for versions, `_handled` accumulates old versions, and all spend remains in memory. These are credible growth costs, but no benchmark currently establishes a bottleneck requiring a storage redesign.
- Add schema/event compatibility policy before long-lived deployments. Current serialized discriminated events have no migration/version mechanism for future incompatible changes.
- Add operational summaries for outstanding work, waits, project-wide escalations, actual spend, and unpriced usage. Keep judgement rulings distinct from final gate actions: a ruling to promote may later fail merge checks or require a human.
- Validate calibrated policies on held-out task families and inspect selection bias. Relevance outcomes observe only work that actually ran; repeat decisions on one task are not automatically independent evidence.
- Add CI with offline checks and a separate container/integration job; pin or deliberately version sandbox dependencies for reproducible check environments.

### Optional cleanup

- Reconcile README/reference phase status and backlog entries with completed work. Several still list implemented relevance filters, escalation resolution, retries, model tiers, and the harder suite as deferred. The reference still says only software has a preset despite the document preset. The final backlog update already schedules documentation refresh and held-out/live validation: these are acknowledged work in progress, not omitted commitments.
- Validate duplicate gates from the same level, positive budgets/timeouts/round limits, and finite in-range Jev probabilities. `jev.fallback` validation currently sits inside the team loop, so a configuration without teams skips it. These are concrete boundary-hardening opportunities, not reasons to redesign Pydantic usage.
- Consider exposing immutable/read-only board views; `Blackboard.current()` returns the live mutable fold, and frozen Pydantic objects containing dictionaries are not deeply immutable. Current core callers mostly respect the convention, but extensions can bypass the claimed append-only mutation path.

## 7. Questions and uncertain areas

These are decisions or unfinished areas, not additional proven defects.

1. **Single owner or multiple owners per project?** Phase 6 describes several independent projects, which does not itself require concurrent workers on the same root. A process lock may be the simplest near-term contract; if multiple owners are intended, fencing and per-level serialization are prerequisites.
2. **What does an upper-level sign-off certify?** The runner always checks out an artifact's original first-level merge, including for reviewers at higher levels. This is coherent for reviewing an original contribution, but does not necessarily review the current release snapshot containing later fixes or interacting changes. The document audience reviewer at `final` makes this distinction particularly important.
3. **What proves a finding fixed?** The router closes it when the fix artifact reaches the finding's level, without deterministically rerunning the original reproducer. That is an explicit design choice. Is gate review of the fix task sufficient, or should evidence be structured and rechecked? The latter would reduce false resolution, but requires a deliberate evidence contract.
4. **Which work owns each execution context?** `b462bde` moves promoters, routing, and supervision to threads. Team relevance/context/checkout, analyzer Jev checks, and executor final checks still call synchronous operations on their caller; model tools may use LangChain's own execution threads. The new overlap test uses an asynchronously sleeping fake worker, so it does not establish latency isolation for all these paths. This remains a potential issue as Phase 6 develops, rather than proof that project overlap is absent. Define board/connection ownership and shutdown before moving more operations between contexts.
5. **How strict is the daily budget?** The current design suggests a soft per-action admission limit, not a guaranteed monetary ceiling. Clarify whether diagnostic gates and fallbacks may spend after exhaustion and whether unknown provider usage should be treated as zero or unknown.
6. **Does idle mean finished or waiting?** `run_until_idle()` returns when a step has no action, including parked, claimed, paused, or budget-exhausted work. Timer-driven stall detection and next-day budget resumption require another caller to step later. The current finite CLI behavior is understandable; a daemon/swarm contract should distinguish these states.
7. **How much operational hardening is intended now?** The proposal names a secret scanner, but no built-in/preset scanner was found. Arbitrary hard checks allow one to be configured. State clearly whether scanning is required behavior or a future policy; do not imply that the existing container or prompt delimiter prevents secrets entering context.
8. **Which configuration changes are allowed on an existing root?** Policy edits may be intentional; level-ladder and project-identity changes need validation or migration. The present reopen path does not distinguish them.
9. **Should audit certify a judge or the entire promotion system?** `GateAudit` intentionally evaluates recorded rulings, not actual `Promoted`/`Rejected` events after merge checks and human approval. Keep this distinction visible in reports and exit criteria.

The evolving scheduler/swarm work and deliberate deferrals are not counted as failures to implement the original proposal. The findings above focus on concrete behavior already present and on boundary decisions that would otherwise become harder to correct as more teams, domains, and concurrent projects are added.
