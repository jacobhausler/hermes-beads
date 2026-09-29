#!/usr/bin/env bash
# scripts/run-scenarios.sh — hbl-pnu.2.8 scenario acceptance runner.
# Runs every scenario lane (tests/test_scenarios_*.mjs) under node --test.
# Each lane owns its own seeded store (make_store.py) and its own negative
# control; the negative-control harness lane additionally re-spawns itself
# with SCENARIO_NEGATIVE=1 and asserts the broken scenario FAILS.
# Exit code is the node --test verdict (0 = all scenarios green).
set -euo pipefail
cd "$(dirname "$0")/.."   # repo root (the lane worktree)
mapfile -t files < <(ls tests/test_scenarios_*.mjs 2>/dev/null || true)
if [ "${#files[@]}" -eq 0 ]; then
  echo "run-scenarios: no tests/test_scenarios_*.mjs found" >&2
  exit 1
fi
exec node --test "${files[@]}"
