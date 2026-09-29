# Runner binding (hbl-pnu.3.3) — what is proven, and what is qualified

Module pair: `runner_binding.py` (admission, grants, closure gating) +
`runner_hooks.py` (patch-in for the isolated Workflow checkout). Tests:
`tests/test_runner_binding.py` drive the real pinned `bd` (v1.3.0) and a real
subprocess of a **patched copy** of `wf.py` under
`tests/fixtures/runner-runtime/` (gitignored). The installed plugin tree is
only read and hash-pinned before/after.

## PROVEN in the isolated lab harness

- Admitted A→B runs to closure end-to-end with no chat/tab continuation
  (fixture child), including after runner-only death (R1: a verified live
  orphan is adopted via the runner's own `_adopt_child`; the solo path never
  Popen's a second child).
- Admission is authenticated: the credential file must carry a host-held
  `secret` (>=32 chars, file mode 0600, under the runner's own state dir,
  generated once). Every admission/stop is verified by HMAC-SHA256
  (`sign_request`/`sign_stop`) over the exact request body. Self-issued
  credentials without the secret are refused (typed `BindingRefusal`), as are
  forged signatures and unsigned stops.
- The approved-set ceiling is mandatory: no `approved_beads` in the
  credential = refusal, so omission can never mean "everything in the store".
- The child never inherits the credential: the runner prelude pops
  `BEADS_ADMISSION_CREDENTIAL_FILE` from the child env before Popen.
- External-close causality needs a runner-held random nonce
  (`secrets.token_hex(16)`) recorded in the run-dir launch intent BEFORE the
  claim. A native close reason must carry `nonce=<value>` verbatim AND the
  closer must be the grant's verifier actor; a forged substring alone leaves
  the successor gated (`external_closed_unknown`).
- Launch intent is written and fsynced (file + containing dir) before
  `claims.claim`; a claim failure leaves the intent for observation-based
  reconcile, never a blind delete.
- Stop race: the stop latch is checked inside `before_launch` under the spawn
  lock; the pre-existing race test is kept and passes.
- No second scheduler (AST audit asserts hooks never spawn their own loop).

## QUALIFIED ONLY — NOT proven here (spec says leave these explicitly blocked)

1. **Real-Hermes autonomy.** The worker child in every test is the committed
   `tests/fixtures/runner-binding/fake-hermes` fixture. Fixture mocks alone do
   NOT qualify autonomy against production Hermes. Qualification requires an
   executable door against the actual installed Hermes under hbl-pnu.3.4.
2. **C3 topology.** This binding declares a **single cooperating mutator**
   topology: exactly one admitted runner is the sole native mutator per store;
   there is NO compare-and-swap / native C3 guard, and the close window is
   read-then-act. The topology string is a declaration, not enforcement —
   hostile or concurrent mutators are out of contract (trusted-agent scope).
3. **Startup wake.** Nothing re-invokes the runner after host restart;
   recovery is by re-running `wf.py run <id>` (the fsynced intent + live-child
   adoption make that re-run safe). No daemon is invented for Beads; a real
   wake door belongs to hbl-pnu.3.4 qualification.

## Reviewer-probe disposition

| probe | finding | pinned test |
|---|---|---|
| p2_runner_kill / p2b_diag | R1 duplicate worker spawn | `test_r1_runner_only_death_never_respawns_live_child` |
| p3_auth_replay | R2 self-issued credential, optional ceiling, env leak, unsigned stop | `test_r2a`–`test_r2e` |
| p4_forged_close_replay | R3 forged close token credited | `test_r3a`–`test_r3c` |
| P6 probe | R4 claim before any intent | `test_r4_launch_intent_fsynced_before_claim` |

Honest open item: the reviewer's suite baseline (176 tests / 92 errors in
their lane copy, from missing gitignored fixture dirs) is unreproduced here
and unexplained; this lane's suite runs green with fixture dirs created on
demand under `tests/fixtures/`.
