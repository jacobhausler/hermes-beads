# HBI boundary (council-ratified 2026-09-30, S3: split + recommend, don't bundle)

Sources: ../consults/20260930-hbi-build-vs-bundle-council/READOUT.md (fable/sol/qwen seats).

## The line
- **This plugin (pane) = actions through the door.** Tree/search reach-to-issue,
  record card, blocker jump, drafts→hidden chat turn, claim/dispatch buttons gated
  by the admission door, history stack (app-back). Anything that MUTATES work goes
  through the door or refuses; nothing here bypasses it.
- **b9s = human exploration.** Board, graph, split-compare, churn views, mobile
  pairing, deep search. Reached by LAUNCH (`scripts/open-in-b9s.sh`), never by
  iframe or bundled binary. Their internal API is unversioned — we bind to nothing
  of it except health+snapshot in the drift guard.
- **Any new generic explorer feature request → upstream to b9s, refused here.**
  Generic-class means: views/predicates that a beads explorer could serve any repo,
  with no Hermes door/chat/coupling in them.

## Rules that keep the line
1. No shipped third-party binary in the plugin surface; no iframe embeds.
2. CI runs `tests/test_b9s_drift.mjs`: pinned b9s against `fixtures/b9s-trial/
   planning-copy` — RED = drift alarm for the owner, NOT a release block.
3. Compare-class modules stay behind explicit flags in workbench (default OFF);
   a flag being ON requires a named Hermes-coupled reason, not habit.
4. Deletion of any generic module requires prior pane-usage evidence at the real
   mount (owner law: no deletion on maintenance-cost reasoning alone; council
   fable caveat recorded in READOUT).
5. 60d/90d review triggers live in the READOUT; flip criteria are owner's call.

## Known measurement corrections (adjudication 2026-09-30)
- `history.mjs` is NOT a deletable orphan: the app loader/test callers construct
  `createHistoryStack` and pass it in (workbench consumes it injected); deleting it
  breaks 6 suites. The READOUT's "true orphan" line applied to desktop-internal
  importers only.
- compare-class cut must be feature-flagged inside `workbench.mjs` first
  (it is imported there); file deletion comes only after flags are proven OFF.
