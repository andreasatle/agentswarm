#!/usr/bin/env bash
# Step 3: levels are branches. A first run with a fake executor: no model, no Jev, no Docker.
here=$(dirname "$0")
source "$here/_common.sh"
ROOT="$RUNS/step03-quickstart"
fresh "$ROOT"

run uv run agentswarm run examples/quickstart/project.yaml --root "$ROOT" \
    --task "Write a note" --task "Write another note"
run git -C "$ROOT/repo" log --oneline --graph --all
