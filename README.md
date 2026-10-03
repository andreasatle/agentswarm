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

## Measuring the judge (Phase 2)

```bash
# Run the greeter task suite: every task through the model executor and three scripted variants,
# judged by Jev in shadow mode, with automatic ground truth. Needs TYPESAFE_API_KEY in this project's
# .env (git-ignored; the CLI and the tests load it; the Jev model version is set in the project config),
# Docker with the sandbox image, and the model in examples/greeter/project.yaml.
uv run agentswarm evaluate examples/greeter/evaluation/suite.yaml --workdir ~/agentswarm-runs/eval-1

uv run agentswarm calibrate ~/agentswarm-runs/eval-1/decisions.sqlite   # the report again
uv run agentswarm label ~/agentswarm-runs/eval-1/decisions.sqlite        # spot-check ~20 by hand
```
