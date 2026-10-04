#!/usr/bin/env bash
# Step 8: release trains, human gates, escalations, on step 5's project.
#   tutorial/08_escalations.sh            list what waits for a person
#   tutorial/08_escalations.sh a-0001     accept a-0001 at its gate, then run the project again
source "$(dirname "$0")/_common.sh"
ROOT="$RUNS/step05-greeter"
needs "$ROOT" tutorial/05_shadow.sh

if [ $# -eq 0 ]; then
    run uv run agentswarm escalations examples/greeter/project.yaml --root "$ROOT"
else
    run uv run agentswarm resolve examples/greeter/project.yaml --root "$ROOT" "$1" --accept --note "reviewed"
    run uv run agentswarm run examples/greeter/project.yaml --root "$ROOT"
fi
