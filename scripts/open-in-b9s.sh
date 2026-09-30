#!/usr/bin/env bash
# open-in-b9s.sh — council S3 affordance (2026-09-30): LAUNCH b9s against the
# active beads store; never bundle, never iframe. Prints the pairing URL.
# Usage: open-in-b9s.sh <store-dir-containing-.beads> [listen-addr]
# b9s is an EXTERNAL recommendation: this script fails with an install hint,
# it never downloads anything.
set -eu
STORE="${1:?usage: open-in-b9s.sh <store-dir> [listen-addr]}"
LISTEN="${2:-127.0.0.1:7979}"
B9S="${B9S_BIN:-b9s}"
command -v "$B9S" >/dev/null 2>&1 || {
  echo "b9s not found (set B9S_BIN or install from github.com/vanderheijden86/b9s)." >&2
  echo "This is an optional external explorer; hermes-beads does not ship it." >&2
  exit 127
}
cd "$STORE"
# -no-token is loopback-only by b9s's own rule; writes route through their bd
# CLI exec (same discipline we use). Nothing here ships or embeds their UI.
exec "$B9S" web -no-token -listen "$LISTEN"
