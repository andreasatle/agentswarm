# AgentSwarm

Independent teams (executors, testers, reviewers, …) watch a shared per-project blackboard and move
work products — git branches — up maturity levels through gates. LLMs produce, Jev decides,
deterministic code controls. See [FRAMEWORK_PROPOSAL.md](FRAMEWORK_PROPOSAL.md) for the design.

## Development

```bash
uv sync
uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run pyright
```
