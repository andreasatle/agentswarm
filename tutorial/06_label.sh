#!/usr/bin/env bash
# Step 6, by hand: spot-check automatic outcomes. Interactive: answer y/n/s(kip)/q(uit).
source "$(dirname "$0")/_common.sh"
needs "$RUNS/step06-eval/decisions.sqlite" tutorial/06_ground_truth.sh

run uv run agentswarm label "$RUNS/step06-eval/decisions.sqlite" --limit "${1:-20}"
