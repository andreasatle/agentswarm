#!/usr/bin/env bash
# Step 12, the person's part: approve publishing, then run again so the handbook reaches published.
here=$(dirname "$0")
source "$here/_common.sh"
ROOT="$RUNS/step12-handbook"
needs "$ROOT" tutorial/12_documents.sh

run uv run agentswarm resolve examples/handbook/project.yaml a-0001 --root "$ROOT" --accept --note "reviewed, publish"
run uv run agentswarm run examples/handbook/project.yaml --root "$ROOT"
run cat "$ROOT/levels/published/sections/01-choosing-books.md"  # a plain folder per level
where "$ROOT"
