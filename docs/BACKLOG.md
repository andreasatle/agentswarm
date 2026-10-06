# Backlog: deliberately deferred work

Everything we decided *not* to do yet, so it isn't forgotten. Each item says where the decision was made
and when it is due. When an item is done, delete it here in the same commit.

| Item | Why deferred | Due | Decided in |
| --- | --- | --- | --- |
| **Score and Choice answers in gate policies** | Every judge so far is Noul only; thresholds for the others need calibration data. | When a judge needs graded answers | Phase 2 design |
| **Per-item checks and the LLM explainer** (§11.6, sources 3 and 4) | Not needed by the current judges. | When a judge needs them | Phase 2 design |
| **Async Jev calls** | Gates now run in worker threads (Phase 6), so projects overlap; a native async client would only save threads. | If thread count matters | Phase 2 design, Q5 |
| **Faster test suite** | The full run takes ~2 minutes (suite validation and mutation tests start many pytest processes). Mark them `slow` and run them in CI or on demand. | Now due: with CI | Phase 2 step 7 |
| **`in_scope` truth for model-executor runs** | Only scripted runs have a known `in_scope`; model runs get it from spot checks only. A scope rule (allowed paths per task) could add a weak automatic source. | With the next evaluation work | Phase 2 step 6 |
| **Judges on train gates** | Gates above the lowest level promote a prefix of many artifacts on sign-offs, hard checks and people; a judge there would need a question about a batch, not one change. The config refuses it. | When a domain needs judgement above the base level | Phase 3 design |
| **Explicit revert of a merged artifact** (§9) | `resolve --drop` retires an artifact but its merged code stays on the level branches. Reverting it is an explicit, logged action the proposal reserves for a person or the supervisor. | When dropping merged work is needed | Phase 3 step 9 |

Done and removed in the 2026-10-04 cleanup (each in its phase's design doc): relevance filters and the
escalation workflow (Phase 3), promotion beyond the base level (release trains, Phase 3), the LLM fallback,
Jev retries, model tiers, sharper wording and the harder suite (Phase 4), spend budgets (Phase 6).

## Open items (updated 2026-10-05)

- **Phase 8 (in progress): small swarms against bigger models.** Revised after the first live runs and the
  research review: the atomic benchmark (probes) is the measuring instrument, on imported EvalPlus tasks;
  every setup is compared at equal cost against one model making n attempts selected by checks; consistency
  is reported as pass^k. Routing is built but frozen. Next: the complexity experiment (25 inventory tasks in five
  levels; Haiku, gemma4-e4b with 1 and 3 attempts, and AgentSwarm), which decides what is built next. See docs/design/phase-8-goldilocks.md, steps 8-15.
- **Phase 9 (planned, paused until Phase 8 answers): conventions that hold.** One level of configuration: the project's test
  structure is shared and defined once (seed, conventions file, checks); each analyzer's probe tests are its
  own method, in its team config. No toolchain layer, detection, plugins or other languages until a real
  project needs one. Structure and conventions (a
  `pyproject.toml`, the `src/` layout, lint, types, architectural rules) become preset checks, a conventions
  file every model reads, and a judge question in shadow; the seed must pass its checks at creation, and
  `agentswarm new` writes a seed that does. See docs/design/phase-9-conventions.md. Due: right after
  Phase 8's runs, because they use the greeter seed this phase changes.

- **Prove the active gate's false-promotion rate.** eval-3 (in sample) and eval-4 (held out) give 1/41
  false promotions pooled and 0/16 held out. The 95% upper bound gets to 5% only at about 73 known
  promotions without error (about one per task). Add held-out tasks (or real outcomes) until then. Due:
  before relying on the active gate unattended.
- **Phase 10 (planned, paused until Phase 8 answers): robustness for high-level goals.** From docs/RESEARCH.md: several attempts selected
  by checks, red-flag and resample, checks out of the producer's reach (protected acceptance tests, no
  weakening of tests), a clarifier for vague goals, verified decompositions, voting on small known steps,
  judge hygiene, pass^k as the release criterion. Each step is built only if a measurement shows it helps.
  See docs/design/phase-10-robustness.md. Due: after Phase 8's measurements and Phase 9.
- **Phase 11 (planned): project types as data.** A project type becomes one YAML file, still one level:
  levels, checks, gates, judges (questions and what a "no" means, built into Jev specs) and teams. Teams
  are one of four mechanisms kept in code (producer, probing reviewer, quoting reviewer, planner) plus data
  (name, instructions, finding kind, evidence). A project names its preset by name or path, so new types
  need no code, and the fixed list of domains goes. Due: when the user wants a third concrete project type,
  which then drives and validates the format (decided with the user, 2026-10-04, under the scope rule:
  generalize from real cases, not imagined ones). Refinements: presets get sub-labels (`software/python`,
  `software/node`, `document/markdown`), plain files in a folder, and users can add their own. Nesting is
  single inheritance only (`extends:` one parent, chains allowed), with one merge rule (a child's key
  replaces the parent's; teams matched by name; lists never merged item by item), and `agentswarm config`
  shows the resolved result and which file each setting came from. Binary formats such as Word are outputs,
  not working formats: `document/word` would extend `document/markdown` with an export step (e.g. pandoc),
  since the file tools, git diffs and quoting reviewers all need text.
- From the audit, deferred past Phase 7: F08 conflict refresh before retry, timeouts on git commands (the
  rest of F14), fencing for several owners of one project, event schema versioning, indexed board queries,
  CI.
