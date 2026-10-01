# AGENTS.md — hermes-beads

Front door for agents and contributors. Read this before touching the tree.

## What this repo is

A Hermes plugin that gives agents honest tools over a [bd](https://github.com
/steveyegge/beads) work graph, plus a read-mostly desktop pane. The bd binary
is the only authority for work state; this repo never mirrors it.

## Repo map

```
__init__.py            plugin entry: registers the six beads_* tools (thin handlers)
beads/
  native.py            THE boundary: run_bd fixed-argv spawn, argv gate, exit-code map
  read_model.py        bd ready / show / list / children / blocked (bounded, client-capped)
  claims.py            claim + read-back, heartbeat, CAS release; conflicts name holders
  write_protocol.py    guarded metadata updates (exit 13 -> STALE, never retried)
desktop/               Hermes Desktop pane (ESM, zero I/O, host injects reads/door)
tests/                 stdlib unittest + node:test suites; fixtures/ seeds helpers
.github/workflows/     ci.yml — the five gates, each propagating its exit code
```

## The rules that keep the tree publishable

1. **One spawn point.** All bd access goes through `beads/native.py:run_bd`
   with a fixed argv list, an explicit store path, and an explicit `--actor`
   for writes. Never `shell=True`, never string-built commands. New flags must
   be registered in the argv gate (`_VALUE_FLAGS`/`_BOOL_FLAGS`) — the
   security suite fails otherwise.
2. **Honest exit codes.** 0 ok, 1 failure, 13 stale guard (nothing written —
   never retry the same guard), 2 circuit breaker (NOT an empty result). A
   failing run must exit nonzero; any wrapper you add propagates `$?`.
3. **Real probes, not mocks.** Tests exercise the real pinned bd binary
   against disposable stores (each fixture owns its own `git init` so the
   embedded-dolt home never falls through to a parent repo). Never claim an
   unexercised behavior: if you didn't run it, the docs don't say it ships.
4. **The pane is a view.** `desktop/*.mjs` stay import-light (react/jsx-runtime
   only), zero I/O, zero timers; mutations reach bd only through host-injected
   handlers. DOM assertions must be paired with paint checks (stylesheet,
   focus rings) where layout matters.
5. **Nothing ships that isn't wired.** No disabled chassis, no speculative
   adapters. If a door isn't qualified, ship one honest `ok:false` stub, not a
   module of scaffolding. Release notes claim only what ships wired and
   exercised.
6. **Stdlib only** in the Python half; Node built-ins only in the desktop
   half's tests. No new dependencies without a measured reason.

## Commands

```sh
python3 tests/test_native.py             # one file = one suite (stdlib unittest)
for t in tests/test_*.py; do python3 "$t" || exit 1; done
node --test tests/test_*.mjs             # pane half
hermes plugins validate .                # admission gate
```

`BEADS_LAB_BD=/path/to/bd` pins the binary for probes (default: `bd` on PATH).
CI must export `BEADS_LAB_BD`: `test_standalone.py` derives `FOREIGN_CWD`
from the bd binary's directory, so an unpinned PATH-relative binary changes
that fixture's location. Run the Node gate on Node 26.
