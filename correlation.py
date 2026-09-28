"""hermes.workflow.* correlation metadata convention (hbl-pnu.3.1).

Optional, uninstall-safe correlation between Beads work and Workflow runs,
written/read ONLY through supported native flags on the pinned bd build:
`bd update --set-metadata/--unset-metadata` and
`bd list --metadata-field key=value / --has-metadata-key` (facts established
by probe + interop R4: namespaced dotted keys survive status changes and
closure, and closed beads stay findable with `--all`).

Keys (literal CLI-returned shape — dotted keys are flat strings, no nested
object semantics are inferred):
    hermes.workflow.run_id | hermes.workflow.node_id | hermes.workflow.attempt

Hard rules (owner contract):
  * Metadata is OPTIONAL correlation, never scheduler state. NO behavior here
    branches on key presence; absence means "worked outside Workflow" and
    reads return {} — never an error.
  * Guarded where available: writes ride ONE `bd update`, so they carry the
    native guard pair via write_protocol._guarded_update (exit 13 ->
    WriteStaleError, nothing written, never retried). Reads carry no guards.
  * No transcripts, outputs, or secrets: values are short single-line
    identifiers (validated at the boundary).
  * Foreign keys are never touched: clear removes only keys inside our
    namespace and refuses anything else.
  * Correlation lives on the Beads-plugin side; no Workflow imports, no
    runner coupling, nothing imports this module (its absence changes zero
    native semantics).
"""

import native          # reuse anchor — fixed-argv boundary (not rewritten)
import read_model      # reuse anchor — read model (not rewritten)
import write_protocol  # reuse anchor — guarded writer (not rewritten)

NAMESPACE = "hermes.workflow."
K_RUN_ID = "hermes.workflow.run_id"
K_NODE_ID = "hermes.workflow.node_id"
K_ATTEMPT = "hermes.workflow.attempt"
OUR_KEYS = (K_RUN_ID, K_NODE_ID, K_ATTEMPT)

MAX_VALUE_LEN = 256


class CorrelationError(ValueError):
    """Rejected correlation input (unknown key, foreign clear, bad value).
    Raised before any bd call — nothing is written."""


def _validate_value(field, value):
    v = str(value)
    if not v.strip():
        raise CorrelationError(f"{field}: empty value refused")
    if len(v) > MAX_VALUE_LEN or any(c in v for c in ("\n", "\r", "\x00")):
        raise CorrelationError(
            f"{field}: values are short single-line identifiers only — "
            "no transcripts/outputs/secrets in metadata")
    return v


def _validate_pair(if_assignee, if_status):
    if if_assignee is None or if_status is None:
        raise CorrelationError(
            "correlation writes require the native guard pair "
            "(if_assignee + if_status) — guarded where available")


def _metadata_row(row):
    md = (row or {}).get("metadata") or {}
    return md if isinstance(md, dict) else {}


def attach_correlation(workspace, issue_id, *, actor, bd_bin="bd",
                       run_id=None, node_id=None, attempt=None,
                       if_assignee, if_status):
    """Set the provided hermes.workflow.* keys in ONE guarded `bd update`
    (--set-metadata tokens) and return the read-back correlation dict.
    Omitted keys are left as-is; this never clears anything."""
    fields = {}
    for key, val in ((K_RUN_ID, run_id), (K_NODE_ID, node_id),
                     (K_ATTEMPT, attempt)):
        if val is not None:
            fields[key] = _validate_value(key, val)
    if not fields:
        raise CorrelationError("attach_correlation with nothing to set")
    _validate_pair(if_assignee, if_status)
    tokens = []
    for k, v in fields.items():
        tokens += ["--set-metadata", f"{k}={v}"]
    write_protocol._guarded_update(workspace, issue_id, tokens,
                                   actor=actor, bd_bin=bd_bin,
                                   if_assignee=if_assignee,
                                   if_status=if_status)
    return _ours(_metadata_row(read_model.show(workspace, issue_id,
                                               bd_bin=bd_bin)))


def clear_correlation(workspace, issue_id, *, actor, bd_bin="bd",
                      keys=OUR_KEYS, if_assignee, if_status):
    """Unset only OUR namespace keys (default: the three) in one guarded
    `bd update`. Foreign keys — inside or outside the namespace — are
    refused; nothing is written when a refusal is raised."""
    keys = tuple(keys)
    foreign = [k for k in keys if not k.startswith(NAMESPACE)]
    if foreign:
        raise CorrelationError(
            f"refusing to unset foreign keys: {foreign} — this module owns "
            "only hermes.workflow.{run_id,node_id,attempt}")
    _validate_pair(if_assignee, if_status)
    tokens = []
    for k in keys:
        tokens += ["--unset-metadata", k]
    write_protocol._guarded_update(workspace, issue_id, tokens,
                                   actor=actor, bd_bin=bd_bin,
                                   if_assignee=if_assignee,
                                   if_status=if_status)
    return _ours(_metadata_row(read_model.show(workspace, issue_id,
                                               bd_bin=bd_bin)))


def correlation_for(workspace, issue_id, *, bd_bin="bd"):
    """Our correlation keys present on one bead. {} when absent — absence is
    'worked outside Workflow', never an error."""
    return _ours(_metadata_row(read_model.show(workspace, issue_id,
                                               bd_bin=bd_bin)))


def find_by_run(workspace, run_id, *, bd_bin="bd", include_closed=False):
    """Reconciliation query: beads whose hermes.workflow.run_id equals
    run_id, via native `bd list --metadata-field`. Rows carry a
    'correlation' dict of our keys. [] when none match — never an error."""
    run_id = _validate_value(K_RUN_ID, run_id)
    argv = ["list", "--json", "--metadata-field", f"{K_RUN_ID}={run_id}"]
    if include_closed:
        argv.append("--all")  # native flag: closed beads included
    _, parsed, _ = native.run_bd(argv, workspace=workspace, bd_bin=bd_bin,
                                 readonly=True)
    rows = parsed if isinstance(parsed, list) else ([parsed] if parsed else [])
    out = []
    for r in rows:
        r = dict(r)
        r["correlation"] = _ours(_metadata_row(r))
        out.append(r)
    return out


def _ours(md):
    return {k: v for k, v in md.items() if k.startswith(NAMESPACE)}

# ponytail: reads filter client-side over native list JSON; add server-side
# paging here only if a run's correlated set ever exceeds --max-rows.
