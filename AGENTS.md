# Conventions for coding agents

The design is in [FRAMEWORK_PROPOSAL.md](FRAMEWORK_PROPOSAL.md). Section numbers (§) in code refer to it.

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
- **LangChain and LangGraph stay in adapter modules** (`models.py`, `workers.py`, the executor's toolbox).
  The rest of the code sees only our own interfaces and Pydantic models. Those adapters may relax
  pyright's `reportUnknown*` rules with a file-level comment; nowhere else.
- **Provider agnostic.** Never hard-code a model provider. Models are chosen in the project config as
  `"<provider>:<model>"` and built with `init_chat_model`; provider packages are optional extras.

## Commands

```bash
uv run pytest
uv run ruff format . && uv run ruff check .
uv run pyright
```

All three must pass before committing. Ruff is configured to skip Markdown; never reformat code
samples in the proposal.
