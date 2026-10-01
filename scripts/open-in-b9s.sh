#!/usr/bin/env bash
# open-in-b9s.sh — convenience wrapper: LAUNCH b9s (git browser) against the
# active beads store; never bundle, never iframe. Prints the pairing URL.
# Usage: open-in-b9s.sh <store-dir-containing-.beads> [listen-addr]
# b9s is an EXTERNAL recommendation: this script fails with an install hint,
# it never downloads anything.
set -eu
STORE="${1:?usage: open-in-b9s.sh <store-dir-containing-.beads> [loopback-listen-addr]}"
LISTEN="${2:-127.0.0.1:7979}"
# -no-token means NO AUTH: binding it off-loopback would expose the store to
# the network unauthenticated, so non-loopback addresses are refused unless
# the operator explicitly acknowledges it with B9S_ALLOW_ANY_BIND=1.
case "$LISTEN" in
  127.*|\[::1\]:*|localhost:*) ;;
  *)
    if [ "${B9S_ALLOW_ANY_BIND:-0}" != "1" ]; then
      echo "refusing to bind '$LISTEN' with -no-token: loopback only (127.x, [::1], localhost)." >&2
      echo "If you really want an unauthenticated network bind, re-run with B9S_ALLOW_ANY_BIND=1." >&2
      exit 64
    fi
    ;;
esac
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
