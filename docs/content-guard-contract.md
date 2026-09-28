# Content-guard contract (hbl-pnu.1.7)

Observed: bd 1.3.0 (f45b249ce6b40ba62aecc03949e6371e8f7c79d8), embedded dolt,
lab binary `/home/hermes/.hermes/work/beads-lab/bin/bd`. Evidence here is
qualification of THAT build, not a minimum-version promise.

## Verdict: UNSUPPORTED — no atomic expected-content/revision mutation

`bd update` exposes exactly two guards, and neither protects content:

```text
argv: bd update --help
      --if-assignee string   Apply the update only if the current assignee
                             equals this value; mismatch writes nothing,
                             exit 13. Cannot combine with --claim.
      --if-status string     Apply the update only if the current status
                             equals this value; mismatch writes nothing,
                             exit 13. Cannot combine with --claim.
```

Probed candidate guard flags — every one is rejected (exit 1,
`Error: unknown flag:`): `--if-revision`, `--if-content`,
`--expect-revision`, `--content-hash`, `--if-desc`, `--cas-revision`.
`bd edit` (interactive description editor) has no guard flags at all.

The `revision` field that `bd show --json` exposes changes on every content
edit (observed `8543466990343094550` -> `4577632972477670462` after one
description change) but **cannot be handed back to any command as a
precondition**. It is observation telemetry, not a CAS token.

## Executable two-actor evidence (isolated fixture stores, own git root)

Raw journal: `../../../workflows/20260928-014347-beads-build-sprint2/work/guard/content-guard-probes.json`
and `tests/test_content_guard.py` (11 tests, all green against the real binary).

1. **Blind overwrite.** Actor A `update --description A-EDIT` (exit 0), then
   actor B with a stale baseline `update --description B-EDIT` (exit 0).
   Final description `B-EDIT`; assignee `""` and status `open` never moved.
   **No native conflict detection.** One edit is silently lost.
2. **Available guards pass over stale content.** With A's edit already in,
   B runs `update --if-assignee '' --if-status open --description B-STALE`
   (exit 0) — final description `B-STALE`. Assignee/status guards are
   satisfied while the description is clobbered: they are not content guards.
3. **Revision is not consumable.** `update --if-revision <observed> ...`
   -> exit 1 `unknown flag`.
4. **Content is not claim-protected either.** After a live claim by owner, a
   different actor's plain `update --description` (no `--force`) is accepted
   (exit 0) and replaces the description while the claim/assignee stay
   intact. Only `--claim` and `-a` collide with a live claim.

## Contract rules (binding for this plugin)

- **Do not advertise preflight as CAS.** A read-then-write sequence has a
  write-write TOCTOU window that bd 1.3.0 cannot close; the test
  `test_preflight_detects_racing_writer_but_leaves_toctou_window` proves the
  racing write is silently replaced even when preflight was consulted.
- **Unsafe blind replacement is DISABLED by default.**
  `tests/test_content_guard.py:guarded_description_update` refuses to write
  when the caller's baseline description no longer matches the store
  (`StaleContentError`), and refuses without a baseline at all
  (`UnsafeContentReplacementError`). Re-enabling requires the explicit
  per-call `allow_unsafe_blind_replace=True` — never a config default, never
  set on a caller's behalf by shared code.
- **Claim discipline is the only native exclusion for descriptions is not
  available**: use `bd update --claim` ownership plus single-writer policy,
  and treat every other description writer as a conflict to surface, not a
  merge to win.
- Any future code path that enables blind description replacement without the
  explicit opt-in violates this contract; the guard tests must fail first.

## Concrete upstream requirement (bd)

Request a content-conditional update mirroring the existing guard posture:

- `bd update ID --if-revision <rev> --description ... --json` (and/or
  `--if-content sha256:<hex>`): single-issue precondition evaluated in the
  same transaction as the write; mismatch writes nothing and exits 13 like
  `--if-assignee`/`--if-status`; `--json` failure envelope carries
  `"guard_mismatch": true`.
- Accept either the exact opaque `revision` value from `bd show --json` or a
  caller-supplied content hash, so a preflight read plus guarded write
  becomes a real CAS without inventing client-side locks.
- Same semantics for `--body-file`/`--stdin` description sources and for
  `bd edit` (refuse to save if the revision moved since the buffer opened).
- No new lock product, no sidecar daemon, no schema fork: this is the same
  precondition pattern bd already ships for assignee/status.

## Acceptance gaps (honest, unfixed)

- Investigation is complete; **safe collaborative editing is NOT made
  available** — that awaits the upstream guard above (or claim-ownership
  policy outside this lane).
- Qualification covers single-machine embedded-dolt stores; cross-replica or
  Dolt-server merge behavior for description conflicts was not probed here.
- Concurrency was simulated deterministically (sequential writers + injected
  racing writer) rather than by process-level race stress; the negative
  finding does not depend on timing, but a positive CAS claim would need a
  true parallel race harness.
