# I0 interop capabilities (execution lane)

Scope: `interop.py` only. Stdlib-only. No Workflow import, no scheduler/session
API invented, no dispatch, no live effecting. Read-only against the installed
trees (`/opt/hermes`, `/home/hermes/.hermes/plugins/hermes-workflows`).

## Supported (executable + tested here)

| Capability | Evidence |
|---|---|
| Exact `(workspace, bead, intent)` preservation, incl. on rejections | `tests/test_interop.py::ExactIdentity`, `BadRequests` |
| Ask/Refine → typed `unqualified` refusal (`session_door_unqualified`) that still reports routing intent; session link carries **no execution authority** | `test_refine_routes_session_door_without_authority`, `test_ask_refine_delivery_flag_unqualified`, `test_session_link_cannot_authorize_work` |
| Work without a qualified admission receipt → typed `unsupported` (`workflow_admission_unqualified`), `delivery: false` | `test_work_without_receipt_is_typed_unsupported` |
| Fake receipt claiming supported admission → refused (`unqualified_admission_claim`) | `test_fake_receipt_claiming_supported_is_refused` |
| Bad/out-of-scope requests rejected without dropping exact IDs | `BadRequests` |
| Qualification reads **actual installed source signatures** via `ast` (no plugin import/execution) | `Qualification` tests; plugin dir present on this host |
| Whole installed source trees hashed before/after each probe pass (both trees; explicit mutable/generated/cache exclusions enumerated in `interop.TREE_EXCL_*`) | `test_tree_integrity_pair_over_whole_installed_trees` |

## Source-observed primitives (inspection only — NOT a runtime guarantee)

Observed in `/home/hermes/.hermes/plugins/hermes-workflows` with bounded
argv/signature/body evidence captured in-run: `act_run`, `act_wait`,
`act_stop`, `act_list` (`__init__.py`), `write_spawn_record` (`wf.py`),
`runner_alive` (`wfcommon.py`) → post-Popen spawn record, stop.request latch,
runner liveness, state read. These are source observations, never runtime
claims; no door earns `qualified_runtime` in this slice.

## Unsupported doors (why nothing is called "supported")

- **C5 session door (Ask/Refine delivery)** — unsupported: within the
  inspected scope the installed source exposes only internal symbols
  (`get_or_create_session` in `gateway/session.py` is a single-flight store
  lookup; `cmd_sessions` in `hermes_cli/sessions_cmd.py` lists local
  history). No supported session open/link API with actual permission and
  scope checks was **found in this inspection** — a claim bounded to the
  inspected files, not proof of global nonexistence. Ask/Refine therefore
  return `ok:false, status:unqualified, error:session_door_unqualified` —
  routing is reported, delivery is unavailable, manual session navigation is
  the honest fallback. The read-only `hermes sessions list` argv probe is
  preserved as evidence that it only LISTS history; it never opens a door.
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
today); a qualified C5 session open/link door (until one actually ships and
is probed live); durable grant/replay receipts; stop/`stopping_unknown`
projection; native `bd` effects. Add when a real qualified Workflow admission
receipt or supported session door lands.

Run: `python3 -m unittest tests.test_interop` from the repo root.
