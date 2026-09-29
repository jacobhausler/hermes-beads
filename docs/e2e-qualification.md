# E2E Qualification (hbl-pnu.4.1)

Qualifies the thin dispatch → work → evidence → closure loop end-to-end
against the REAL pinned bd (v1.3.0, f45b249ce): fresh disposable stores built
by `tests/fixtures/e2e-runtime/bootstrap.py`, no mocks, no in-memory stand-ins.
Run: `python3 tests/test_e2e.py` (8 tests; failures exit nonzero — proven by
the in-suite self-destruct subprocess, never by trusting the harness).

## The causal loop the test traverses (exact argv, every step read back)

| step | call | native argv (`bin/bd -C <store> [--readonly] --actor <a> …`) | read-back |
|---|---|---|---|
| 1 frontier | `read_model.ready(label="impl")` | `ready --json --exclude-type=epic --label impl -n 100` | gate present; blocked successor, off-label decoy absent; epic absent — AND present in the raw frontier (`ready --json --label impl`, exclusion off): the bound is real, not an accident of seeding |
| 2 claim | `claims.claim(iid, actor=worker)` | `update <iid> --assignee <worker> --status in_progress --if-assignee "" --if-status open --json` | row `assignee==worker`, `status==in_progress`; second actor's mid-loop claim raises `ClaimConflictError` naming the holder (serial contention — never a forced takeover); heartbeat while active |
| 3 guarded edit | `write_protocol.update_fields(fields={"notes":…}, if_assignee=…, if_status="in_progress")` | `update <iid> --notes <v> --if-assignee <worker> --if-status in_progress --json` | `bd show --json` notes equal; `readback_verified=True`; stale guard → exit 13 → `WriteStaleError(holder)`, store unchanged (hash-compared) |
| 4 evidence | `evidence.WorkerSurface.record_evidence` | `comments add <iid> -f <file>` — text begins `EVIDENCE attempt=<a> artifacts=<;joined>` | comments API shows exactly one envelope by this author/attempt |
| 5 close | `evidence.authorized_close(authorization, reason, evidence_actor, attempt, artifacts)` | guarded `close <iid> --reason … --json` | precondition: real envelope comment on the bead |
| 6 verify | returned record + `bd show --json` + comments API | — | `status=closed`, `closed_at` set, `close_reason==reason`, envelope still present |
| 7 successors | `read_model.ready` again | step-1 argv | gate gone, successor now ready — the close released `dep add successor gate` (type=blocks): complete causal loop |

## Invariants proven beyond the happy path

- **Replacement stays DISABLED.** `replace_description`/`replace_title` raise
  `ExplicitUnsupportedError` by default and the row hash is unchanged. The
  "guarded edit" of the bead spec is satisfied by the SUPPORTED metadata
  update (`--if-assignee` + `--if-status`); there is no `--if-revision`, and
  none is faked.
- **Native-close-without-evidence is rejected by the plugin verifier, not by
  native close** (owner contract C3): a bead native-closed with no envelope
  closes natively (exit 0 — native closure remains permitted), while
  `verify_closure` raises `ClosureAmbiguityError("…evidence comment absent…")`
  and `authorized_close` raises `ClosureRefusedError` leaving the row
  byte-identical (hash-compared). Positive control in the same class: the
  same native close WITH an envelope passes `verify_closure` — the refusal is
  specifically the absent evidence, not closure provenance.
- **Append-only comments are native fact, not invented capability.** bd 1.3.0
  has only `comments add` / `comments <id>` — every deletion-shaped argv
  (`delete`, `rm`, trailing `delete`) exits nonzero and the comment survives.
  The negative fixture is a separate store whose bead never received an
  envelope; NO comment-deletion was simulated. `evidence.py` never builds a
  delete verb (source audit).
- **Unique actor per run/attempt; parent ≠ worker.** The claimer never closes;
  closure carries an explicit `authorization` string naming the review and an
  artifact-citing reason (`done: <artifacts>` — bd's own shape).

## Fixture discipline

- Disposable stores live under `tests/fixtures/e2e-runtime/` — unique per
  run, gitignored via the worktree's `.git/info/exclude` (the shared
  `.gitignore` is untouched); only `bootstrap.py` is tracked. Runtime
  databases are never committed.
- Each store gets its own `git init` — without it bd's embedded-dolt home
  falls through to the lab repo's databases (recorded trap).
- Stores are deleted at exit unless `E2E_KEEP_FIXTURES=1` (debug aid).
- The planning store is read-only to this lane: the test only creates its own
  throwaway stores; fixture closure is the explicitly permitted closure path.
- No `--force`, no SQL, no shadow store, no Beads memory/mail/formulas.

## Read-only discovery commands an operator can rerun

```
bin/bd --version                                             # v1.3.0 (f45b249ce)
bin/bd -C <store> --readonly ready --json --exclude-type=epic --label impl   # frontier
bin/bd -C <store> --readonly show <iid> --json               # claim/close read-back
bin/bd -C <store> --readonly comments <iid> --json           # envelope read-back
```
