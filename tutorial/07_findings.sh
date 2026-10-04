#!/usr/bin/env bash
# Step 7: analyzers, findings and the router, as they happened in step 5's project: sign-offs, findings
# (each backed by a failing test) and any fix artifacts the router created.
source "$(dirname "$0")/_common.sh"
needs "$RUNS/step05-greeter" tutorial/05_shadow.sh

run uv run agentswarm status examples/greeter/project.yaml --root "$RUNS/step05-greeter"
