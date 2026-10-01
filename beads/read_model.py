"""Bounded fixed-argv read facade on native.run_bd ( / N1).

Every verb here is one native `bd` invocation with a argv list built in THIS
module — values (IDs, labels, parent IDs) are separate tokens, never shell
composited. There is deliberately NO per-verb framework: no registries, no
base classes, no dispatch tables; just functions that call run_bd and return
the parsed native JSON verbatim.

Field-preservation contract: rows are returned exactly as bd's
--json emitted them. Statuses, issue types, and edge types that this plugin
has never seen are passed through untouched — no whitelist, no coercion, no
reconstruction of readiness. If bd says it, we say it.

Boundary honesty, observed against the pinned lab binary (bd 1.3.0,
f45b249ce) — flags exist where listed, nowhere else:
  ready     -n/--limit, --max-rows (exit 2 breaker), --exclude-type, --label
  list      -n/--limit, --max-rows (exit 2 breaker), --parent, --status, -l
  query     -n/--limit only (--max-rows: "unknown flag", exit 1)
  show      no bound flags (--max-rows: unknown flag) — a single exact ID
  children  only --pretty (no -n, no --max-rows); bd documents it as an alias
            of `list --parent <id> --status all`, so the bounded route to the
            same native rows IS that list argv (see children()).
  blocked   no bound flags at all (label filters + --parent only)
  comments  no bound flags (issue-id required; --local-time only)
  history   --limit N (--limit 0 means UNBOUNDED — never emitted)
  info      --json identity document

For the verbs bd gives no native bound to (blocked, comments), the facade
applies a CLIENT cap after the native call and raises ClientBoundError — an
explicit bound owned and labeled by us, never disguised as bd's exit-2
circuit breaker (native breaker semantics stay reserved for verbs where bd
itself enforces it, via native.CircuitBreakerError).

Failure envelopes are preserved as native raises them: run_bd already refuses
exit 2 (CircuitBreakerError), exit 13 (GuardMismatchError), nonzero exit, and
exit-0 bodies carrying failed[]/error (JsonParseError). Exceptions carry
argv/exit_code/stderr/stdout so the caller sees the untouched envelope —
including the pipe-masked exit-1 receipts where stdout mixes prose+JSON and
refuses to parse (tests replay these from recorded interop receipts).

No cache, no readiness reconstruction (that is bd's GetReadyWork semantics),
no direct DB/SQL, no plugin-minted IDs, no Workflow imports.
"""
from datetime import datetime, timezone

from . import native

# Default bounds are explicit and mandatory: 0/unbounded is rejected.
DEFAULT_LIMIT = 100
DEFAULT_MAX_ROWS = 1000
DEFAULT_HISTORY_LIMIT = 20


class ReadModelError(native.NativeError):
    """Facade-level named failure (bad bound usage, client cap)."""


class ClientBoundError(ReadModelError):
    """Rows returned exceeded the CLIENT cap for a verb bd gives no native
    --max-rows to. Labeled client-side on purpose: this is our bound, not a
    bd circuit-breaker (bd's native breaker is exit 2 → CircuitBreakerError).
    """

    def __init__(self, verb, count, cap):
        super().__init__(
            f"{verb}: {count} rows exceed client cap {cap} "
            f"(bd 1.3.0 offers no native --max-rows for this verb; "
            f"refine filters or raise the cap explicitly)"
        )
        self.verb, self.count, self.cap = verb, count, cap


def _checked_bound(value, name, default):
    """Bounds are explicit: None→default, 0/negative/oversized→named error."""
    if value is None:
        value = default
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ReadModelError(f"{name} must be a positive int (unbounded reads are refused); got {value!r}")
    return value


def _now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _rows(parsed, verb, client_cap=None):
    """Normalize bd's list-shaped --json output and apply a CLIENT cap when
    the verb has no native bound. Preserves every field verbatim."""
    if parsed is None:
        rows = []
    elif isinstance(parsed, list):
        rows = parsed
    elif isinstance(parsed, dict):  # single-row dict shapes (legacy aliases)
        rows = [parsed]
    else:
        raise native.JsonParseError([verb], 0, f"{verb}: unexpected JSON shape {type(parsed).__name__}")
    if client_cap is not None and len(rows) > client_cap:
        raise ClientBoundError(verb, len(rows), client_cap)
    return rows


# ---- verbs (each is exactly one fixed-argv bd invocation) -------------------

def ready(workspace, *, label=None, limit=DEFAULT_LIMIT, max_rows=DEFAULT_MAX_ROWS,
          bd_bin="bd"):
    """Bounded ready frontier: epics excluded (they never dispatch).
    Native bounds: -n and --max-rows (exit 2 → native.CircuitBreakerError)."""
    n = _checked_bound(limit, "limit", DEFAULT_LIMIT)
    mr = _checked_bound(max_rows, "max_rows", DEFAULT_MAX_ROWS)
    argv = ["ready", "--json", "--exclude-type=epic", "-n", str(n),
            "--max-rows", str(mr)]
    if label:
        argv += ["--label", label]
    _, parsed, _ = native.run_bd(argv, workspace=workspace, bd_bin=bd_bin,
                                 readonly=True)
    return _rows(parsed, "ready")


def show(workspace, issue_id, *, bd_bin="bd"):
    """Exact-ID show, array legacy shape flattened. No bound flags exist on
    bd show (probed); the single-ID argv is inherently bounded."""
    if not issue_id or not isinstance(issue_id, str):
        raise ReadModelError("show requires a non-empty issue ID string")
    _, parsed, _ = native.run_bd(["show", issue_id, "--json"],
                                 workspace=workspace, bd_bin=bd_bin,
                                 readonly=True)
    if isinstance(parsed, list):
        return parsed[0] if parsed else None
    return parsed


def list_issues(workspace, *, status=None, label=None, parent=None,
                include_closed=False, limit=DEFAULT_LIMIT,
                max_rows=DEFAULT_MAX_ROWS, bd_bin="bd"):
    """Bounded list. Native bounds: -n and --max-rows (exit 2 breaker)."""
    n = _checked_bound(limit, "limit", DEFAULT_LIMIT)
    mr = _checked_bound(max_rows, "max_rows", DEFAULT_MAX_ROWS)
    argv = ["list", "--json", "-n", str(n), "--max-rows", str(mr)]
    if status:
        argv += ["--status", status]
    if label:
        argv += ["--label", label]
    if parent:
        argv += ["--parent", parent]
    if include_closed:
        argv.append("--all")
    _, parsed, _ = native.run_bd(argv, workspace=workspace, bd_bin=bd_bin,
                                 readonly=True)
    return _rows(parsed, "list")


def children(workspace, parent_id, *, limit=DEFAULT_LIMIT,
             max_rows=DEFAULT_MAX_ROWS, bd_bin="bd"):
    """All children of a parent, bounded.

    `bd children` itself takes no bound flags (only --pretty), so we use the
    native equality bd documents for it — `list --parent <id> --status all`
    — which is a supported stock-bd argv and carries real -n/--max-rows
    enforcement. Not a reconstruction: same query, same rows, native.
    """
    if not parent_id or not isinstance(parent_id, str):
        raise ReadModelError("children requires a non-empty parent ID string")
    argv = ["list", "--json", "--parent", parent_id, "--status", "all",
            "-n", str(_checked_bound(limit, "limit", DEFAULT_LIMIT)),
            "--max-rows", str(_checked_bound(max_rows, "max_rows", DEFAULT_MAX_ROWS))]
    _, parsed, _ = native.run_bd(argv, workspace=workspace, bd_bin=bd_bin,
                                 readonly=True)
    return _rows(parsed, "children")


def query_parent(workspace, parent_id, *, limit=DEFAULT_LIMIT, bd_bin="bd"):
    """query parent=<id> (the task's parent=/query verb). Native bound: -n.
    --max-rows is NOT a flag on bd query (exit 1 'unknown flag', probed) —
    the -n bound is the enforced one."""
    if not parent_id or not isinstance(parent_id, str):
        raise ReadModelError("query_parent requires a non-empty parent ID string")
    n = _checked_bound(limit, "limit", DEFAULT_LIMIT)
    argv = ["query", f"parent={parent_id}", "--json", "-n", str(n)]
    _, parsed, _ = native.run_bd(argv, workspace=workspace, bd_bin=bd_bin,
                                 readonly=True)
    return _rows(parsed, "query_parent")


def blocked(workspace, *, label=None, parent=None, limit=DEFAULT_LIMIT,
            bd_bin="bd"):
    """bd blocked --json. bd 1.3.0 gives this verb NO bound flag (probed
    --help and runtime): the cap below is client-side and honestly labeled
    (ClientBoundError), never a fake exit-2."""
    n = _checked_bound(limit, "limit", DEFAULT_LIMIT)
    argv = ["blocked", "--json"]
    if label:
        argv += ["--label", label]
    if parent:
        argv += ["--parent", parent]
    _, parsed, _ = native.run_bd(argv, workspace=workspace, bd_bin=bd_bin,
                                 readonly=True)
    return _rows(parsed, "blocked", client_cap=n)


def comments(workspace, issue_id, *, limit=DEFAULT_LIMIT, bd_bin="bd"):
    """bd comments <issue-id> --json. No native bound exists; client cap is
    explicit. Missing ID → bd's own error envelope ({'error': ...} exit 1)
    arrives via run_bd's named error, stdout preserved on the exception."""
    if not issue_id or not isinstance(issue_id, str):
        raise ReadModelError("comments requires a non-empty issue ID string")
    n = _checked_bound(limit, "limit", DEFAULT_LIMIT)
    _, parsed, _ = native.run_bd(["comments", issue_id, "--json"],
                                 workspace=workspace, bd_bin=bd_bin,
                                 readonly=True)
    return _rows(parsed, "comments", client_cap=n)


def history(workspace, issue_id, *, limit=DEFAULT_HISTORY_LIMIT, bd_bin="bd"):
    """bd history <id> --json --limit N (native bound). bd treats --limit 0
    as 'all' (probed: returned 4+ rows) — we refuse 0/None here so no call
    ever leaves this module unbounded."""
    if not issue_id or not isinstance(issue_id, str):
        raise ReadModelError("history requires a non-empty issue ID string")
    n = _checked_bound(limit, "limit", DEFAULT_HISTORY_LIMIT)
    _, parsed, _ = native.run_bd(["history", issue_id, "--json", "--limit", str(n)],
                                 workspace=workspace, bd_bin=bd_bin,
                                 readonly=True)
    return _rows(parsed, "history")


def info(workspace, *, bd_bin="bd"):
    """Workspace identity + freshness stamp for every read surface (owner
    contract: show workspace identity and freshness). Delegates identity to
    native.store_info (parsed `bd info --json`, real database_path) and adds
    the version line and the observation timestamp."""
    ident = native.store_info(workspace, bd_bin=bd_bin)
    ident["bd_version"] = native.version(workspace, bd_bin=bd_bin)
    ident["observed_at"] = _now_iso()
    return ident
