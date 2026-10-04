# Phase 9: Conventions that hold

Status: **planned**, to start after the Phase 8 runs finish. The greeter seed is used by the running
experiment, and changing it midway would change its conditions.

## The problem

A project's structure and conventions decide whether it stays workable as it grows: a `pyproject.toml`, code
under `src/<package>/`, tests under `tests/`, lint and type rules, and architectural rules like this
repository's "classes, not flat functions". The user's previous project (forge-rs) failed when these drifted.
Today AgentSwarm doesn't enforce any of them:

- **The greeter seed** is a bare `greeter.py` and `tests/`: no `pyproject.toml`, no `src/` layout.
- **The software preset's only check is pytest.** Since tests import from the top-level folder, code in the
  wrong place passes as long as the tests do.
- **The executor is told no conventions,** unless a project writes them as constraints.

## The scope rule (applies to this phase and every later one)

A new mechanism must answer: *which measured problem, or which concrete project, needs this now?*
"It might be useful later" goes to the backlog with the condition that would make it due. Generalize only
what the existing concrete cases share; prefer explicit configuration to detection; prefer data to code.
This is decided with the user, who asked that proposals, theirs included, be held to it.

## What a project assumes today

The engine assumes only files in git and checks that are commands. The presets assume much more:
`software` is Python in five places (the sandbox image, pytest as the check, the analyzers' pytest probes,
mutation testing through Python's parser, the task suites), and `document` is Markdown, lightly (the
project's own checker and a mention of sections in the writer's instructions).

## The principle

**Anything that must stay true is a check, not a hope.** Models drift, constraints are requests and judges
are probabilistic, but a hard check that fails stops the work every time. And because the merge queue
reruns the checks on the merged result, a level branch can never contain a violation.

Conventions are enforced in three layers, each with its own job:

| Layer | Job | Strength |
| --- | --- | --- |
| Hard checks | enforce everything that can be checked mechanically | a guarantee: work that fails doesn't move up |
| Conventions file | tell the models the rules, including the ones no command can check | guidance the model sees on every task |
| A judge question | catch the rest (architectural style), measured in shadow before it acts | calibrated, like every other decision point |

## Design

### 0. One level of configuration: shared structure in the project, methods in the teams

Two kinds of tests need opposite treatment:

- **The project's own tests** (`tests/`, written by the executor, run by the gates) are a shared contract:
  where tests live, how they're named and run. Every team depends on it and the gates enforce it, so it is
  defined once, by the project: its seed (`pyproject.toml`, `tests/`), its conventions file (section 3)
  and the checks below.
- **A reviewer's probe tests** (the throwaway tests an analyzer writes in `_analysis/` to demonstrate a
  bug, never committed) are the team's own method. No other team sees them, so how they are written and
  run belongs in the team's definition: `probe: {folder: _analysis, pattern: "test_*.py", check:
  unit_tests}`, with those values as defaults for the software analyzers.

So methods are decentralized and shared structure is not: teams coordinate only through the board and the
repository, and the repository's conventions are the interface between them.

This keeps configuration at one level, the preset plus the project's own config. A separate toolchain layer
was considered and dropped: it would bring back the two levels of YAML (domain and language) that the
user's earlier attempt struggled with, and nothing needs it now. `software` is honestly Python software;
a real project in another language would get its own preset (for example `software-node`), still one
level. Not built (backlog, with triggers): automatic detection of a project's language, presets for other
languages (due when a real project in that language is wanted), and any plugin mechanism.

### 1. Mechanical conventions become the software preset's checks

Every software project gets these by default, each a separate check, so a failure names exactly what's wrong:

| Check | Command (in the sandbox) | Catches |
| --- | --- | --- |
| `layout` | `python -m agentswarm_checks.layout` | Python files outside `src/<package>/` and `tests/`; a missing `pyproject.toml`, `src/<package>/__init__.py` or `tests/` |
| `package` | build and install the project offline, then import it | a broken `pyproject.toml`, code that only works from the top-level folder |
| `lint` | `ruff check` and `ruff format --check` | style and simple bugs; rules come from the project's own `pyproject.toml` |
| `types` | `basedpyright` | type errors; strictness from the project's own `pyproject.toml` |
| `unit_tests` | `pytest`, as now | behaviour |

Projects keep control: the rules for ruff and pyright live in the project's own `pyproject.toml`, and a
project can drop or replace any check in its config (presets are defaults).

### 2. The sandbox image carries the toolchain

The image gets ruff, basedpyright (a pyright build that bundles its own Node, so it runs without network),
hatchling (to build packages offline) and `agentswarm_checks`: a small package from this repository with
the checks that are scripts (`layout` first). Checks still run without network. A project that needs
third-party dependencies needs them available offline (a wheel folder mounted read-only), which is a later
step. Until then, software projects are standard-library-only, as the greeter already is.

### 3. A project's conventions file reaches every model

If the seed has a `CONVENTIONS.md` (or an `AGENTS.md`, the same idea), its text is added to every
executor's and reviewer's instructions as the project's rules. That's where rules no command can check
live: "behaviour lives in classes", "no module-level functions", "one concept per class". A model reading
the rules on every task drifts far less than one guessing them from examples.

### 4. Architectural style gets a judge question, in shadow first

A `follows_conventions` question at the first gate ("does the change follow every rule in the project's
conventions file?") covers what checks can't. Like every new decision point it starts in shadow: it's
logged and measured (truth from human spot checks, since no automatic source exists for style), and only
goes active if it proves reliable.

### 5. The base level must pass its own checks from the start

At project creation, `Project.open` runs the hard checks of the first gate on the seed's first commit and
refuses to start if they fail. Otherwise every artifact would be rejected for problems it didn't cause,
and no model can fix a convention violation it wasn't asked about. A project with an existing codebase
that doesn't pass yet gets a clear message: fix the seed first, or start with fewer checks and add them as
the code is brought into line.

### 6. Starting right: `agentswarm new`

`agentswarm new software NAME --dir PATH` writes a seed that passes all the checks from the first commit:
`pyproject.toml` (with ruff, basedpyright and pytest settings), `src/NAME/__init__.py`, `tests/`, a
`CONVENTIONS.md` with sensible defaults, and a `project.yaml` on the software preset. Documents get
`agentswarm new document`. Writing a project from scratch then starts with its conventions in force.

### 7. The greeter moves to the new layout

The greeter seed gets `pyproject.toml` and `src/greeter/`, and the evaluation suites move with it: the
references become `src/greeter/__init__.py`, and pytest finds `src/` through `pythonpath` in
`pyproject.toml`. The hidden tests keep `from greeter import ...` unchanged. Because the task conditions
change, the moved suites are new suite versions: their decisions form new cohorts and are never pooled
with Phase 4 and Phase 8 data.

## Steps

1. Software settings out of shared code (found by an audit, 2026-10-04): `probe:` in the team config (the
   analyzers' probe folder, file pattern and check move out of `AnalysisToolbox`); the default sandbox
   image moves from `SandboxConfig` into each preset (today every document project silently gets the
   Python image); `PYTHONDONTWRITEBYTECODE` moves from both sandboxes into the software preset's sandbox
   environment.
2. `agentswarm_checks` with the `layout` check; the sandbox image with ruff, basedpyright and hatchling.
3. The software preset's checks (`layout`, `package`, `lint`, `types`, `unit_tests`) and its default
   constraints for the executor.
4. The seed check at project creation.
5. The conventions file in every executor's and reviewer's instructions; the `follows_conventions`
   question in shadow.
6. `agentswarm new` for software and documents.
7. The greeter and its suites on the new layout; the tutorial and examples updated.

## Not in this phase

- Third-party dependencies inside the sandbox (an offline wheel folder); standard-library projects first.
- Other languages, automatic detection, plugins: see section 0.

## Decisions made during implementation
