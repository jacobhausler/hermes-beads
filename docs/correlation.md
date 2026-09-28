# hermes.workflow.* correlation convention (hbl-pnu.3.1)

Optional, uninstall-safe correlation between Beads work and Hermes Workflow
runs. Lives entirely on the Beads-plugin side (`correlation.py`); the Workflow
runner never imports Beads and this module imports nothing from Workflow.
Nothing in the plugin imports this module — its absence changes zero native
semantics.

## Keys (literal flat strings — no nested-object semantics inferred)

    hermes.workflow.run_id
    hermes.workflow.node_id
    hermes.workflow.attempt

## Native surface used (bd 1.3.0, f45b249ce)

| Operation | Native flag |
|---|---|
| set   | `bd update ID --set-metadata key=value` |
| unset | `bd update ID --unset-metadata key` |
| find  | `bd list --json --metadata-field key=value` / `--has-metadata-key key` (`--all` includes closed) |

Established fact (interop R4, re-proven by `tests/test_correlation.py`):
namespaced dotted keys survive status changes and closure; closed beads stay
findable with `--all`.

## Hard rules

- Metadata is OPTIONAL correlation, never scheduler state. No behavior
  branches on key presence; absence means "worked outside Workflow" and
  reads return `{}` / `[]` — never an error.
- Writes ride ONE `bd update` through `write_protocol._guarded_update`, so
  they carry the native guard pair (`--if-assignee` + `--if-status`);
  guard mismatch => `WriteStaleError` (exit 13), nothing written, never
  retried. Reads carry no guards.
- Values are short single-line identifiers (<=256 chars, no newline/NUL);
  no transcripts, outputs, or secrets.
- Foreign keys are never touched: `clear_correlation` removes only keys
  inside `hermes.workflow.` and refuses anything else before any bd call.

## API

- `attach_correlation(ws, id, *, actor, run_id=, node_id=, attempt=, if_assignee=, if_status=)`
- `clear_correlation(ws, id, *, actor, keys=OUR_KEYS, if_assignee=, if_status=)`
- `correlation_for(ws, id)` -> our-keys dict (`{}` when absent)
- `find_by_run(ws, run_id, include_closed=False)` -> rows with a
  `correlation` dict (`[]` when none)

Tests: `python3 tests/test_correlation.py` (real pinned `bd`, disposable
fixtures under `tests/fixtures/correlation-runtime/`, planning store never
touched).
