# I0 interop capabilities (execution lane)

Scope: `interop.py` only. Stdlib-only. No Workflow import, no scheduler/session
API invented, no dispatch, no live effecting.

## Supported (executable + tested here)

| Capability | Evidence |
|---|---|
| Exact `(workspace, bead, intent)` preservation, incl. on rejections | `tests/test_interop.py::ExactIdentity`, `BadRequests` |
| Ask/Refine → Hermes session door; link carries **no execution authority** | `test_refine_routes_session_door_without_authority`, `test_session_link_cannot_authorize_work` |
| Work without a qualified admission receipt → typed `unsupported` (`workflow_admission_unqualified`), `delivery: false` | `test_work_without_receipt_is_typed_unsupported` |
| Fake receipt claiming supported admission → refused (`unqualified_admission_claim`) | `test_fake_receipt_claiming_supported_is_refused` |
| Bad/out-of-scope requests rejected without dropping exact IDs | `BadRequests` |
| Qualification reads **actual installed source signatures** via `ast` (no plugin import/execution) | `Qualification` tests; plugin dir present on this host |

## Qualified-against installed source (inspection only — NOT a runtime guarantee)

Supported primitives observed in `/home/hermes/.hermes/plugins/hermes-workflows`:
`act_run`, `act_wait`, `act_stop`, `act_list` (`__init__.py`), `write_spawn_record`
(`wf.py`), `runner_alive` (`wfcommon.py`) → spawn journal, stop.request latch,
runner liveness, state read.

## UNKNOWN / unsupported admission (why Work stays `unsupported`)

- `exact_replay_key` — unsupported: `act_run` issues timestamp-based run IDs,
  not exact request-replay keys (C2 requires exact-key replay).
- `durable_work_admission` — unsupported: no authenticated one-door
  admission/receipt API; `write_spawn_record` is post-Popen journaling, not an
  admission gate.
- `authenticated_principal_check` — unknown: no admission-time identity/
  permission validation in the inspected Workflow entry points.
- `durable_cancellation` — unsupported: `/opt/hermes/agent/subagent_lifecycle.py`
  keeps parent/correlation cancellation in-memory (`_REGISTRY.correlations`).

## Deliberately left out (I3 and later own these)

Full admitted-Work handling against a real qualified Workflow receipt (the
`admitted` branch exists but no installed evidence qualifies any receipt
today); durable grant/replay receipts; stop/`stopping_unknown` projection;
native `bd` effects. Add when a real qualified Workflow admission/receipt
transport lands.

Run: `python3 -m unittest tests.test_interop` from the repo root.
