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
- Admission is authenticated: the only credential consulted lives at
  `$HERMES_HOME/beads/admission-credential.json` (`credential_path()`), created
  by the host via `provision_credential()` with a fresh 64-hex secret. The door
  opens the beads dir and the file with `O_NOFOLLOW` and checks the SAME fds
  (`fstat`): dir owner-only and not a symlink; file regular, runner-owned,
  mode 0600, exactly one link. Check and read are one open, so a swap, a
  symlinked dir, or a hardlinked self-minted file is refused (`test_r2f`,
  `test_r2g`, `test_r2h`). Every admission/stop carries an HMAC-SHA256
  (`sign_request`/`sign_stop`) over the exact request body.
- Trust boundary, stated plainly: `HERMES_HOME` is trusted host config and
  the runner uid is inside the boundary. A process that controls the runner's
  environment or runs as the runner uid can re-provision (`rotate=True`) or
  read the 0600 secret file — a file-secret scheme cannot stop that; it needs
  a different-uid host signer (production gate hbl-pnu.3.6). The per-call env
  pointer that let a request aim the door at any file is gone. Children do
  not receive the secret in env or argv; a same-uid child can still read the
  file.
- The approved-set ceiling is mandatory: no `approved_beads` in the
  credential = refusal, so omission can never mean "everything in the store".
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

Known residuals (not blocking, stated): the admission signature does not bind a
run id, so the same signed request admitted into a second run dir yields a
second grant (both within the approved ceiling); intent files are not
integrity-signed (tamper fails closed at close time, not at read time).
