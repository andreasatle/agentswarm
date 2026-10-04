#!/usr/bin/env bash
# Step 9: decomposition and joins. The whole flow (a goal split in two, children by train, the umbrella by
# join, up to candidate) runs offline in this test, with scripted models and judges.
source "$(dirname "$0")/_common.sh"

run uv run pytest -v tests/test_project.py -k Decomposition
run uv run pytest -v tests/teams/test_decomposer.py
