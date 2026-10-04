#!/usr/bin/env bash
# Step 5: Jev judges the gate, in shadow. The software preset: executor, judged gate, two analyzers,
# release train, human production gate. Needs Ollama, Docker and TYPESAFE_API_KEY in .env.
here=$(dirname "$0")
source "$here/_common.sh"
ROOT="$RUNS/step05-greeter"
fresh "$ROOT"

run uv run agentswarm run examples/greeter/project.yaml --root "$ROOT" \
    --task "Add two functions to greeter.py: farewell(name), which returns 'Goodbye, <name>!', and shout(name), which returns the hello greeting in upper case. Add tests for both."
run uv run agentswarm calibrate "$ROOT/decisions.sqlite"
where "$ROOT"
