#!/usr/bin/env bash
# Step 10: the supervisor. Stalls, repeated rejections, round budgets, drift and the daily budget, offline.
source "$(dirname "$0")/_common.sh"

run uv run pytest -v tests/control/test_supervisor.py
