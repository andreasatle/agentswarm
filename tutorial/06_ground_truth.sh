#!/usr/bin/env bash
# Step 6: ground truth without labelling. Runs the greeter suite (14 tasks x 4 variants) with Jev in
# shadow, then prints the calibration report. Needs Ollama, Docker and the Jev key; takes a while.
source "$(dirname "$0")/_common.sh"
WORK="$RUNS/step06-eval"
fresh "$WORK"

run uv run agentswarm evaluate examples/greeter/evaluation/suite.yaml --workdir "$WORK"
