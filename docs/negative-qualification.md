# Negative qualification (hbl-pnu.4.2) — refusal honesty, no-lost-update discipline

Suite: `tests/test_negative.py`. Fixtures: disposable stores under
`tests/fixtures/negative-runtime/` (gitignored via this worktree's
`.git/info/exclude`; each fixture owns its own `git init` so the
embedded-dolt home never reaches the lab repo's databases). Pinned binary:
`/home/hermes/.hermes/work/beads-lab/bin/bd` (bd 1.3.0, f45b249ce). The
planning store is never touched. Receipt semantics replicate (do not
import) `tests/test_native.py` / `tests/test_write_protocol.py` fixture
patterns; production business fields of refused beads are asserted
unchanged on every refusal path.

## Case → observed-behaviour map (all asserted: exit code + --json envelope + on-disk state)

| Case | Exact assertion (observed against pinned bd) |
|---|---|
| Claim conflict names holder | Refused claim: exit 1, JSON envelope `{"error":"1 of 1 issues failed to update","failed":[{"id":…,"error":"updating issue: issue already claimed by <holder>"}],"schema_version":1}`; read-back keeps original assignee; business fields byte-identical. `claims.ClaimConflictError.holder` carries the disclosed name (Q3/Q7 receipts). |
| Guard mismatch | Stale `--if-status`/`--if-assignee`: exit 13, envelope entry carries `guard_mismatch:true`, raw `bd show --json` stdout byte-identical before/after, exactly ONE update argv (argv-audit shim on native.subprocess.run — never retried). |
| --readonly write-block | `bd --readonly` refuses `create`, `comment`, `comment add` with exact stderr `Error: operation '<op>' is not allowed in read-only mode`, exit 1; store unchanged; reads stay green (interop T3 shape). |
| exit-2 circuit breaker | `list --max-rows 1` over 2 rows raises `native.CircuitBreakerError` (exit 2) — never an empty list. |
| honest empty vs backend failure | all-claimed store: `ready --json` exit 0 + stdout `[]` (real empty). Missing binary → `BdNotFoundError`, non-store dir → `WorkspaceError` — never `[]`. |
| empty-label vs backend-empty | `ready --label <none-match>` returns [] AND a separate scoped `list` shows the store's beads: the [] is filter-scoped, not backend-empty. |
| close-on-worker impossible | `WorkerSurface.request_closure` raises `WorkerClosureRefusedError` after appending only a REQUEST-CLOSURE comment; argv audit shows zero `close` tokens on the worker path; worker surface exposes no close/reopen method; every `authorized_close` precondition refusal issues zero subprocess calls. |
| --force audit | AST audit over every `native.run_bd(...)` call site in native.py, claims.py, write_protocol.py, evidence.py, read_model.py, correlation.py, interop.py: zero `--force`, `--if-revision`, `--if-content`, `--cas` tokens; `close`/`reopen` argv literals occur ONLY inside `evidence.authorized_close`/`authorized_reopen`. |

## Threat-model boundary (CONTRACTS-v3 — tested, not asserted)

The worker-closure refusal and the close-precondition gate are
**trusted-agent policy** at the plugin surface: cooperative, enforced in
our code, and provably bypassable — `test_native_close_escape_hatch_stays_open`
runs the *native* `bd close` past a refused worker request and succeeds.
That passing is exactly what keeps the claim honest: this is surface
separation, **not a security boundary**, no ACL, no sandbox, no hostile
same-credential containment. `--readonly` likewise: the same binary
without the flag mutates freely.

## Explicitly UNQUALIFIED (return unknown/unsupported, do not infer)

* concurrent multi-writer: UNQUALIFIED — no test here races two live
  writers; serial contention demos prove honest refusal, not concurrency.
* CAS: UNQUALIFIED — no `--if-revision`/content-conditional mutation
  exists in bd 1.3.0; timestamp/revision preflight + readback is not CAS.
* **declared topology** for any future parallel enablement: *single effecting writer*
  per store (one worker actor per run/attempt; unique actor identity
  enforced by `actor()`); independent native writers must be absent or
  participating before multi-writer admission. This document is the gate:
  `DeclaredTopologyGate` fails this suite if these statements disappear.

Run: `python3 tests/test_negative.py` — failures exit nonzero.
