# Conventions for coding agents

The design is in [FRAMEWORK_PROPOSAL.md](FRAMEWORK_PROPOSAL.md). Section numbers (§) in code refer to it.

## Layout

```
src/agentswarm/
  core/        domain model (§7), project configuration and domain presets (§17), clock
  board/       event log, blackboard views, claims, artifact context, spend (§8)
  workspace/   git repositories and worktrees (§9), sandboxes (§13.4), confined file access,
               the root lock and identity
  gating/      hard checks, gates, merge queue, promoters, release trains, joins, level publication (§11)
  judging/     judge specs, threshold policy, deciders, decision log, calibration, tuning, audit (§12, §15)
  jev/         the Jev client and typed question specs
  llm/         chat models and workers: the only place LangChain is imported (§13)
  teams/       the team loop, executors, analyzers, decomposer, document teams, team catalog (§10)
  control/     plain-code controllers: finding router, supervisor, escalations, scheduler, budgets (§14)
  evaluation/  task suites with known answers, evaluation runs, mutation testing
  project.py   wires one project together; swarm.py runs several; cli.py is the command line;
               prompts.py collects every prompt and Jev question into docs/PROMPTS.md
tests/         mirrors src/agentswarm/; shared helpers live in tests/support/
```

New code goes in the subpackage whose responsibility it shares; a new subpackage needs a reason.

## Code structure

- **Classes, not flat functions.** Behaviour lives in methods on classes that own their state and
  collaborators (e.g. `EventLog.append()`, `ClaimRegistry.claim()`, `MergeQueue.enqueue()`).
  Do not write module-level functions; a small pure helper belongs as a `@staticmethod` or
  `@classmethod` on the class it serves.
- **Design the classes first.** Do not write the simplest code that makes a test pass; give each
  concept from the proposal its own class with a clear responsibility.
- **Inject dependencies through constructors.** Swappable parts (deciders, workers, clocks, git) are
  `Protocol`s with a real implementation and a fake.
- **Typed end to end.** Pydantic models for data, `StrEnum` for closed sets. No string parsing,
  no dict indexing with hand-written keys.
- **Tests are grouped in classes**, one `Test<Subject>` per class under test.
- **LangChain and LangGraph stay in `agentswarm/llm/`** (`models.py`, `workers.py`).
  The rest of the code sees only our own interfaces and Pydantic models. Those adapters may relax
  pyright's `reportUnknown*` rules with a file-level comment; nowhere else.
- **Provider agnostic.** Never hard-code a model provider. Models are chosen in the project config as
  `"<provider>:<model>"` and built with `init_chat_model`; provider packages are optional extras.

## Deferred work

Anything deliberately postponed goes in [docs/BACKLOG.md](docs/BACKLOG.md), with why and when it is due.
Check it at the start of each phase; remove an item in the commit that does it.

## Commands

```bash
uv run pytest
uv run ruff format . && uv run ruff check .
uv run pyright
```

All three must pass before committing. Ruff is configured to skip Markdown; never reformat code
samples in the proposal.
