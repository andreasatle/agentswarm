#!/usr/bin/env bash
# Step 15: does the swarm help? Measuring before optimizing.
#   tutorial/15_measure.sh        offline: the prompt catalogue, and the inventory suite's ground truth checked
#   tutorial/15_measure.sh live   the complexity experiment on two tasks, every setup (needs Ollama, Docker,
#                                 Jev and ANTHROPIC_API_KEY in .env; about an hour, a few cents for Haiku)
here=$(dirname "$0")
source "$here/_common.sh"

if [ "${1:-}" = live ]; then
    ROOT="$RUNS/step15-complexity"
    fresh "$ROOT"
    run uv run agentswarm compare examples/goldilocks/complexity-smoke.yaml --workdir "$ROOT"
else
    run uv run agentswarm prompts | head -60
    run uv run pytest -q tests/examples/test_inventory_suite.py
fi
