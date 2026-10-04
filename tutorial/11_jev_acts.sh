#!/usr/bin/env bash
# Step 11: making Jev act. Tune thresholds on step 6's decisions, run the harder suite with the gate
# active, and audit how often it was wrong. Needs Ollama, Docker and the Jev key; takes a while.
here=$(dirname "$0")
source "$here/_common.sh"
needs "$RUNS/step06-eval/decisions.sqlite" tutorial/06_ground_truth.sh
WORK="$RUNS/step11-active"
fresh "$WORK"

run uv run agentswarm tune "$RUNS/step06-eval/decisions.sqlite"
run uv run agentswarm evaluate examples/greeter/evaluation/hard/suite.yaml --workdir "$WORK" --active "gate:*"
run uv run agentswarm audit "$WORK/decisions.sqlite"
