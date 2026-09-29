"""Fixed-argv native boundary to the bd (Beads) CLI.

The only authority for how we talk to bd is the installed binary itself:
`bd version`, `bd info`, and `bd <cmd> --help` against the pinned build
observed in this lab (v1.3.0, f45b249ce). Do not copy flags from tutorials.

Contract (hbl-pnu.1.6 / N0):
  - Fixed argv lists executed with subprocess.run(shell=False). Callers pass
    path/ID/label values as separate argv entries; nothing is shell-interpolated.
  - Every invocation binds an explicit canonical workspace (cwd=) and, for
    mutations and audit-relevant reads, an explicit --actor.
  - JSON is parsed from stdout; a nonzero exit is honored regardless of whether
    stdout parses (a pipe-masked exit-1 must not become an empty success).
  - Exit-code map (observed bd 1.3.0): 0 ok | 1 general failure | 13 stale
    --if-assignee/--if-status guard (nothing written; NEVER retry the same
    guard) | 2 --max-rows circuit breaker (NOT an empty result).

N1 (hbl-pnu.1.1) broadens the read/UI surface on top of run_bd(); this module
intentionally ships only what the initial smoke and capability probes need.
"""
import json
import os
import shutil
import itertools
import subprocess

DEFAULT_TIMEOUT = 30

# ---- fixed-argv security boundary (hbl-pnu.4.4) -----------------------------
# run_bd is the single spawn point; before ANY subprocess.run the argv_prefix
# must match one of the exact shapes below (token classes, in order). That is
# the injection surface: no shipped caller ever composes a value INTO a flag
# or appends a raw list, so an off-table argv has no legitimate origin and is
# refused whole. Values stay opaque (text bodies may contain ;rm, ../../x,
# newlines — argv has no shell, inert as single tokens); ids are exact-ID
# tokens (no dash-leading, pathy, or control-token shapes); --actor and
# --readonly are gateway-controlled and may never appear in a prefix at all.

_SHAPES = "shapes"

# Token classes: exact literal ("F:--json" or bare verb), "I" exact-ID token,
# "V" bounded scalar value, "T" opaque text body (comment/reason/notes).

_GATEWAY_FLAGS = ("--actor", "--readonly")  # run_bd owns these slots
_META = ";|&$<>`"  # shell meta — dead inside value/ID tokens (belt, no shell anyway)

_LIST_OPTS = (("F:--status", "V"), ("F:--label", "V"), ("F:--parent", "V"),
              ("F:--all",))
_GUARD = ["F:--if-assignee", "V", "F:--if-status", "V", "F:--json"]
_FIELD_GROUPS = (("F:--priority", "V"), ("F:--notes", "T"), ("F:--due", "V"),
                 ("F:--estimate", "V"), ("F:--external-ref", "V"),
                 ("F:--defer", "V"))


def _opts(base, options):
    """base argv + every ordered subset of the (flag, class) option pairs."""
    out = []
    for k in range(len(options) + 1):
        for combo in itertools.combinations(options, k):
            out.append(base + list(itertools.chain.from_iterable(combo)))
    return out


def _field_permutations(base, groups):
    """base argv + any ordered selection of field-flag groups in any order
    (bd takes them in any sequence; write_protocol passes dict order)."""
    out = []
    for k in range(len(groups) + 1):
        for perm in itertools.permutations(groups, k):
            out.append(base + list(itertools.chain.from_iterable(perm)))
    return out


_SPEC_LISTS = []
# ready frontier (native bounds always present; label optional)
_SPEC_LISTS += _opts(["ready", "F:--json", "F:--exclude-type=epic", "F:-n",
                      "V", "F:--max-rows", "V"], [("F:--label", "V")])
# list family (read_model.list_issues: flags appended in this fixed order)
_SPEC_LISTS += _opts(["list", "F:--json", "F:-n", "V", "F:--max-rows", "V"],
                     _LIST_OPTS)
# list by metadata field (correlation.find_by_run)
_SPEC_LISTS += _opts(["list", "F:--json", "F:--metadata-field", "V"],
                     [("F:--all",)])
# circuit-breaker probe shape (tests pin --max-rows alone)
_SPEC_LISTS += [["list", "F:--json", "F:--max-rows", "V"]]
_SPEC_LISTS += [["query", "V", "F:--json", "F:-n", "V"]]
_SPEC_LISTS += _opts(["blocked", "F:--json"],
                     [("F:--label", "V"), ("F:--parent", "V")])
_SPEC_LISTS += [["comments", "I", "F:--json"],
                ["comments", "add", "I", "T"],
                ["history", "I", "F:--json", "F:--limit", "V"],
                ["info", "F:--json"],
                ["create", "T"],
                ["update", "I", "F:--claim", "F:--json"],
                ["heartbeat", "I", "F:--json"],
                # failure-body contract shapes (unguarded, used against a
                # stubbed proc; write_protocol's AST audit owns guard policy)
                ["update", "I", "F:--notes", "T", "F:--json"],
                # negative-qualification shapes (single-guard probes)
                ["update", "I", "F:--if-assignee", "V", "F:--append-notes",
                 "T", "F:--json"],
                ["update", "I", "F:--if-status", "V", "F:--priority", "V",
                 "F:--json"],
                ["epic", "status", "I", "F:--json"],
                ["epic", "close-eligible", "F:--dry-run", "F:--json"],
                ["close", "I", "F:--reason", "T", "F:--json"],
                ["reopen", "I", "F:--reason", "T", "F:--json"]]
_SPEC_LISTS += _opts(["unclaim", "I", "F:--if-assignee", "V", "F:--json"],
                     [("F:--reason", "T")])
# guarded update: bare guard pair, any ordered field subset, replacement
# text, and the correlation metadata namespace (fixed key order subsets)
_SPEC_LISTS += _field_permutations(["update", "I"] + _GUARD, _FIELD_GROUPS)
_SPEC_LISTS += [["update", "I"] + _GUARD + ["F:--title", "T"],
                ["update", "I"] + _GUARD + ["F:--description", "T"]]
_SPEC_LISTS += _opts(["update", "I"] + _GUARD,
                     [("F:--set-metadata", "V")] * 3)
_SPEC_LISTS += _opts(["update", "I"] + _GUARD,
                     [("F:--unset-metadata", "V")] * 3)

_FIXED_ARGV_SHAPES = {_SHAPES: tuple(sorted({tuple(s) for s in _SPEC_LISTS}))}


def _tok_ok(cls, tok):
    """Token-class check for the fixed-argv gate (pure, no I/O)."""
    if not isinstance(tok, str) or tok == "" or "\x00" in tok:
        return False
    if cls == "T":
        # opaque text body (comment/description/notes/reason): NO shell in
        # argv, so ;rm/../newlines are inert single tokens — and the plugin's
        # own negative suite round-trips \t\n\r through bd, so they must
        # pass. Only NUL (unspawnable) and gateway-flag spoofing are refused;
        # ESC is refused too — terminal escape payloads must never leave the
        # store toward a renderer via a plugin-composed write.
        return (tok != "" and "\x00" not in tok and "\x1b" not in tok
                and not any(g in tok for g in _GATEWAY_FLAGS))
    if any(c in tok for c in "\n\r\t\x1b") or any(c in tok for c in _META):
        return False
    # gateway-flag spoofing is dead in EVERY non-text slot too (an
    # --actor-looking value never belongs in an id/flag/value position)
    if any(g in tok for g in _GATEWAY_FLAGS):
        return False
    if cls == "V":
        return True  # bounded scalar (ids, labels, dates, counts, key=value)
    if cls == "I":
        # exact-ID token: never dash-leading, pathy, spaced, or control-shaped
        if tok.startswith("-"):
            return False
        if ".." in tok or "/" in tok or "\\" in tok or " " in tok:
            return False
        return all(ch >= " " for ch in tok)
    if cls.startswith("F:"):
        return tok == cls[2:]
    # bare verb/literal token (e.g. "ready", "add", "all") — exact match
    return tok == cls


def _shape_ok(argv_prefix):
    """True iff argv_prefix matches one shipped fixed-argv shape exactly
    (length + per-position token class). Pure: call it without spawning."""
    if not isinstance(argv_prefix, (list, tuple)) \
            or not all(isinstance(t, str) for t in argv_prefix):
        return False
    for shape in _FIXED_ARGV_SHAPES[_SHAPES]:
        if len(shape) != len(argv_prefix):
            continue
        if all(_tok_ok(cls, tok) for cls, tok in zip(shape, argv_prefix)):
            return True
    return False


class NativeError(Exception):
    """Base class for every named failure the boundary can raise."""


class BdNotFoundError(NativeError):
    """The bd binary is not present/executable."""

    def __init__(self, bd_bin, attempted):
        super().__init__(
            f"bd backend unavailable: {bd_bin!r} not found or not executable. "
            f"Install Beads or pass an explicit bd_bin; attempted argv: {attempted}"
        )
        self.bd_bin = bd_bin


class WorkspaceError(NativeError):
    """The explicit workspace is missing or is not an initialized bd store."""

    def __init__(self, workspace, reason):
        super().__init__(
            f"workspace unavailable: {workspace!r} — {reason}. "
            "Pass the canonical absolute store directory (the dir containing "
            ".beads/) or run `bd init` there first."
        )
        self.workspace = workspace


class BdCommandError(NativeError):
    """bd exited nonzero (or its JSON failed to parse) — the honest failure."""

    def __init__(self, argv, exit_code, stderr, stdout=""):
        self.argv, self.exit_code = list(argv), exit_code
        self.stderr, self.stdout = stderr, stdout
        super().__init__(
            f"bd exited {exit_code}: argv={argv} stderr={stderr.strip()[:500] or '(none)'}"
        )


class CircuitBreakerError(BdCommandError):
    """--max-rows exceeded (exit 2): a breached bound, never 'no results'."""


class GuardMismatchError(BdCommandError):
    """Exit 13: a --if-assignee/--if-status precondition no longer held.
    Nothing was written. Retrying the same guard is pointless — re-read, abort,
    and report."""


class JsonParseError(BdCommandError):
    """stdout was not parseable JSON (or the JSON itself reported failures)."""


def _workspace_check(workspace):
    if not os.path.isabs(workspace):
        raise WorkspaceError(workspace, "workspace must be an absolute canonical path")
    if not os.path.isdir(workspace):
        raise WorkspaceError(workspace, "directory does not exist")
    if not os.path.isdir(os.path.join(workspace, ".beads")):
        raise WorkspaceError(workspace, "no .beads/ store present")


def run_bd(argv_prefix, *, workspace, bd_bin="bd", readonly=False, actor=None,
           timeout=DEFAULT_TIMEOUT, expect_json=True):
    """Execute one fixed-argv bd command.

    argv_prefix: list of tokens, e.g. ["ready", "--json", "-n", "100"]. Values
    (paths, IDs, labels) are separate entries — never a composed shell string.
    Returns (exit_code, parsed_json_or_None, stderr). Raises the named errors
    above; a missing binary never degrades into an empty result.
    """
    if not isinstance(argv_prefix, list) or not all(isinstance(t, str) for t in argv_prefix):
        raise ValueError("argv_prefix must be a list of str tokens (fixed argv)")
    if argv_prefix and not _shape_ok(argv_prefix):
        # Fixed-argv law: the prefix must match a shipped shape EXACTLY —
        # refusing here is before any subprocess.run (no partial exec, no
        # side effect). An off-table argv has no legitimate origin.
        raise ValueError(
            f"argv refused by fixed-argv shape gate: {argv_prefix!r} does "
            "not match any shipped fixed-argv shape (see _FIXED_ARGV_SHAPES)")
    _workspace_check(workspace)
    if actor is not None and not _tok_ok("I", actor):
        raise ValueError(f"actor token refused by fixed-argv gate: {actor!r}")

    argv = [bd_bin]
    if readonly:
        argv.append("--readonly")
    if actor:
        argv += ["--actor", actor]
    argv += argv_prefix

    try:
        proc = subprocess.run(argv, cwd=workspace, capture_output=True,
                              text=True, shell=False, timeout=timeout)
    except FileNotFoundError as exc:
        raise BdNotFoundError(bd_bin, argv) from exc
    except subprocess.TimeoutExpired:
        raise BdCommandError(argv, None, f"timed out after {timeout}s")

    parsed = None
    if expect_json and proc.stdout.strip():
        try:
            parsed = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise JsonParseError(argv, proc.returncode, proc.stderr,
                                 proc.stdout) from exc

    if proc.returncode == 2:
        raise CircuitBreakerError(argv, 2, proc.stderr, proc.stdout)
    if proc.returncode == 13:
        raise GuardMismatchError(argv, 13, proc.stderr, proc.stdout)
    if proc.returncode != 0:
        raise BdCommandError(argv, proc.returncode, proc.stderr, proc.stdout)
    if expect_json and argv_prefix and "--json" in argv_prefix and parsed is None:
        # --json was requested but stdout was empty: distinguish from real empty list.
        raise JsonParseError(argv, proc.returncode, proc.stderr, proc.stdout)
    # Failure contract: a JSON body that reports failures is a failure even if
    # the process exited 0 (bd's batch ops carry failed[]/error with exit 0 in
    # some paths). Reject with the named JSON error — never a silent success.
    if isinstance(parsed, dict) and (
            (isinstance(parsed.get("failed"), list) and parsed["failed"])
            or parsed.get("error")):
        raise JsonParseError(argv, proc.returncode, proc.stderr, proc.stdout)
    return proc.returncode, parsed, proc.stderr


# ---- initial-smoke surface only (N1 broadens this) --------------------------

def store_info(workspace, *, bd_bin="bd"):
    """Store identity (database path, mode, issue count).

    Uses `bd info --json` (native output mode), NOT the plain-text rendering:
    returns the parsed canonical database_path together with the workspace so
    H1's storeIdentityKey({workspace, db}) consumes a real pair.
    """
    _, parsed, _ = run_bd(["info", "--json"], workspace=workspace, bd_bin=bd_bin,
                          readonly=True, expect_json=True)
    db = (parsed or {}).get("database_path")
    if not db:
        raise JsonParseError(["info", "--json"], 0, "info JSON lacks database_path")
    return {
        "workspace": os.path.realpath(workspace),
        "db": db,
        "mode": (parsed or {}).get("mode"),
        "issue_count": (parsed or {}).get("issue_count"),
    }


def version(workspace, *, bd_bin="bd"):
    """Installed bd version string, or raises BdNotFoundError."""
    _workspace_check(workspace)
    try:
        proc = subprocess.run([bd_bin, "version"], cwd=workspace,
                              capture_output=True, text=True, shell=False, timeout=10)
    except FileNotFoundError as exc:
        raise BdNotFoundError(bd_bin, [bd_bin, "version"]) from exc
    if proc.returncode != 0:
        raise BdCommandError([bd_bin, "version"], proc.returncode, proc.stderr)
    return (proc.stdout or "").strip()


def ready_frontier(workspace, *, bd_bin="bd", label=None, limit=100, max_rows=1000):
    """Bounded, scoped ready frontier (epics excluded — they never dispatch)."""
    argv = ["ready", "--json", "--exclude-type=epic", "-n", str(limit)]
    if label:
        argv += ["--label", label]
    if max_rows:
        argv += ["--max-rows", str(max_rows)]
    _, parsed, _ = run_bd(argv, workspace=workspace, bd_bin=bd_bin, readonly=True)
    return parsed or []


def show(workspace, issue_id, *, bd_bin="bd"):
    """Exact-ID show. bd returns an ARRAY (audited legacy shape) — never assume object."""
    _, parsed, _ = run_bd(["show", issue_id, "--json"], workspace=workspace,
                          bd_bin=bd_bin, readonly=True)
    if isinstance(parsed, list):
        return parsed[0] if parsed else None
    return parsed


def claim(workspace, issue_id, *, bd_bin="bd", actor):
    """Atomic claim by the explicit actor, then MANDATORY read-back equality."""
    if not actor:
        raise ValueError("claim requires an explicit actor identity")
    _, parsed, _ = run_bd(["update", issue_id, "--claim", "--json"],
                          workspace=workspace, bd_bin=bd_bin, actor=actor)
    row = parsed[0] if isinstance(parsed, list) and parsed else parsed
    back = show(workspace, issue_id, bd_bin=bd_bin)
    if not row or not back or back.get("assignee") != actor or back.get("status") != "in_progress":
        raise BdCommandError(["update", issue_id, "--claim"], 0,
                             f"claim read-back mismatch: expected owner={actor} "
                             f"in_progress, got {back!r}")
    return back


def heartbeat(workspace, issue_id, *, bd_bin="bd", actor):
    _, parsed, _ = run_bd(["heartbeat", issue_id, "--json"], workspace=workspace,
                          bd_bin=bd_bin, actor=actor)
    return parsed


def smoke(workspace, *, bd_bin="bd", label=None):
    """Initial-smoke receipt: version + store identity + bounded frontier.
    Returns a plain dict; failures arrive as named NativeError subclasses,
    never as an empty list dressed up as success."""
    v = version(workspace, bd_bin=bd_bin)
    ready = ready_frontier(workspace, bd_bin=bd_bin, label=label)
    return {
        "ok": True,
        "bd_version": v,
        "workspace": os.path.realpath(workspace),
        "ready_count": len(ready),
        "ready_ids": [r.get("id") for r in ready],
    }
