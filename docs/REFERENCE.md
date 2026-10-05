# AgentSwarm Reference

Everything in one place: concepts, files, configuration, events, teams, gates, judges, the decision log,
evaluation and the command line. For a guided walk through the system, read the [tutorial](../tutorial/)
first. The design and every decision behind it are in [FRAMEWORK_PROPOSAL.md](../FRAMEWORK_PROPOSAL.md)
and [docs/design/](design/); deferred work is in [BACKLOG.md](BACKLOG.md). Section numbers (§) refer to
the proposal.

## Status

| Phase | What it built | State |
| --- | --- | --- |
| 0 | Event log, views, claims, git levels, merge queue, team loop | done |
| 1 | Real executor (LangChain, any provider), Docker sandbox, CLI | done |
| 2 | Jev as gate judge in shadow mode, decision log, automatic ground truth, calibration | done |
| 3 | Analyzer teams, finding router, release train, decomposition, supervisor, escalation queue, presets | done |
| 4 | Active per decision point, retries and fallback, tuning, gate audit, model tiers, harder and held-out suites | done, except proving false promotions ≤ 5% (needs more data; [phase-4](design/phase-4-jev-active.md)) |
| 5 | Documents as a second domain: writer, outliner, three reviewers, the `document` preset | done (live handbook run to published) |
| 6 | Several projects at once: spend on the board, shared scheduler, daily budgets, `agentswarm swarm` | done (live greeter + handbook swarm) |
| 7 | Hardening from an independent audit ([CODEX_AUDIT.md](../CODEX_AUDIT.md)): sandbox and file boundaries, crash-safe promotion, claim ownership, evidence quality | done ([phase-7](design/phase-7-hardening.md)) |
| 8 | Small swarms against bigger models: narrow red teams, `agentswarm compare`, routing with model curves, the atomic benchmark (`agentswarm probe`) on imported EvalPlus tasks, equal-cost baselines | in progress: probes and baselines ([phase-8](design/phase-8-goldilocks.md)) |
| 9 | Conventions that hold: layout, packaging, lint and type checks in the preset; a conventions file; `agentswarm new` | planned ([phase-9](design/phase-9-conventions.md)) |
| 10 | Robustness for high-level goals: attempts selected by checks, red-flag and resample, protected tests, clarifier, verified decompositions ([RESEARCH.md](RESEARCH.md)) | planned ([phase-10](design/phase-10-robustness.md)) |
| 11 | Project types as data: one YAML file per type, sub-labelled presets, single inheritance | planned ([BACKLOG](BACKLOG.md)), due with a third project type |

## 1. Layout

```
src/agentswarm/
  core/        domain.py (artifacts, findings, events), config.py (ProjectConfig), clock.py, presets/
  board/       eventlog.py, blackboard.py (BoardViews), claims.py, context.py, artifacts.py, spend.py
  workspace/   git.py (Repository, Worktree), sandbox.py (DockerSandbox, LocalSandbox, RunCopy),
               files.py (ConfinedFiles), lock.py (RootLock, RootIdentity)
  gating/      checks.py, gate.py, merge_queue.py, promotion.py (Promoter), train.py, join.py,
               publication.py (LevelPublisher)
  judging/     judges.py, policy.py, judgement.py, deciders.py, decisions.py,
               calibration.py, tuning.py, audit.py, labelling.py
  jev/         api.py (wire format), spec.py (Ask, QuestionSet), client.py (JevClient)
  llm/         models.py (ChatModels), workers.py (Worker, LangChainWorker): the only LangChain imports
  teams/       runner.py (Team, TeamRunner), executor.py, analyzer.py, relevance.py, decomposer.py,
               documents.py (writer, outliner, reviewers), catalog.py
  control/     router.py (FindingRouter), supervisor.py, escalations.py, scheduler.py, budget.py
  evaluation/  suite.py, run.py, faults.py (variants), mutation.py
  project.py   Project, ProjectServices: one project wired together
  swarm.py     Swarm: several projects in one process, sharing a scheduler
  cli.py       the `agentswarm` command
tests/         mirrors src/agentswarm/; shared helpers in tests/support/
examples/      quickstart (no model needed), greeter (software), greeter/evaluation (suite, hard/, heldout/),
               handbook (documents), swarm-quickstart.yaml, swarm-live.yaml
docker/        sandbox-python.Dockerfile
```

## 2. Concepts

| Term | Meaning |
| --- | --- |
| **Project** | One north star, one repository, one board, one decision log. |
| **North star** | The project's goal in one paragraph; judges and drift checks measure against it. |
| **Artifact** | A unit of work: a task plus a git branch. Ids `a-0001`; children of a split `a-0001.1`. |
| **Level** | A maturity stage, per project (`raw → experimental → candidate → production`, or any ladder). The lowest level holds one branch per artifact; each level above is one shared branch of the same name. |
| **Blackboard** | The project's append-only event log plus the views folded from it. Nothing changes state except appending events. |
| **Team** | One kind of work at one level, run as a loop (§10.2). Teams never call each other. |
| **Claim** | A lease: one team works on one artifact at a time. Expired leases are ignored. |
| **Gate** | The rule for moving up a level: hard checks, then optionally a judge, then a policy. |
| **Judge** | A set of yes/no (Noul) questions phrased so that yes is good, asked of a decider (Jev). |
| **Policy** | Plain code turning a judge's probabilities into promote, reject (with findings) or escalate. |
| **Decision** | One judgement, logged with its exact input, answers, ruling, mode and judge version. |
| **Outcome** | The ground truth for one question of one decision, with its source. |
| **Shadow / active** | A decision point in shadow mode is logged but doesn't act; active acts on the ruling. Set per point. |
| **Finding** | Typed feedback on an artifact (kind, severity, detail); resolved by an event. |
| **Sign-off** | An analyzer team's statement that it examined an artifact's current version. |
| **Release train** | Promotion above the base level: the upper branch fast-forwards over a ready prefix of the lower one. |
| **Umbrella** | An artifact split into children; it does no work and joins when they are done. |
| **Escalation** | A question for a person; resolved by retry, accept or drop. An artifact with an open escalation is *parked*: no team, router or join acts on it until it's resolved. |
| **Active artifact** | Neither parked nor dropped (`BoardViews.active`): the one rule every controller checks. |
| **Cohort** | One decision point, judge wording and concrete model. Calibration, tuning and audits never pool cohorts. |
| **Spend** | Model and Jev usage, priced from the config and recorded on the board (`Spent`). |
| **Swarm** | Several projects run concurrently in one process, sharing model, Jev and sandbox limits. |

## 3. A project on disk

Under the `--root` directory (it must be mountable by Docker, i.e. under `$HOME` with Colima/Docker Desktop):

| Path | What |
| --- | --- |
| `blackboard.sqlite` | The event log (table `events`: seq, at, kind, payload JSON). |
| `decisions.sqlite` | The decision log (tables `decisions`, `outcomes`). |
| `repo/` | The git repository; the main checkout stays on a detached HEAD. |
| `worktrees/<artifact>` | A team's checkout of an artifact branch, removed after it acts. |
| `worktrees/_gate-*`, `_level-*`, `_train-*`, `_join-*`, `_hidden-*`, `_mutation-*` | Throwaway checkouts used by gates, the merge queue, trains, joins and evaluation. |
| `worktrees/.run-*` | A copy of a checkout, without `.git`, made for one sandboxed command and deleted after it. |
| `.agentswarm.lock` | Held by the one process that has the project open (its pid inside). |
| `identity.json` | The project name and levels this root was created with; another config is refused. |

Branches: one per level above the lowest (`experimental`, `candidate`, ...), plus `raw/<id>-<slug>` per
artifact. Analyzers' scratch tests live in `_analysis/` inside a detached checkout and are never committed.

## 4. Configuration

One YAML file per project, validated into `ProjectConfig` when loaded. Unknown keys are errors.
`agentswarm config FILE` prints the effective configuration (with the domain preset applied).

### Top level

| Key | Type | Default | Notes |
| --- | --- | --- | --- |
| `project` | str | required | Name; also the event log's project id. |
| `domain` | `software` \| `document` | required | Selects the preset. |
| `north_star` | str | required | |
| `levels` | list of names | required (or preset) | At least two, unique. |
| `seed` | path | none | A directory whose files form the first commit; relative to the config file. |
| `sandbox` | see below | Docker | |
| `models` | name → model | `{}` | |
| `checks` | name → check | `{}` | |
| `teams` | list | `[]` | Each `use` at most once. |
| `gates` | list | `[]` | |
| `budgets` | see below | | |
| `supervisor` | see below | | |
| `jev` | see below | | |

Validation rules: teams watch known levels; gates connect adjacent levels and name defined checks; teams
name defined models and checks; `requires:` names teams watching the gate's lower level; the gate out of
the lowest level can't require sign-offs; a judge sits only on the gate out of the lowest level (trains promote on sign-offs; backlog);
`jev.fallback` names a defined model; a team sets `model` or `model_tiers`, not both.

### `models.<name>`

| Key | Default | Notes |
| --- | --- | --- |
| `model` | required | `"<provider>:<model>"`, e.g. `ollama:gemma4:e4b`, `openai:gpt-5`, `anthropic:claude-sonnet-4-6`. Built with LangChain's `init_chat_model`; install the provider's extra (`uv sync --extra ollama`). The model must make real tool calls. |
| `temperature`, `max_tokens`, `base_url`, `timeout_s` | none | Passed only when set. `max_tokens` limits each reply's output; for Ollama it is passed as `num_predict`, the only name Ollama's model reads. |
| `context_window` | Ollama: 32768 | Tokens the model reads at once (Ollama's `num_ctx`). Ollama's own default, 4096, silently drops the start of longer conversations. Refused for providers whose window is fixed. |
| `usd_per_million` | none | `{input: 3.0, output: 15.0}`: the price used for spend and budgets. Unpriced models record $0 and are counted as unpriced. |

### `checks.<name>`

| Key | Default | Notes |
| --- | --- | --- |
| `command` | required | Run in the sandbox on a throwaway copy of the checkout (nothing it writes is kept); exit 0 passes. |
| `timeout_s` | 600 | A timeout kills the whole process group (or the container). |
| `kind` | `test_failure` | The finding kind a failure produces. |
| `failure_exit_codes` | none | Exits meaning "ran and found a problem" (pytest: `[1]`). Any other non-zero exit, a timeout or a command that can't start is an *error*: it still blocks a gate, but is never evidence for an analyzer. Unset: every non-zero exit is a failure. |

### `sandbox`

| Key | Default | Notes |
| --- | --- | --- |
| `kind` | `docker` | `local` runs on the host with a minimal environment: tests and trusted checks only. |
| `image` | `agentswarm/sandbox-python:3.13` | Build: `docker build -t agentswarm/sandbox-python:3.13 -f docker/sandbox-python.Dockerfile docker`. |
| `network` | false | |
| `cpus`, `memory`, `pids` | 2.0, `2g`, 256 | Plus a read-only root, `/tmp` tmpfs, dropped capabilities, your uid. |

### `teams[]`

| Key | Default | Notes |
| --- | --- | --- |
| `use` | required | A name in the team catalog (section 7); also the team's name. |
| `watches` | required | Its level. |
| `model` / `model_tiers` | none | One model, or tiers from cheap to strong (the executor moves up a tier per rejection). |
| `checks` | `[]` | Checks the team may run while working; analyzers run test files with the first. |
| `max_model_calls` | 40 | Per action; the agent stops there. |
| `constraints` | `[]` | Extra rules sent with every task. |
| `relevance` | none | A relevance judge asked before working (analyzers). |
| `max_depth` | 3 | Decomposer: how deep tasks may be split. |

### `gates[]`

| Key | Default | Notes |
| --- | --- | --- |
| `from`, `to` | required | Adjacent levels. |
| `hard_checks` | `[]` | Check names, run in order; the first failure rejects with a `BLOCKER` finding. |
| `judge` | none | A judge name (lowest gate only, for now). |
| `thresholds` | `{}` | Per question: `{pass: 0.9, fail: 0.2}` (inclusive; fail < pass). Missing questions use 0.9 / 0.2. |
| `requires_human` | false | Escalate instead of promoting; a person accepts. |
| `requires` | `[]` | Teams whose sign-off each promoted artifact needs (gates above the base). |

### `budgets`, `supervisor`, `jev`

| Key | Default | Notes |
| --- | --- | --- |
| `budgets.max_rounds_per_artifact` | none | More commits than this escalates. |
| `budgets.usd_per_day` | none | Spend for the current UTC day; when reached, teams start no new actions until the next day and the supervisor escalates once. A soft limit: gates and the fallback may still spend. |
| `supervisor.stall_after_minutes` | 30 | No event on an artifact for this long escalates. |
| `supervisor.max_rejections` | 3 | Rejections in a row since the last promotion. |
| `supervisor.drift_every` | 5 | Ask about drift after every N merged changes. |
| `jev.model` | `jev-latest` | Pin a version (`jev-1.13.0`) once thresholds are tuned. |
| `jev.mode` | `shadow` | The default for decision points `active` doesn't name. |
| `jev.active` | `[]` | Glob patterns of decision points that act, e.g. `gate:*`, `relevance:red_team`. |
| `jev.fallback` | none | A model that answers when Jev can't (marked `llm:`, uncalibrated). |
| `jev.timeout_s` | 30 | |
| `jev.usd_per_million` | none | Jev's price, for spend. |

### Presets

`domain: software` loads `src/agentswarm/core/presets/software.yaml`: levels raw → experimental →
candidate → production; a `unit_tests` check (pytest); the executor, integration tester and red team (all
on `models.coder`, which the project must define); a judged raw gate; a release train to candidate needing
both analyzers' sign-offs; a human production gate.

`domain: document` loads `core/presets/document.yaml`: levels draft → reviewed → final → published; a
`check_doc` check (the project's seed provides `tools/check_doc.py`); an outliner and a writer at draft, a
consistency and a fact checker at reviewed, an audience reviewer at final (all on `models.main`); a gate
judged by `SectionReadiness`; sign-off trains; a human publishing gate.

A project's own top-level keys replace the preset's whole (listing `teams` replaces all preset teams).

## 5. Events

Every state change is one of these, appended to the log. "Change" marks events that change the
artifact's version (`BoardViews.version`), which is what teams key on to act once per change.

| Event | Fields | Effect on the views | Change |
| --- | --- | --- | --- |
| `ArtifactCreated` | artifact | New artifact at the lowest level. | yes |
| `Claimed` | claim (artifact, team, expires_at, id) | Lease held until released or expired. | |
| `Released` | artifact, team, claim | Ends that claim only, so a late release never frees a newer one. | |
| `CommitPushed` | artifact, commit, model | Latest commit; becomes the unjudged commit; clears an approval. | yes |
| `FindingAdded` | finding (id, artifact, level, team, kind, severity, detail) | Open finding. | |
| `FindingResolved` | artifact, finding, resolved_by, reason | Closes the finding. | |
| `SignedOff` | artifact, team, version | Sign-off for that version. | |
| `Sized` | artifact, fits, children, decision | `fits=false` makes an umbrella. | yes |
| `Paused` / `Resumed` | reason | Project-wide pause of teams that start work. | |
| `Spent` | by, model, input_tokens, output_tokens, usd, priced | Project-wide spend (budgets, reports). | |
| `PromotionStarted` | branch, previous, target, promotions | A level branch is about to move (recorded first). | |
| `PromotionSettled` | branch, moved | The move finished (with its promotions) or didn't happen. | |
| `Promoted` | artifact, from_level, to_level, gate_decision, commit | New level; closes findings of the level left; clears approval; first one records the merge commit. | yes |
| `Rejected` | artifact, level, findings, gate_decision | Open findings replaced by the gate's; commit judged. | yes |
| `Escalated` | artifact (or none: project-wide), reason, approval_for | Open escalation; commit judged. `approval_for` marks a human gate's own request. | |
| `EscalationResolved` | artifact, resolution (`retry`/`accept`/`drop`), note | Clears the escalation; accept re-queues the last commit, approved; drop retires the artifact. | retry only |

Finding kinds: `test_failure`, `build_failure`, `out_of_scope`, `incomplete`, `security`, `inconsistent`,
`too_large`, `edge_case`, `unsupported`, `unclear`. Severities: `info`, `minor`, `major`, `blocker` (major and blocker are
"blocking": they hold trains and get routed).

## 6. Views (`BoardViews`)

`artifact(id)`, `artifacts()`, `artifacts_at(level)` (live ones), `open_findings(id)`, `claim_on(id, now)`,
`active_claims(now)`, `history(id)`, `last_commit(id)`, `unjudged_commit(id)`, `version(id)`,
`merge_commit(id)`, `signed_off(id, team)`, `signed_off_by(id)`, `is_umbrella(id)`, `children(id)`,
`depth(id)`, `fix_for(finding)`, `last_activity(id)`, `open_escalation(id)`, `open_escalations()`,
`accepted(id)`, `dropped(id)`, `active(id)`, `paused`, `spent()`, `spent_since(t)`, `project_escalations()`,
`moving()`. Folding the same log always gives equal views (`Blackboard.rebuild`).

## 7. Teams

Every team's instructions, payload, tools and answer are in [PROMPTS.md](PROMPTS.md).

| `use` | Watches | Does | Notes |
| --- | --- | --- | --- |
| `executor` | lowest | A model edits the artifact's branch with a toolbox (list/read/write/delete files, run checks), commits. | Model tiers; waits for sizing when the project has a decomposer; commits and escalates failing or incomplete work. |
| `fake_executor` | lowest | Writes the task to a file. | No model; for tests and dry runs. |
| `integration_tester` | above lowest | A model writes and runs tests through real call paths; reports problems. | Analyzer; finding kind `test_failure`. |
| `red_team` | above lowest | A model tries to break the change with hostile input. | Analyzer; finding kind `edge_case`. |
| `decomposer` | lowest | Asks Jev if a task fits one branch; if clearly not, a model splits it; Jev checks the split. | Children `a-0001.1`; umbrella joins later. Splits only when `size:*` is active; a shadow `split:*` never blocks. |
| `writer` | lowest | The executor for prose: edits sections, runs `check_doc`, commits. | Document preset. |
| `outliner` | lowest | The decomposer for documents: splits a goal into sections. | Document preset. |
| `consistency_checker` | above lowest | Contradictions, terminology, cross-references. | Reviewing analyzer; kind `inconsistent`. |
| `fact_checker` | above lowest | Claims that are wrong or unsupported. | Reviewing analyzer; kind `unsupported`. |
| `audience_reviewer` | above lowest | What the intended reader would find unclear. | Reviewing analyzer; kind `unclear`. |

Analyzers examine a merged artifact in a detached checkout of its merge commit; the change shown is what
the merge brought into the level (`merge^1..merge`). They ask their relevance judge first (if configured),
and keep only problems with evidence: for code, a test they wrote that *fails* (a test that can't run is no
evidence); for prose, a verbatim quote of at least 12 characters that really is in the named file. They end
with a sign-off. Pausable teams (executor, decomposer) wait while the project is paused; analyzers
don't.

## 8. Gates and promotion

| Loop | Serves | Rule |
| --- | --- | --- |
| `Promoter` | the gate out of the lowest level | For each artifact with an unjudged commit: claim it, run hard checks on that exact commit (detached), ask the judge, apply the policy. Promote goes through the merge queue: merge onto the level tip in a detached checkout, rerun the checks on the result, and only then move the level branch. |
| `TrainPromoter` | gates above | Longest ready prefix of the lower level's first-parent line (signed off, no blocking findings, unclaimed, no open escalation except the gate's own approval request; dropped artifacts pass); hard checks on its last commit (the first failing commit gets a blocker); fast-forward. Human gates escalate once and promote up to the last accepted artifact. |
| `JoinPromoter` | umbrellas | When every child reached the next level: that level's hard checks on its tip, then `JoinCheck`; else escalate once. |

A person's `accept` replaces judgement (Jev, `requires_human`, a failed join) for one gate, never the hard
checks.

Every level branch moves through `LevelPublisher`: record `PromotionStarted`, move the branch by
compare-and-swap, record the promotions with `PromotionSettled`. At the start of every project step,
`recover()` settles a move a crash interrupted: recorded as promoted if the branch reached its target,
otherwise as not having happened (the commit is judged again).

## 9. Judges and decision points

| Judge | Questions (yes is good) | Used at |
| --- | --- | --- |
| `ChangeReadiness` (alias `RawToExperimental`) | `in_scope`, `tests_relevant`, `complete` (each with a finding kind) | `gate:<lowest>-><base>` (software) |
| `SectionReadiness` | `on_topic`, `clear`, `complete` | `gate:draft->reviewed` (documents) |
| `ConsistencyRelevance`, `FactRelevance`, `AudienceRelevance` | `relevant` | the document reviewers' relevance points |
| `IntegrationRelevance` | `relevant` | `relevance:integration_tester` |
| `AdversarialRelevance` | `relevant` | `relevance:red_team` |
| `SizeCheck` | `fits` | `size:<lowest>` |
| `SplitCheck` | `smaller` (sees the proposed children) | `split:<lowest>` |
| `JoinCheck` | `accomplished` | `join:<level>-><next>` |
| `DriftCheck` | `on_course` | `drift` |

**Policy (§11.2):** per question, `p ≥ pass` passes, `p ≤ fail` fails, otherwise unsure; any fail rejects
(one finding per failed question with `Fails`), else any unsure escalates, else promote.
**Modes:** each point is shadow (logged, the system acts as if there were no judge) or active, resolved by
`jev.active` patterns. **Versions and cohorts:** a judge's version is a hash of its questions' wording;
decisions record it and the concrete model, and reports keep each cohort (point, version, model) apart.

**Deciders:** `JevDecider` (with retries for timeouts and 429/5xx), `LLMDecider` (fallback; yes/no/unsure →
0.95/0.05/0.5, marked `llm:<model>`, excluded from calibration), `FallbackDecider`, `FakeDecider`
(answers per spec, for tests).

## 10. Decision log and outcomes

A `Decision` holds: id, point, artifact, commit, base (where the change forked, at judging time), mode,
judge, judge_version, state (what the judge saw: north star, task, open findings, diff truncated to 60,000
characters, proposal), answers, ruling, model, time.

| Outcome source | Strength | Ground truth for |
| --- | --- | --- |
| `human` | 3 | anything (`agentswarm label`) |
| `planted` | 2 | scripted evaluation variants (known by construction) |
| `hidden_tests` | 1 | `complete` |
| `mutation` | 1 | `tests_relevant` |
| `team_result` | 1 | relevance: did the team's work find anything? |

Every outcome is kept; readers use the strongest, then the latest.

| Report | Command | What |
| --- | --- | --- |
| Calibration | `calibrate` | Per cohort and question: 10 bins, ECE, Brier, coverage and accuracy at thresholds, sources. |
| Tuning | `tune` | Lowest pass / highest fail threshold reaching a target precision (Wilson bounds). YAML per gate and model, written exactly; pasteable only when every question of the judge is tuned and ready, otherwise commented out with the reasons. "Ready" is in-sample: confirm on held-out tasks. |
| Gate audit | `audit` | Active gate rulings per cohort: false promotions (≤ 5%), false rejections (≤ 10%), escalations (≤ 30%), judged by the 95% Wilson upper bound. It audits the judge's rulings, not the promotions that followed. |

`label` offers only decisions asked with the judge's current wording, so a person answers the question that
was judged.

## 11. Controllers

- **FindingRouter:** open major/blocker findings above the lowest level become one fix artifact each
  (`fixes` = the finding, parent = the artifact); a finding is resolved when its fix reaches its level.
- **Supervisor:** escalates stalls (time since an artifact's last event), repeated rejections and rounds
  over budget, once per state; asks `DriftCheck` after every N merged changes and pauses on a clear "no"
  when `drift` is active.
  It also escalates once a day, project-wide, when the daily budget is spent.
- **FindingRouter, joins and team runners skip inactive artifacts** (parked or dropped).
- **Escalations:** `agentswarm escalations / resolve / resume`; needs only the board and decision log.
- **Scheduler** (one per process; in a swarm, shared): at most `max_model_calls` team actions at once
  (default 4, served in arrival order), `jev_per_minute` Jev requests (1,200), `max_sandboxes` sandbox runs
  (4).

## 12. Evaluation

A suite (`examples/greeter/evaluation/suite.yaml`, `hard/suite.yaml`, and `heldout/suite.yaml`, 16 tasks the
thresholds were never tuned on) names a project config, a judge and a test command
(`test_failure_exit_codes`, default `[1, 2]`: for hidden tests a collection error means the code under test
can't be imported, i.e. incomplete), and lists tasks with hidden tests and reference solutions. Before any
run, the evaluator checks that the first gate's judge asks what it scores and that scripted variants face an
executor-only project; only that gate's decisions get truths. Hidden tests that can't run record no
outcome. `EvaluationRun` runs every task in
every variant in a fresh project, Jev in shadow (or `--active`), with one shared decision log:

| Variant | Writes | Known truths |
| --- | --- | --- |
| `executor` | the configured model's work | `complete` (hidden tests), `tests_relevant` (mutation) |
| `reference` | reference solution + hidden tests as unit tests | in scope, tests relevant, complete |
| `out-of-scope` | the same + an unrelated file and function | not in scope |
| `trivial-tests` | reference + `assert True` test | tests not relevant, not complete |

Mutation operators (on changed lines of changed non-test files, at most 12 per file): comparison and
arithmetic swaps, flipped booleans, changed strings and integers, `return x → return None`, negated `if`
conditions. `tests_relevant` is true when the change added tests and they kill at least half the mutants.

## 13. Command line

| Command | Does |
| --- | --- |
| `agentswarm config CONFIG` | Print the effective configuration. |
| `agentswarm run CONFIG --root DIR [--task TEXT]... [--max-rounds N]` | Add tasks and run until idle; print every artifact's history. |
| `agentswarm status CONFIG --root DIR` | Spend, project-wide escalations, and every artifact's history. Reads only the board: works while the project runs. |
| `agentswarm swarm SWARM [--task PROJECT=TEXT]... [--max-rounds N]` | Run every project of a swarm file concurrently; exit 1 if any project failed (the others still finish). |
| `agentswarm escalations CONFIG --root DIR` | Open escalations with the decisions behind them. |
| `agentswarm resolve CONFIG --root DIR ARTIFACT --retry\|--accept\|--drop [--note TEXT]` | Resolve one. |
| `agentswarm resume CONFIG --root DIR [--note TEXT]` | Lift a drift pause. |
| `agentswarm evaluate SUITE --workdir DIR [--variant V]... [--active P]...` | Run a suite; print calibration (and the audit with `--active`). |
| `agentswarm calibrate DECISIONS` | Calibration report. |
| `agentswarm tune DECISIONS [--precision P] [--min-support N]` | Threshold recommendations. |
| `agentswarm audit DECISIONS [--point POINT]` | Gate error rates against the bounds. |
| `agentswarm label DECISIONS [--limit N]` | Human spot check (answers hidden until you answer). |
| `agentswarm probe EXPERIMENT --workdir DIR` | The atomic benchmark: one prompt and one answer per probe, no tools, scored by plain code (`produce`: hidden tests pass; `critique`: a test that fails on a planted bug and passes on the reference). Every model answers the same probes; resumable. See `examples/goldilocks/probe.yaml`. |
| `agentswarm prompts` | Every model call and Jev question as the model sees it: instructions, a sample payload, tools, answer schema. Its output is [PROMPTS.md](PROMPTS.md). |

A swarm file:

```yaml
scheduler: {max_model_calls: 2, jev_per_minute: 1200, max_sandboxes: 2}
projects:                       # paths relative to this file; names and roots must be distinct
  - {config: greeter/project.yaml, root: ~/agentswarm-runs/swarm-live/greeter}
  - {config: handbook/project.yaml, root: ~/agentswarm-runs/swarm-live/handbook}
```

The CLI loads `.env` from the working directory (never overriding the shell): `TYPESAFE_API_KEY` for Jev,
and any provider keys LangChain reads.

## 14. Environment and tests

- Python 3.13, `uv`. Provider extras: `uv sync --extra ollama` (or `openai`, `anthropic`, `--all-extras`).
- Docker with the sandbox image (section 4). With Colima, project roots must be under `$HOME`.
- `uv run pytest` (≈ 420 tests, offline, about 2½ minutes); `uv run ruff check .`; `uv run pyright` (strict).
- Opt-in live tests: `-m docker` (needs a daemon), `-m live` with `AGENTSWARM_LIVE_MODEL=ollama:gemma4:e4b`,
  `-m jev` with `AGENTSWARM_JEV_MODEL=jev-latest` and the key in `.env`.

## 15. Safety boundaries

- **Sandbox:** commands run on a throwaway copy of the checkout without `.git`, in Docker without network,
  with a read-only root, dropped capabilities and limits; output is capped (start and end kept).
- **Host git:** hooks and the filesystem monitor off, a minimal environment (no provider keys), and each
  worktree pinned to the git directory recorded when it was created.
- **Host files:** every read and write of project files goes through `ConfinedFiles`: relative paths only,
  no `.git`, no links leading outside, bounded reads.
- **Ownership:** one process per project root (`RootLock`); claims carry ids; a worker whose claim was
  superseded has its result discarded (`ClaimRegistry.publish`).
- **Startup:** teams and judges are checked before anything is written; a failed open closes what it
  opened; a root keeps its project name and levels (`RootIdentity`).

## 16. Extending

- **A team:** a class with `name`, `watches`, `pausable`, `relevant(ctx)` and `async act(ctx, worktree) ->
  list[Event]` (never write the board yourself); register a factory in `TeamCatalog`.
- **An analyzer:** subclass `Analyzer`, set `INSTRUCTIONS` and `KIND`.
- **A judge:** a Pydantic model whose fields are `Annotated[NoulAnswer, Ask("..."), Fails(kind, "...")]`;
  register it in `JudgeCatalog`. Gate judges need `Fails` on every question.
- **A check:** any command, in `checks:`.
- **A domain:** a preset YAML in `core/presets/<domain>.yaml`, like `software` and `document`.
- **A model provider:** nothing to code; install its LangChain package and name it in `models:`.
