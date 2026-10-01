# Changelog

All notable changes to this plugin are documented here.
The format follows Keep-a-Changelog; versions follow SemVer.

## [0.1.1] — 2026-10-01

Honest-surface release: the plugin now ships only what is wired, exercised,
and documented.

### Removed (moved to branch `door-stack/v0.1.0` until qualified)
- The unqualified door/scheduler chassis: `interop`, `bot_handoff`,
  `work_door`, `runner_binding`, `runner_hooks`, `work_bridge`,
  `correlation`, `evidence` (~60% of the previous source) and their test
  surface. Release notes no longer describe behavior that cannot fire.
- The internal qualification vocabulary; "never claim an unexercised
  behavior" is a contributor rule now, not a shipped ontology.
- Dead `docs/` ledgers; the capability contract lives inline at the boundary
  (`beads/native.py`) where a reader meets it.

### Restructured
- `beads/` Python package (native, read_model, claims, write_protocol);
  the repo root keeps only the plugin entry point.
- Six tools registered (`beads_smoke`, `beads_frontier`, `beads_show`,
  `beads_claim`, `beads_update`, `beads_comment`).
- `AGENTS.md` as the contributor front door; `LICENSE` added (MIT).

### Gates
- `.github/workflows/ci.yml` runs every gate with its exit code propagated
  (`set -o pipefail`, no swallowed statuses) against a pinned hermes-agent
  and a checksum-pinned bd release.
- The 0.1.0 release lane's wrapper bug (a gate printing FAILED next to
  EXIT=0) is fixed by construction: suites exit nonzero, CI exits on first
  nonzero suite.

## [0.1.0] — 2026-09-30

First public release of the native Beads (bd) work-graph plugin for Hermes.

### Core boundary
- Native bd adapter: explicit canonical workspace + actor, fixed argv (no
  shell interpolation), honest exit-code map (0/1/13/2), JSON parsing that
  keeps failed rows, and named "unavailable" errors instead of invented data.
- Escape hatch: stock `bd` alone remains fully sufficient; the plugin only
  reduces agent effort. Generic exploration belongs to b9s — launched, never
  bundled.

### Work graph
- Scoped ready frontier and native claim with mandatory read-back; blocked or
  inherited-blocked work is refused before mutation, honestly.
- Guarded write protocol with exit-13 STALE discipline; blind replacement is
  refused by default (bd 1.3.0 has no content CAS — declared honestly).
- Evidence-backed closure with authorized reopen; append-only comments
  (no delete verb).

### Workbench pane
- Tree, search (bounded ancestor recovery), record card, blocker jump with
  inherited blockers, history/breadcrumb. Generic comparison belongs to b9s —
  launched, never bundled.
- Drafts keyed by workspace+store/bead; Save stays disabled pending native
  CAS; copy/export and diff/reload/cancel preserve the draft.
- Visual layer F1–F7: one shipped stylesheet, accessibility floor (ARIA tree
  contract, 3:1 contrast, text labels not color alone), data-driven keymap,
  warm p95 ≤ 100 ms refresh budgets, 1000-row cap with explicit partial scope.

### Doors & bots
- Bot handoff (Ask/Refine) with typed session-door refusals; real Work door
  claims with host-credential binding (owner-only, no symlink/hardlink
  bypasses) and honest run-state + cancel passthroughs.
- Optional hermes.workflow.* correlation, fully additive; zero beads imports
  in the workflow runner (coupling gates enforce it).
- Pane-usage telemetry: an honest instrument for what the pane is actually used for.

### Qualification
- Standalone gate: plugin works with no hermes-workflows and stock-bd parity
  from a foreign cwd.
- Scenario acceptance lanes (orient, blocked-badge, search ancestors, claim
  contention + dead-worker reclaim, cross-branch blocker return, draft
  conflict, bot Ask/Refine) against real seeded bd stores with negative
  controls.
- Mounted React smoke, packaging qualification (exact archive, stock
  admission validator, disposable-home install/uninstall, private-string
  scan), and a negative-control suite for refusal honesty and
  no-lost-update discipline.
