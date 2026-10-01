"""Fixed-argv native boundary to the bd (Beads) CLI.

The only authority for how we talk to bd is the installed binary itself:
`bd version`, `bd info`, and `bd <cmd> --help` against the pinned build
observed in this lab (v1.3.0, f45b249ce). Do not copy flags from tutorials.

Contract ( / N0):
  - Fixed argv lists executed with subprocess.run(shell=False). Callers pass
    path/ID/label values as separate argv entries; nothing is shell-interpolated.
  - Every invocation binds an explicit canonical workspace (cwd=) and, for
    mutations and audit-relevant reads, an explicit --actor.
  - JSON is parsed from stdout; a nonzero exit is honored regardless of whether
    stdout parses (a pipe-masked exit-1 must not become an empty success).
  - Exit-code map (observed bd 1.3.0): 0 ok | 1 general failure | 13 stale
    --if-assignee/--if-status guard (nothing written; NEVER retry the same
    guard) | 2 --max-rows circuit breaker (NOT an empty result).

Reads and UI-facing queries build on run_bd(); this module
intentionally ships only what the initial smoke and capability probes need.
"""
import json
import os
import shutil
import subprocess

DEFAULT_TIMEOUT = 30

# ---- fixed-argv security boundary ------------------------------------
# run_bd is the single spawn point. There is no shell, so shell metachars,
# "../" and newlines are inert inside a token. The only injection path left is
# a caller VALUE that bd parses as a FLAG (e.g. an id "--db=/x" or "-C/etc").
# Rule: a dash-leading token must be a known plugin flag, or the value slot
# right after a value-taking flag (pflag binds that slot as the value, never as
# a flag). Gateway-owned globals (--actor, --readonly, --db, -C ...) are not in
# either set, so a prefix can never carry them.
_VALUE_FLAGS = frozenset({
    "-n", "--max-rows", "--limit", "--label", "--status", "--parent",
    "--reason", "--if-assignee", "--if-status", "--priority", "--notes",
    "--append-notes", "--title", "--description", "--due", "--estimate",
    "--external-ref", "--defer", "--metadata-field", "--set-metadata",
    "--unset-metadata"})
_BOOL_FLAGS = frozenset({"--json", "--all", "--claim", "--dry-run",
                         "--exclude-type=epic"})


_ID_AT = {"show": 1, "update": 1, "close": 1, "reopen": 1, "heartbeat": 1,
          "unclaim": 1, "history": 1, "query": 1, "children": 1, "comments": 1}


def _argv_ok(argv_prefix):
    """True iff no caller token can be parsed by bd as an unintended flag.

    * first token is a bare verb (never dash-leading);
    * every dash-leading token is a known plugin flag, except the one slot
      right after a value-taking flag (pflag binds it as that flag's value);
    * after a literal "--" everything is positional text (bd stops flag parsing).
    Pure: no I/O. Refusal happens before any subprocess.run."""
    if not argv_prefix or not isinstance(argv_prefix[0], str) \
            or argv_prefix[0].startswith("-"):
        return False
    # id slot never dash-leading: `close <id=--reason> --reason Y` would
    # otherwise bind the reason and close issue Y (confused deputy).
    verb = argv_prefix[0]
    at = _ID_AT.get(verb)
    if verb in ("comments", "epic") and len(argv_prefix) > 1 \
            and argv_prefix[1] in ("add", "status"):
        at = 2
    if at is not None and (len(argv_prefix) <= at
                           or not isinstance(argv_prefix[at], str)
                           or argv_prefix[at].startswith("-")):
        return False
    expect_value = False
    positional_only = False
    for tok in argv_prefix:
        if not isinstance(tok, str) or "\x00" in tok:
            return False
        if expect_value or positional_only:
            expect_value = False
            continue
        if tok == "--":
            positional_only = True
        elif tok.startswith("-"):
            if tok in _VALUE_FLAGS:
                expect_value = True
            elif tok not in _BOOL_FLAGS:
                return False
    return not expect_value


class NativeError(Exception):
    """Base class for every named failure the boundary can raise."""


class BdNotFoundError(NativeError):
    """The bd binary is not present/executable."""

    def __init__(self, bd_bin, attempted):
        super().__init__(
            f"bd backend unavailable: {bd_bin!r} not found or not executable. "
            f"Install Beads or set HERMES_BEADS_BD_BIN; attempted argv: {attempted}"
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
    if not _argv_ok(argv_prefix):
        # refused before any subprocess.run: no partial exec, no side effect
        raise ValueError(f"argv refused by fixed-argv gate: {argv_prefix!r}")
    _workspace_check(workspace)
    if actor is not None and (not actor or actor.startswith("-")
                              or any(c in actor for c in "\x00\n\r")):
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
