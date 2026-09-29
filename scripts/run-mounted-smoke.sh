#!/bin/sh
# Mounted workbench smoke (hbl-pnu.4.5): real React 19 + jsdom DOM against a real bd store.
# RICH_UI_NODE_MODULES must point at a node_modules holding react, react-dom, jsdom (read-only).
set -eu
cd "$(dirname "$0")/.."
: "${RICH_UI_NODE_MODULES:?set to a node_modules dir containing react, react-dom, jsdom}"
: "${MOUNTED_REPORT_DIR:=tests/.mounted-evidence}"
export RICH_UI_NODE_MODULES MOUNTED_REPORT_DIR
exec node --test tests/test_mounted_smoke.mjs
