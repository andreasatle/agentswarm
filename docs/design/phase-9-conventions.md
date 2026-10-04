# Phase 9: Toolchains and conventions that hold

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

### 0. A toolchain is a data file, named explicitly

The two concrete cases, Python software and Markdown documents, get a toolchain file each:
`core/toolchains/python.yaml` and `core/toolchains/markdown.yaml`. A project names its own:

```yaml
domain: software        # the workflow shape: levels, teams, gates (the preset)
toolchain: python       # the language: sandbox image, checks, probe tests, instructions
```

A toolchain holds what is now hard-wired or scattered: the sandbox image, the convention checks (below),
how analyzers write and run probe tests (folder, file-name pattern, which check runs them), whether
mutation testing applies, and a few lines of language-specific instructions for the models. A project
overrides any part of it, as it overrides a preset. The domain's preset names a default toolchain, so
existing configs keep working.

Deliberately **not** built (backlog, with triggers): automatic detection from the seed's files (it saves
one line of config but hides a decision; `agentswarm new` writes the line instead), toolchains for other
languages (due when a real project in that language is wanted), and any plugin mechanism (a toolchain is
data; a new language is a file and a sandbox image).

### 1. Mechanical conventions become the Python toolchain's checks

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

1. Toolchains as data: `python.yaml` and `markdown.yaml`, `toolchain:` in the project config, the
   preset's default toolchain, and the analyzers' probe settings moved out of code into the toolchain.
2. `agentswarm_checks` with the `layout` check; the sandbox image with ruff, basedpyright and hatchling.
3. The Python toolchain's checks (`layout`, `package`, `lint`, `types`, `unit_tests`) and its default
   constraints for the executor.
4. The seed check at project creation.
5. The conventions file in every executor's and reviewer's instructions; the `follows_conventions`
   question in shadow.
6. `agentswarm new` for software and documents, writing the `toolchain:` line.
7. The greeter and its suites on the new layout; the tutorial and examples updated.

## Not in this phase

- Third-party dependencies inside the sandbox (an offline wheel folder); standard-library projects first.
- Languages other than Python and Markdown, automatic detection, plugins: see section 0.

## Decisions made during implementation
