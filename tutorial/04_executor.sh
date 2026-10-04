#!/usr/bin/env bash
# Step 4: a real executor in a sandbox. Needs Ollama (ollama pull gemma4:e4b) and Docker with the
# sandbox image (docker build -t agentswarm/sandbox-python:3.13 -f docker/sandbox-python.Dockerfile docker).
source "$(dirname "$0")/_common.sh"
ROOT="$RUNS/step04-greeter"
fresh "$ROOT"

run uv run agentswarm run examples/greeter/evaluation/project.yaml --root "$ROOT" \
    --task "Add a function farewell(name) to greeter.py that returns 'Goodbye, <name>!', with tests."
run git -C "$ROOT/repo" show experimental:greeter.py
