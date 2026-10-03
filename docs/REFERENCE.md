# AgentSwarm Reference

Everything in one place: concepts, files, configuration, events, teams, gates, judges, the decision log,
evaluation and the command line. For a guided walk through the system, read [TUTORIAL.md](TUTORIAL.md)
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
| 4 | Active per decision point, retries and fallback, tuning, gate audit, model tiers, harder suite | final step (runs) in progress |
| 5 | Documents as a second domain | designed ([phase-5](design/phase-5-documents.md)); handbook checker done |
| 6 | Several projects, shared scheduler, budgets | designed ([phase-6](design/phase-6-multi-project.md)) |

## 1. Layout

```
src/agentswarm/
  core/        domain.py (artifacts, findings, events), config.py (ProjectConfig), clock.py, presets/
  board/       eventlog.py, blackboard.py (BoardViews), claims.py, context.py, artifacts.py
  workspace/   git.py (Repository, Worktree), sandbox.py (DockerSandbox, LocalSandbox)
  gating/      checks.py, gate.py, merge_queue.py, promotion.py (Promoter), train.py, join.py
  judging/     judges.py, policy.py, judgement.py, deciders.py, decisions.py,
               calibration.py, tuning.py, audit.py, labelling.py
  jev/         api.py (wire format), spec.py (Ask, QuestionSet), client.py (JevClient)
  llm/         models.py (ChatModels), workers.py (Worker, LangChainWorker): the only LangChain imports
  teams/       runner.py (Team, TeamRunner), executor.py, analyzer.py, relevance.py, decomposer.py, catalog.py
  control/     router.py (FindingRouter), supervisor.py, escalations.py
  evaluation/  suite.py, run.py, faults.py (variants), mutation.py
  project.py   Project, ProjectServices: one project wired together
  cli.py       the `agentswarm` command
tests/         mirrors src/agentswarm/; shared helpers in tests/support/
examples/      greeter (software), greeter/evaluation (task suites), handbook (documents, Phase 5)
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
| **Escalation** | A question for a person; resolved by retry, accept or drop. |

## 3. A project on disk

Under the `--root` directory (it must be mountable by Docker, i.e. under `$HOME` with Colima/Docker Desktop):

| Path | What |
| --- | --- |
| `blackboard.sqlite` | The event log (table `events`: seq, at, kind, payload JSON). |
| `decisions.sqlite` | The decision log (tables `decisions`, `outcomes`). |
| `repo/` | The git repository; the main checkout stays on a detached HEAD. |
| `worktrees/<artifact>` | A team's checkout of an artifact branch, removed after it acts. |
| `worktrees/_gate-*`, `_level-*`, `_train-*`, `_join-*`, `_hidden-*`, `_mutation-*` | Throwaway checkouts used by gates, the merge queue, trains, joins and evaluation. |

Branches: one per level above the lowest (`experimental`, `candidate`, ...), plus `raw/<id>-<slug>` per
artifact. Analyzers' scratch tests live in `_analysis/` inside a detached checkout and are never committed.

## 4. Configuration

One YAML file per project, validated into `ProjectConfig` when loaded. Unknown keys are errors.
`agentswarm config FILE` prints the effective configuration (with the domain preset applied).

### Top level

| Key | Type | Default | Notes |
| --- | --- | --- | --- |
| `project` | str | required | Name; also the event log's project id. |
| `domain` | `software` \| `document` | required | Selects the preset (only `software` has one so far). |
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
the lowest level can't require sign-offs; judges above the lowest level are refused (Phase 4 backlog);
`jev.fallback` names a defined model; a team sets `model` or `model_tiers`, not both.

### `models.<name>`

| Key | Default | Notes |
| --- | --- | --- |
| `model` | required | `"<provider>:<model>"`, e.g. `ollama:gemma4:e4b`, `openai:gpt-5`, `anthropic:claude-sonnet-4-6`. Built with LangChain's `init_chat_model`; install the provider's extra (`uv sync --extra ollama`). The model must make real tool calls. |
| `temperature`, `max_tokens`, `base_url`, `timeout_s` | none | Passed only when set. |

### `checks.<name>`

| Key | Default | Notes |
| --- | --- | --- |
| `command` | required | Run in the sandbox with the checkout as working directory; exit 0 passes. |
| `timeout_s` | 600 | |
| `kind` | `test_failure` | The finding kind a failure produces. |

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
| `budgets.usd_per_day` | none | Not enforced yet (Phase 6). |
| `supervisor.stall_after_minutes` | 30 | No event on an artifact for this long escalates. |
| `supervisor.max_rejections` | 3 | Rejections in a row since the last promotion. |
| `supervisor.drift_every` | 5 | Ask about drift after every N merged changes. |
| `jev.model` | `jev-latest` | Pin a version (`jev-1.13.0`) once thresholds are tuned. |
| `jev.mode` | `shadow` | The default for decision points `active` doesn't name. |
| `jev.active` | `[]` | Glob patterns of decision points that act, e.g. `gate:*`, `relevance:red_team`. |
| `jev.fallback` | none | A model that answers when Jev can't (marked `llm:`, uncalibrated). |
| `jev.timeout_s` | 30 | |

### Presets

`domain: software` loads `src/agentswarm/core/presets/software.yaml`: levels raw → experimental →
candidate → production; a `unit_tests` check (pytest); the executor, integration tester and red team (all
on `models.coder`, which the project must define); a judged raw gate; a release train to candidate needing
both analyzers' sign-offs; a human production gate. A project's own top-level keys replace the preset's
whole (listing `teams` replaces all preset teams).

## 5. Events

Every state change is one of these, appended to the log. "Change" marks events that change the
artifact's version (`BoardViews.version`), which is what teams key on to act once per change.

| Event | Fields | Effect on the views | Change |
| --- | --- | --- | --- |
| `ArtifactCreated` | artifact | New artifact at the lowest level. | yes |
| `Claimed` | claim (artifact, team, expires_at) | Lease held until released or expired. | |
| `Released` | artifact, team | Ends the team's own claim only. | |
| `CommitPushed` | artifact, commit, model | Latest commit; becomes the unjudged commit; clears an approval. | yes |
| `FindingAdded` | finding (id, artifact, level, team, kind, severity, detail) | Open finding. | |
| `FindingResolved` | artifact, finding, resolved_by, reason | Closes the finding. | |
| `SignedOff` | artifact, team, version | Sign-off for that version. | |
| `Sized` | artifact, fits, children, decision | `fits=false` makes an umbrella. | yes |
| `Paused` / `Resumed` | reason | Project-wide pause of teams that start work. | |
| `Promoted` | artifact, from_level, to_level, gate_decision, commit | New level; closes findings of the level left; clears approval; first one records the merge commit. | yes |
| `Rejected` | artifact, level, findings, gate_decision | Open findings replaced by the gate's; commit judged. | yes |
| `Escalated` | artifact, reason | Open escalation; commit judged. | |
| `EscalationResolved` | artifact, resolution (`retry`/`accept`/`drop`), note | Clears the escalation; accept re-queues the last commit, approved; drop retires the artifact. | retry only |

Finding kinds: `test_failure`, `build_failure`, `out_of_scope`, `incomplete`, `security`, `inconsistent`,
`too_large`, `edge_case`. Severities: `info`, `minor`, `major`, `blocker` (major and blocker are
"blocking": they hold trains and get routed).

## 6. Views (`BoardViews`)

`artifact(id)`, `artifacts()`, `artifacts_at(level)` (live ones), `open_findings(id)`, `claim_on(id, now)`,
`active_claims(now)`, `history(id)`, `last_commit(id)`, `unjudged_commit(id)`, `version(id)`,
`merge_commit(id)`, `signed_off(id, team)`, `signed_off_by(id)`, `is_umbrella(id)`, `children(id)`,
`depth(id)`, `fix_for(finding)`, `last_activity(id)`, `open_escalation(id)`, `open_escalations()`,
`accepted(id)`, `dropped(id)`, `paused`. Folding the same log always gives equal views (`Blackboard.rebuild`).

## 7. Teams

| `use` | Watches | Does | Notes |
| --- | --- | --- | --- |
| `executor` | lowest | A model edits the artifact's branch with a toolbox (list/read/write/delete files, run checks), commits. | Model tiers; waits for sizing when the project has a decomposer; commits and escalates failing or incomplete work. |
| `fake_executor` | lowest | Writes the task to a file. | No model; for tests and dry runs. |
| `integration_tester` | above lowest | A model writes and runs tests through real call paths; reports problems. | Analyzer; finding kind `test_failure`. |
| `red_team` | above lowest | A model tries to break the change with hostile input. | Analyzer; finding kind `edge_case`. |
| `decomposer` | lowest | Asks Jev if a task fits one branch; if clearly not, a model splits it; Jev checks the split. | Children `a-0001.1`; umbrella joins later. Splits only when `size:*` / `split:*` are active. |

Analyzers examine a merged artifact in a detached checkout of its merge commit, ask their relevance judge
first (if configured), report only problems backed by a test they ran that fails (the evidence filter),
and end with a sign-off. Pausable teams (executor, decomposer) wait while the project is paused; analyzers
don't.

## 8. Gates and promotion

| Loop | Serves | Rule |
| --- | --- | --- |
| `Promoter` | the gate out of the lowest level | For each artifact with an unjudged commit: claim it, run hard checks on that exact commit (detached), ask the judge, apply the policy. Promote goes through the merge queue: merge onto the level, rerun the checks on the result, keep or undo. |
| `TrainPromoter` | gates above | Longest ready prefix of the lower level's first-parent line (signed off, no blocking findings, unclaimed; dropped artifacts pass); hard checks on its last commit (the first failing commit gets a blocker); compare-and-swap fast-forward. Human gates escalate once and promote up to the last accepted artifact. |
| `JoinPromoter` | umbrellas | When every child reached the next level: that level's hard checks on its tip, then `JoinCheck`; else escalate once. |

A person's `accept` replaces judgement (Jev, `requires_human`, a failed join) for one gate, never the hard
checks.

## 9. Judges and decision points

| Judge | Questions (yes is good) | Used at |
| --- | --- | --- |
| `ChangeReadiness` (alias `RawToExperimental`) | `in_scope`, `tests_relevant`, `complete` (each with a finding kind) | `gate:<lowest>-><base>` |
| `IntegrationRelevance` | `relevant` | `relevance:integration_tester` |
| `AdversarialRelevance` | `relevant` | `relevance:red_team` |
| `SizeCheck` | `fits` | `size:<lowest>` |
| `SplitCheck` | `smaller` (sees the proposed children) | `split:<lowest>` |
| `JoinCheck` | `accomplished` | `join:<level>-><next>` |
| `DriftCheck` | `on_course` | `drift` |

**Policy (§11.2):** per question, `p ≥ pass` passes, `p ≤ fail` fails, otherwise unsure; any fail rejects
(one finding per failed question with `Fails`), else any unsure escalates, else promote.
**Modes:** each point is shadow (logged, the system acts as if there were no judge) or active, resolved by
`jev.active` patterns. **Versions:** a judge's version is a hash of its questions' wording; decisions record
it, and reports never mix versions.

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
| Calibration | `calibrate` | Per point, judge version, question: 10 bins, ECE, Brier, coverage and accuracy at thresholds, sources. |
| Tuning | `tune` | Lowest pass / highest fail threshold reaching a target precision (Wilson bounds); YAML per gate; readiness. |
| Gate audit | `audit` | Active gate decisions: false promotions (≤ 5%), false rejections (≤ 10%), escalations (≤ 30%), judged by the 95% Wilson upper bound. |

## 11. Controllers

- **FindingRouter:** open major/blocker findings above the lowest level become one fix artifact each
  (`fixes` = the finding, parent = the artifact); a finding is resolved when its fix reaches its level.
- **Supervisor:** escalates stalls (time since an artifact's last event), repeated rejections and rounds
  over budget, once per state; asks `DriftCheck` after every N merged changes and pauses on a clear "no"
  when `drift` is active.
- **Escalations:** `agentswarm escalations / resolve / resume`; needs only the board and decision log.

## 12. Evaluation

A suite (`examples/greeter/evaluation/suite.yaml`, `hard/suite.yaml`) names a project config, a judge and a
test command, and lists tasks with hidden tests and reference solutions. `EvaluationRun` runs every task in
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
| `agentswarm status CONFIG --root DIR` | Print every artifact's history. |
| `agentswarm escalations CONFIG --root DIR` | Open escalations with the decisions behind them. |
| `agentswarm resolve CONFIG --root DIR ARTIFACT --retry\|--accept\|--drop [--note TEXT]` | Resolve one. |
| `agentswarm resume CONFIG --root DIR [--note TEXT]` | Lift a drift pause. |
| `agentswarm evaluate SUITE --workdir DIR [--variant V]... [--active P]...` | Run a suite; print calibration (and the audit with `--active`). |
| `agentswarm calibrate DECISIONS` | Calibration report. |
| `agentswarm tune DECISIONS [--precision P] [--min-support N]` | Threshold recommendations. |
| `agentswarm audit DECISIONS [--point POINT]` | Gate error rates against the bounds. |
| `agentswarm label DECISIONS [--limit N]` | Human spot check (answers hidden until you answer). |

The CLI loads `.env` from the working directory (never overriding the shell): `TYPESAFE_API_KEY` for Jev,
and any provider keys LangChain reads.

## 14. Environment and tests

- Python 3.13, `uv`. Provider extras: `uv sync --extra ollama` (or `openai`, `anthropic`, `--all-extras`).
- Docker with the sandbox image (section 4). With Colima, project roots must be under `$HOME`.
- `uv run pytest` (≈ 330 tests, offline, about 2 minutes); `uv run ruff check .`; `uv run pyright` (strict).
- Opt-in live tests: `-m docker` (needs a daemon), `-m live` with `AGENTSWARM_LIVE_MODEL=ollama:gemma4:e4b`,
  `-m jev` with `AGENTSWARM_JEV_MODEL=jev-latest` and the key in `.env`.

## 15. Extending

- **A team:** a class with `name`, `watches`, `pausable`, `relevant(ctx)` and `async act(ctx, worktree) ->
  list[Event]` (never write the board yourself); register a factory in `TeamCatalog`.
- **An analyzer:** subclass `Analyzer`, set `INSTRUCTIONS` and `KIND`.
- **A judge:** a Pydantic model whose fields are `Annotated[NoulAnswer, Ask("..."), Fails(kind, "...")]`;
  register it in `JudgeCatalog`. Gate judges need `Fails` on every question.
- **A check:** any command, in `checks:`.
- **A domain:** a preset YAML in `core/presets/<domain>.yaml` (Phase 5 adds `document`).
- **A model provider:** nothing to code; install its LangChain package and name it in `models:`.
