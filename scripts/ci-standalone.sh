#!/bin/sh
# Standalone-without-Workflow gate (hbl-pnu.3.2): isolated HERMES_HOME, no hermes-workflows, stock bd parity from a foreign cwd.
set -eu
cd "$(dirname "$0")/.."
exec python3 tests/test_standalone.py
