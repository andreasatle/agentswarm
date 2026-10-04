#!/usr/bin/env bash
# Step 12: a second domain. The handbook goes draft -> reviewed -> final, then waits for a person.
# Needs Ollama, Docker and the Jev key. Then: tutorial/12_publish.sh
here=$(dirname "$0")
source "$here/_common.sh"
ROOT="$RUNS/step12-handbook"
fresh "$ROOT"

run uv run agentswarm run examples/handbook/project.yaml --root "$ROOT" \
    --task "Write a short handbook for running a book club: choosing books, running a meeting, keeping members"
run git -C "$ROOT/repo" ls-tree -r --name-only final
where "$ROOT"
