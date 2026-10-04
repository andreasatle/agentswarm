#!/usr/bin/env bash
# Step 10: the supervisor. Stalls, repeated rejections, round budgets, drift and the daily budget, offline.
here=$(dirname "$0")
source "$here/_common.sh"

run uv run pytest -v tests/control/test_supervisor.py
