#!/usr/bin/env bash
# verify-tier1.sh — prove the Tier 1 install story on vanilla hermes.
#
# Tier 1 = `hermes plugins enable hermes-beads` + an operator-configured bd
# (PATH or HERMES_BEADS_BD_BIN): the six beads_* tools mount, and the desktop
# pane is ABSENT — degrading honestly, with no false 'loaded automatically'
# tell anywhere in the shipped docs.
#
# What it does, in a throwaway HERMES_HOME created via mktemp under $TMPDIR
# (NEVER the repo, NEVER your real home):
#   1. stage the shipped tree (plugin.yaml, __init__.py, beads/, desktop/,
#      README.md, docs/ — the test_packaging shipped set);
#   2. `plugins enable` through the stock CLI (python -m hermes_cli.main),
#      then the real SDK discovery path — assert the plugin and ALL SIX
#      beads_* tools mount;
#   3. assert no pane entry point shipped (no desktop/plugin.js);
#   4. grep the staged shipped docs for the false tell (case-insensitive
#      'loaded automatically' / 'automatically loaded'); zero hits required;
#   5. print one TIER1_RECEIPT json line (home, plugins, tools, tell hits),
#      then delete the scratch home and propagate every gate's exit code.
#
# Run from anywhere: python3 tests/test_install_tiers.py wires this into CI
# gate 2; standalone: scripts/verify-tier1.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LANE="$(dirname "$SCRIPT_DIR")"
NAME="hermes-beads"
TOOLS=(beads_smoke beads_frontier beads_show beads_claim beads_update beads_comment)
# The CI/seat-pinned core venv (same resolution as tests/test_packaging.py).
VENV_PY="${HERMES_VENV_PY:-/opt/hermes/.venv/bin/python}"
[ -x "$VENV_PY" ] || VENV_PY="$(command -v python3)"

fail() { echo "verify-tier1: $*" >&2; exit 1; }

# --- 0. shipped-tree preflight (read-only, on the repo copy) ---------------
[ -f "$LANE/plugin.yaml" ] || fail "not a plugin lane (no plugin.yaml at $LANE)"
[ ! -e "$LANE/desktop/plugin.js" ] \
  || fail "desktop/plugin.js exists — Tier 1's honest 'no pane entry point' claim would be false; this slice is docs-only until the real entry point merges"

# --- 1. throwaway home under $TMPDIR (spec: tempfile, NEVER repo root) ------
command -v mktemp >/dev/null || fail "mktemp required"
HOME_SCRATCH="$(mktemp -d "${TMPDIR:-/tmp}/hermes-beads-tier1-XXXXXX")"
cleanup() { rm -rf "$HOME_SCRATCH"; }
trap cleanup EXIT
case "$HOME_SCRATCH" in
  "$LANE"*|"$SCRIPT_DIR"*) fail "scratch home leaked into the repo: $HOME_SCRATCH" ;;
esac
[ -d "$HOME_SCRATCH" ] && [ ! -e "$HOME_SCRATCH/.git" ] \
  || fail "scratch home is not a clean temp dir"

# --- 2. stage the shipped tree (test_packaging's shipped set) ---------------
STAGE="$HOME_SCRATCH/plugins/$NAME"
mkdir -p "$STAGE"
for item in plugin.yaml __init__.py beads desktop README.md docs; do
  [ -e "$LANE/$item" ] || fail "shipped tree incomplete: $item missing from $LANE"
  cp -R "$LANE/$item" "$STAGE/"
done
find "$STAGE" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true

# --- 3. enable + mount through the stock paths ------------------------------
export HERMES_HOME="$HOME_SCRATCH"
"$VENV_PY" -m hermes_cli.main plugins enable "$NAME" --no-allow-tool-override \
  >/dev/null 2>&1 || fail "hermes plugins enable $NAME failed"

# Real SDK discovery — the same path the backend uses to mount tools.
probe_out="$(
  "$VENV_PY" - <<'PY' 2>/dev/null
import json
from hermes_cli.plugins import discover_plugins, get_plugin_manager
discover_plugins(force=True)
m = get_plugin_manager()
print(json.dumps({
    "plugins": sorted(getattr(m, "_plugins", {}) or {}),
    "tools": sorted(getattr(m, "_plugin_tool_names", set()) or []),
}))
PY
)" || fail "plugin discovery probe crashed"
[ -n "$probe_out" ] || fail "discovery probe printed nothing"

for t in "${TOOLS[@]}"; do
  case "$probe_out" in
    *"$t"*) ;;
    *) fail "tier-1 promise broken: tool $t did not mount" ;;
  esac
done

# --- 4. honest absence: no pane entry point, no false tell ------------------
[ ! -e "$STAGE/desktop/plugin.js" ] \
  || fail "staged tree carries desktop/plugin.js (pane would falsely arrive)"

# Same detector as tests/test_install_tiers.py (keep in lockstep).
tell_hits="$(grep -rniE 'loaded[[:space:]]+automatically|automatically[[:space:]]+loaded' \
  "$STAGE/README.md" "$STAGE/plugin.yaml" "$STAGE/docs" 2>/dev/null || true)"
if [ -n "$tell_hits" ]; then
  echo "$tell_hits" >&2
  fail "false pane tell found in shipped docs (see hits above)"
fi

# --- 5. receipt (parsed by tests/test_install_tiers.py) ---------------------
HOME_SCRATCH="$HOME_SCRATCH" probe_out="$probe_out" "$VENV_PY" - <<'PY'
import json, os
r = json.loads(os.environ["probe_out"])
r["home"] = os.environ["HOME_SCRATCH"]
r["false_tell_hits"] = 0
print("TIER1_RECEIPT " + json.dumps(r, sort_keys=True))
PY

echo "verify-tier1: Tier 1 holds — tools mount on vanilla hermes, pane absent, docs honest."
