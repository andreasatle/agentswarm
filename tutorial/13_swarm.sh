#!/usr/bin/env bash
# Step 13: several projects at once, sharing one scheduler. The quickstart swarm needs nothing else.
# Its projects live where examples/swarm-quickstart.yaml says (~/agentswarm-runs/swarm/).
#   tutorial/13_swarm.sh          the quickstart swarm
#   tutorial/13_swarm.sh live     the greeter and the handbook (needs Ollama, Docker, the Jev key)
here=$(dirname "$0")
source "$here/_common.sh"

if [ "${1:-}" = live ]; then
    run uv run agentswarm swarm examples/swarm-live.yaml \
        --task "greeter=Add a function farewell(name) to greeter.py that returns 'Goodbye, <name>!'. Add tests." \
        --task "handbook=Write a short handbook for running a book club: choosing books, running a meeting, keeping members"
else
    run uv run agentswarm swarm examples/swarm-quickstart.yaml --task "notes-a=Write a note" --task "notes-b=Write a note"
fi
