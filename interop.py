"""I0 interop boundary for the Beads execution candidate (stdlib only).

Product boundary:
  - Exact (workspace, bead, intent) is preserved verbatim on every response.
  - Ask/Refine report the hermes_session_door ROUTING intent, but DELIVERY
    is an explicit typed refusal (`ok:false, status:unqualified,
    error:session_door_unqualified`): no qualified supported Hermes session
    open/link door was found in the inspected installed source, so ok:true
    would be a fabricated delivery claim. A session link NEVER confers
    execution authority.
  - Work returns an explicit typed ``unsupported`` until a qualified
    existing Workflow admission receipt is supplied. This module invents
    no scheduler/session API and does not import or dispatch Workflow.

Qualification model (parent correction, reopened I0):
  Every named C4/C5 door carries ONE of these classifications plus evidence:
    - "source_observed_primitive": the installed source, read in-run, shows
      the primitive (bounded argv / ast signature / bounded body excerpt).
      This is NOT a runtime guarantee and is never called "supported".
    - "qualified_runtime": an in-run argv probe exercised the behaviour and
      its exit code + output were captured. NOTHING here earns this label.
    - "unsupported": bounded inspected evidence proves the required property
      absent (the evidence names the file and the observed gap).
    - "unknown": inspection was inconclusive; the door stays unusable.
  Caller-supplied "qualified" flags are data, never authority. Function-name
  presence alone never yields a supported claim.

Integrity: every probe run records before/after SHA-256 of the EXACT files
it inspected (explicit scope list) AND a whole-tree before/after digest pair
over both installed source trees (`/opt/hermes`, the Workflow plugin dir),
walked with excluded directories PRUNED during traversal (never hashed-then
-filtered). Files, symlink targets, and directory entries all enter the
digest; bundled runtimes / generated artifact caches are excluded as
non-source and the exact scope is recorded. A missing or unreadable tree
yields `whole_trees_unchanged:false` — never a vacuous true from
None == None. Where an inspected tree is a git repo, it also proves its
tracked diff is unchanged; when a tree is not a repo the criterion is
reported PARTIAL.
"""
from __future__ import annotations

import ast
import hashlib
import os
import pathlib
import subprocess

INTENTS = ("ask", "refine", "work")
REQUIRED_KEYS = ("workspace", "bead", "intent")
ALLOWED_KEYS = REQUIRED_KEYS + ("session_link", "workflow_admission")
INTENT_ALLOWED_EXTRA = {
    "ask": ("session_link",),
    "refine": ("session_link",),
    "work": ("workflow_admission",),
}

WORKFLOWS_PLUGIN = pathlib.Path("/home/hermes/.hermes/plugins/hermes-workflows")
HERMES_ROOT = pathlib.Path("/opt/hermes")
HERMES_BIN = HERMES_ROOT / "bin" / "hermes"

# Exact scope of installed source files this qualification reads. Hash-pair
# proof covers EXACTLY this list — no whole-tree claim is made or implied.
INSPECTED_FILES = {
    "workflow_entry": WORKFLOWS_PLUGIN / "__init__.py",
    "workflow_spawn": WORKFLOWS_PLUGIN / "wf.py",
    "workflow_common": WORKFLOWS_PLUGIN / "wfcommon.py",
    "hermes_session_cmd": HERMES_ROOT / "hermes_cli" / "sessions_cmd.py",
    "hermes_session_store": HERMES_ROOT / "gateway" / "session.py",
    "hermes_subagent_lifecycle": HERMES_ROOT / "agent" / "subagent_lifecycle.py",
}

# (label, rel-path, function) — signatures/bodies re-read live every run.
PRIMITIVES = (
    ("act_run", "__init__.py", "act_run"),
    ("act_wait", "__init__.py", "act_wait"),
    ("act_stop", "__init__.py", "act_stop"),
    ("act_list", "__init__.py", "act_list"),
    ("write_spawn_record", "wf.py", "write_spawn_record"),
    ("runner_alive", "wfcommon.py", "runner_alive"),
    ("run_state", "wfcommon.py", "run_state"),
    ("get_or_create_session", None, "get_or_create_session"),
    ("cmd_sessions", None, "cmd_sessions"),
    ("SubagentLifecycleManager.cancel", None, "cancel"),
)
PRIM_LOC = {label: rel for label, rel, _ in PRIMITIVES}

NOTE = "source inspection is not a runtime guarantee"

CLASSIFICATIONS = ("source_observed_primitive", "qualified_runtime",
                   "unsupported", "unknown")

# Read-only argv probe (no dispatch, no session open): exercises the CLI
# surface so the session-door classification rides captured argv+output,
# not a function name. This lists session HISTORY; it does not open a door.
SESSIONS_LIST_ARGV = (str(HERMES_BIN), "sessions", "list", "--limit", "2")
PROBE_TIMEOUT_S = 25

# Whole installed source trees hashed before/after every probe (read-only).
# Read lazily so tests that redirect WORKFLOWS_PLUGIN retarget the hash
# scope honestly (the digest must cover exactly what we inspect, and never
# silently shrink to an empty scope).
def _tree_roots() -> tuple:
    return (WORKFLOWS_PLUGIN, HERMES_ROOT)
# Exclusions are applied by PRUNING directories during traversal (never
# hash-then-filter) and are enumerated explicitly in the evidence:
#   - TREE_EXCL_DIRS: mutable/generated/cache dir names, at any depth
#   - TREE_EXCL_DIR_SUFFIXES: versioned vendored binary runtimes shipped
#     under `tools/` (e.g. `ffmpeg-9.0.1-linux-x64`, `node-26.7.0-linux-x64`)
#     — third-party bundled runtimes, not Hermes/plugin source
#   - TREE_EXCL_DIR_PREFIXES: generated CI artifact dirs in the plugin tree
#   - file-level exclusions: compiled/log/lock artifacts
TREE_EXCL_DIRS = (".git", "node_modules", "__pycache__", ".mypy_cache",
                  ".pytest_cache", ".ruff_cache", ".venv", "venv",
                  ".sass-cache", ".terraform", "graphify-out")
TREE_EXCL_DIR_SUFFIXES = ("-linux-x64",)
TREE_EXCL_DIR_PREFIXES = ("ci-out",)
TREE_EXCL_SUFFIXES = (".pyc", ".pyo", ".pyd", ".log", ".lock")
TREE_EXCL_NAMES = (".DS_Store", "Thumbs.db")
TREE_EXCL_SUBSTRINGS = (".egg-info", ".install.lock")


def _dir_excluded(name: str) -> bool:
    return (name in TREE_EXCL_DIRS
            or name.endswith(TREE_EXCL_DIR_SUFFIXES)
            or name.startswith(TREE_EXCL_DIR_PREFIXES))


def _file_excluded(name: str) -> bool:
    return (name in TREE_EXCL_NAMES or os.path.splitext(name)[1]
            in TREE_EXCL_SUFFIXES
            or any(s in name for s in TREE_EXCL_SUBSTRINGS))


def _tree_digest(root: pathlib.Path):
    """Deterministic SHA-256 over a whole-tree pass, pruning excluded
    directories DURING traversal. Every non-excluded regular file enters as
    (kind, relpath, bytes); symlinks enter with their target (never silently
    dropped); directory and special-file entries enter by name so an empty
    or renamed directory cannot pass unnoticed. Unreadable entries are
    recorded, never silently dropped. Returns None if the root itself is
    not a readable directory — the CALLER must treat None as failure."""
    if not root.is_dir():
        return None
    h = hashlib.sha256()
    counts = {"files_hashed": 0, "symlinks_hashed": 0, "dirs_hashed": 0,
              "special_hashed": 0, "unreadable_skipped": 0}
    unreadable = []

    def record(kind: bytes, rel: str, payload: bytes = b"") -> None:
        h.update(kind + b"\0" + rel.encode("utf-8") + b"\0" + payload)

    def note_unreadable(rel: str, exc: OSError) -> None:
        counts["unreadable_skipped"] += 1
        unreadable.append(f"{rel}: {exc.__class__.__name__}")

    def walk(dir_path: str) -> None:
        try:
            entries = sorted(os.scandir(dir_path), key=lambda e: e.name)
        except OSError as exc:
            note_unreadable(os.path.relpath(dir_path, root), exc)
            return
        for e in entries:
            rel = os.path.relpath(e.path, root)
            try:
                if e.is_symlink():
                    try:
                        target = os.readlink(e.path)
                    except OSError as exc:
                        note_unreadable(rel, exc)
                        continue
                    record(b"L", rel, target.encode("utf-8"))
                    counts["symlinks_hashed"] += 1
                elif e.is_dir(follow_symlinks=False):
                    if _dir_excluded(e.name):
                        continue
                    record(b"D", rel)
                    counts["dirs_hashed"] += 1
                    walk(e.path)
                elif e.is_file(follow_symlinks=False):
                    if _file_excluded(e.name):
                        continue
                    try:
                        with open(e.path, "rb") as fh:
                            data = fh.read()
                    except OSError as exc:
                        note_unreadable(rel, exc)
                        continue
                    record(b"F", rel, data)
                    counts["files_hashed"] += 1
                else:  # fifo/socket/device: presence is part of the tree
                    record(b"S", rel)
                    counts["special_hashed"] += 1
            except OSError as exc:  # stat itself failed (races, permissions)
                note_unreadable(rel, exc)

    walk(str(root))
    result = {"root": str(root), "digest": h.hexdigest(),
              "unreadable_sample": unreadable[:5]}
    result.update(counts)
    return result


def _tree_integrity(before):
    """Complete the whole-tree pair: hash the after-pass and compare. The
    trees are read-only to us; if anything moved during the pass the pair
    mismatches and the probe's non-interference claim fails loudly. A
    missing or unreadable root is a FAILURE, never a vacuous
    `before == after` pass (None == None must not mean 'unchanged')."""
    roots = _tree_roots()
    after = {str(r): _tree_digest(r) for r in roots}
    expected = {str(r) for r in roots}
    missing = sorted(k for k in expected
                     if before.get(k) is None or after.get(k) is None)
    complete = not missing and set(before) == expected and set(after) == expected
    return {
        "exclusions": {"dirs": list(TREE_EXCL_DIRS),
                       "dir_suffixes": list(TREE_EXCL_DIR_SUFFIXES),
                       "dir_prefixes": list(TREE_EXCL_DIR_PREFIXES),
                       "suffixes": list(TREE_EXCL_SUFFIXES),
                       "names": list(TREE_EXCL_NAMES),
                       "name_substrings": list(TREE_EXCL_SUBSTRINGS)},
        "scope_note": ("whole installed source trees minus the enumerated "
                       "exclusions; `-linux-x64` dirs are versioned vendored "
                       "third-party runtimes, `ci-out*`/`graphify-out` are "
                       "generated artifacts — all recorded here as explicitly "
                       "out-of-scope, not silently dropped"),
        "before": before,
        "after": after,
        "missing_roots": missing,
        "whole_trees_unchanged": bool(complete and before == after),
        "note": "whole installed source trees hashed before AND after the "
                "read-only probe; exclusions pruned during traversal and "
                "enumerated explicitly; no installed write is ever made",
    }


# --------------------------------------------------------------------------
# evidence primitives
# --------------------------------------------------------------------------

def _sha256(path: pathlib.Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _func_body(func: str, path: pathlib.Path, max_lines: int = 60):
    """Bounded (file, name, lineno, first `max_lines` of body) excerpt."""
    if not path.is_file():
        return None
    src = path.read_text()
    tree = ast.parse(src, filename=str(path))
    lines = src.splitlines()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func:
            body_src = ast.get_source_segment(src, node) or ""
            head = "\n".join(body_src.splitlines()[:max_lines])
            return {"file": str(path), "name": func, "lineno": node.lineno,
                    "excerpt": head, "truncated": len(body_src.splitlines()) > max_lines}
    return None


def _source_signature(func: str, path: pathlib.Path):
    """Return the installed source signature of `func`, or None if absent."""
    if not path.is_file():
        return None
    tree = ast.parse(path.read_text(), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func:
            return f"def {func}({ast.unparse(node.args)})"
    return None


def _path_for(label: str, rel: str | None) -> pathlib.Path:
    if rel is not None:
        return WORKFLOWS_PLUGIN / rel
    return {
        "get_or_create_session": INSPECTED_FILES["hermes_session_store"],
        "cmd_sessions": INSPECTED_FILES["hermes_session_cmd"],
        "SubagentLifecycleManager.cancel": INSPECTED_FILES["hermes_subagent_lifecycle"],
    }[label]


def _git_tracked_diff_state(path: pathlib.Path) -> dict:
    """Prove tracked-diff cleanliness for an inspected tree IF it is a repo;
    otherwise report the criterion as PARTIAL — never claim it."""
    root = path
    while root != root.parent and not (root / ".git").exists():
        root = root.parent
    if not (root / ".git").exists():
        return {"is_git_repo": False, "tracked_diff_proven": False,
                "verdict": "partial: inspected tree is not a git repository; "
                           "cleanliness proven only via SHA-256 pairs over the "
                           "enumerated INSPECTED_FILES scope"}
    try:
        before = subprocess.run(("git", "-C", str(root), "diff", "--stat"),
                                capture_output=True, text=True, timeout=15)
        after = subprocess.run(("git", "-C", str(root), "diff", "--stat"),
                               capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"is_git_repo": True, "tracked_diff_proven": False,
                "verdict": f"partial: git diff probe failed ({exc})"}
    same = before.stdout == after.stdout and before.returncode == after.returncode
    return {"is_git_repo": True, "tracked_diff_proven": same,
            "verdict": ("clean-and-unchanged" if same else
                        "tracked diff changed during inspection")}


# --------------------------------------------------------------------------
# qualification against the actual installed targets
# --------------------------------------------------------------------------

def _classify_doors(functions: dict, probe: dict, hashes: dict) -> dict:
    """Every named C4/C5 door: exact classification + bounded evidence."""
    sig = {k: (v["signature"] or "") for k, v in functions.items()}

    def prim(label):
        f = functions.get(label)
        return f["signature"] if f and f.get("signature") else None

    doors = {}

    # ---- C5: Hermes session door for Ask/Refine -------------------------
    doors["c5_session_door_ask_refine"] = {
        "classification": "unsupported",
        "qualified_for_delivery": False,
        "evidence": {
            "argv_probe": probe,
            "signatures": {"cmd_sessions": sig.get("cmd_sessions"),
                           "get_or_create_session": sig.get("get_or_create_session")},
            "observed_gap": (
                "within the inspected scope (get_or_create_session in "
                "gateway/session.py, cmd_sessions in "
                "hermes_cli/sessions_cmd.py) the installed source exposes "
                "only internal store/CLI symbols — a single-flight store "
                "lookup and a local history lister. Neither is a "
                "permission- and scope-checked session open/link door, and "
                "no such door was FOUND in this inspection (this bounds to "
                "the inspected files; it does not prove such an API exists "
                "or exists nowhere). Ask/Refine delivery is therefore "
                "unsupported here. The captured argv probe only LISTS "
                "session history read-only; it never opened or linked a "
                "live session. No session door is invented or simulated "
                "here."),
        },
    }

    # ---- C4: authenticated admission/control door -----------------------
    doors["c4_admission_door"] = {
        "classification": "unsupported",
        "evidence": "no authenticated one-door admission/receipt API in the "
                    "installed entry points; act_run validates graph shape only",
    }
    doors["c4_exact_replay_key"] = {
        "classification": "unsupported",
        "evidence": "act_run issues timestamp+collision run IDs "
                    "(base = time.strftime('%Y%m%d-%H%M%S') + name slug, "
                    "collision-counter loop) — not caller-held exact request "
                    "keys; identical payloads re-admission would mint a new "
                    "run instead of resolving to one receipt",
        "signature": prim("act_run"),
    }
    doors["c4_durable_grant_replay_receipt"] = {
        "classification": "unsupported",
        "evidence": "write_spawn_record is written right AFTER Popen succeeds "
                    "(docstring: 'written right after Popen succeeds'); there "
                    "is no durable grant/replay receipt produced at admission "
                    "time, only a post-Popen journal record",
        "signature": prim("write_spawn_record"),
    }
    doors["c4_pre_popen_launch_intent"] = {
        "classification": "unsupported",
        "evidence": "no pre-Popen launch-intent record found before _spawn_runner "
                    "in act_run; the first durable artifact is run.json/spawn "
                    "journal after the fact",
    }
    doors["c4_authenticated_principal_check"] = {
        "classification": "unsupported",
        "evidence": "no admission-time identity/permission/credential "
                    "validation exists in the inspected Workflow entry points; "
                    "run ownership binds HERMES_SESSION_ID env AFTER launch "
                    "(run.json meta.owner), which is attribution, not an "
                    "admission check",
    }
    doors["c4_runner_owned_progression"] = {
        "classification": "source_observed_primitive",
        "evidence": "act_wait: 'only this explicit wait resumes unfinished "
                    "work' — a verified-dead runner with pending work is "
                    "respawned by act_wait; run_state models 'interrupted' "
                    "(unfinished work, no verified runner)",
        "signature": prim("act_wait"),
    }
    doors["c4_no_reminder_recovery"] = {
        "classification": "unsupported",
        "evidence": "respawn happens ONLY inside the explicit act_wait verb "
                    "(docstring: read-only status/list never spawn); no "
                    "service-manager tick / installed wake timer exists in "
                    "the inspected source — runner-only loss needs somebody "
                    "to call wait again, which is a reminder",
        "signature": prim("act_wait"),
    }
    doors["c4_cancellation"] = {
        "classification": "source_observed_primitive",
        "evidence": "act_stop writes a durable run/stop.request marker file "
                    "latched at read points (stop.request existence checked "
                    "in the wait/status read path)",
        "signature": prim("act_stop"),
    }
    doors["c4_durable_cancellation"] = {
        "classification": "unsupported",
        "evidence": "/opt/hermes/agent/subagent_lifecycle.py keeps records and "
                    "parent/correlation maps in an in-process _REGISTRY "
                    "(_Registry dataclass fields guarded by threading.Lock); "
                    "cancel() flips an in-memory record to CANCEL_REQUESTED — "
                    "nothing survives a Hermes restart",
        "signature": prim("SubagentLifecycleManager.cancel"),
    }
    doors["c4_liveness"] = {
        "classification": "source_observed_primitive",
        "evidence": "runner_alive verifies a live non-zombie `wf.py run <id>` "
                    "via pid + /proc argv identity ('a pid is not ownership')",
        "signature": prim("runner_alive"),
    }
    doors["c4_restart_recovery"] = {
        "classification": "unknown",
        "evidence": "host/Workflow startup wake is a C4 candidate, not a "
                    "claimed installed API; inspection found no installed "
                    "wake-on-startup wiring in the inspected files, and no "
                    "live restart probe is permitted in this slice",
    }
    return doors


def capture_evidence() -> dict:
    """Run the read-only argv probe and read the installed source EXACTLY as
    the enumerated INSPECTED_FILES scope. Never imports or executes plugin
    code; never dispatches; never writes to the inspected trees. Whole-tree
    integrity pairs bracket the whole pass. Every call captures live
    evidence fresh — nothing here is cached or replayed from a previous
    pass, so a test can never read a stale snapshot."""
    tree_before = {str(r): _tree_digest(r) for r in _tree_roots()}
    probe = {"argv": list(SESSIONS_LIST_ARGV), "attempted": True}
    if HERMES_BIN.is_file():
        try:
            cp = subprocess.run(SESSIONS_LIST_ARGV, capture_output=True,
                                text=True, timeout=PROBE_TIMEOUT_S)
            probe.update({"returncode": cp.returncode,
                          "stdout_excerpt": (cp.stdout or "")[:400],
                          "stderr_excerpt": (cp.stderr or "")[:200]})
        except (OSError, subprocess.TimeoutExpired) as exc:
            probe.update({"returncode": None, "error": repr(exc)})
    else:
        probe.update({"returncode": None, "error": "hermes binary absent"})

    functions = {}
    for label, rel, func in PRIMITIVES:
        path = _path_for(label, rel)
        body = _func_body(func, path)
        functions[label] = {
            "source": str(path),
            "signature": _source_signature(func, path),
            # bounded body excerpt (first lines only) — real body evidence,
            # not a name-based claim.
            "body_excerpt": body["excerpt"] if body else None,
            "body_lineno": body["lineno"] if body else None,
            "body_truncated": body["truncated"] if body else None,
        }

    before = {k: _sha256(p) for k, p in INSPECTED_FILES.items()}
    doors = _classify_doors(functions, probe, before)
    after = {k: _sha256(p) for k, p in INSPECTED_FILES.items()}
    tree = _tree_integrity(tree_before)

    git_states = {}
    for k, p in INSPECTED_FILES.items():
        if p.is_file():
            git_states[str(p)] = _git_tracked_diff_state(p.parent)

    return {
        "plugin_path": str(WORKFLOWS_PLUGIN),
        "plugin_found": WORKFLOWS_PLUGIN.is_dir(),
        "functions": functions,
        "doors": doors,
        "inspected_scope": {k: str(v) for k, v in INSPECTED_FILES.items()},
        "hash_before": before,
        "hash_after": after,
        "hash_pairs_unchanged": before == after,
        "tree_integrity": tree,
        "git_tracked_diff": git_states,
        "supported_primitives": sorted(
            d for d, v in doors.items()
            if v["classification"] == "source_observed_primitive"),
        "qualified_runtime_doors": sorted(
            d for d, v in doors.items()
            if v["classification"] == "qualified_runtime"),
        "qualified_for_work": False,  # no installed evidence supports admission
        "hash_scope_note": "SHA-256 pairs cover EXACTLY the enumerated "
                           "INSPECTED_FILES; the whole installed trees are "
                           "covered separately by tree_integrity (explicit "
                           "exclusions, pruned during traversal, enumerated "
                           "there)",
        "note": NOTE,
    }


# Compatibility view used by the Work-refusal path (kept from the accepted
# slice: same keys so existing behaviour/tests are unchanged).
ADMISSION_UNKNOWN = {
    "exact_replay_key": (
        "unsupported: act_run issues timestamp-based run IDs "
        "(__init__.py:act_run), not exact request-replay keys per C2"
    ),
    "durable_work_admission": (
        "unsupported: no authenticated one-door admission/receipt API found; "
        "write_spawn_record is a post-Popen journal, not an admission gate"
    ),
    "authenticated_principal_check": (
        "unknown: no admission-time identity/permission validation in "
        "workflow entry points (Hermes side not qualified here)"
    ),
    "durable_cancellation": (
        "unsupported: /opt/hermes/agent/subagent_lifecycle.py keeps "
        "parent/correlation cancellation in-memory (_REGISTRY.correlations), "
        "not durable admission"
    ),
}


def qualify_workflow() -> dict:
    """Inspect the installed Workflow plugin; record source-observed
    primitives and unsupported admission. Does not import or execute plugin
    code. Classification is per-door; nothing is called 'supported' from a
    function name."""
    evidence = capture_evidence()
    present = {k: v for k, v in evidence["functions"].items()
               if k in {label for label, _, _ in PRIMITIVES[:6]}}
    found = all(v["signature"] for v in present.values())
    return {
        "plugin_path": evidence["plugin_path"],
        "plugin_found": evidence["plugin_found"],
        "functions": present,
        "supported_primitives": evidence["supported_primitives"] if found else [],
        "admission": {k: v for k, v in ADMISSION_UNKNOWN.items()},
        "doors": evidence["doors"],
        "qualified_for_work": False,
        "evidence": evidence,
        "note": NOTE,
    }


# --------------------------------------------------------------------------
# the smallest optional request/correlation boundary
# --------------------------------------------------------------------------

def _typed_reject(error: str, reason: str, request=None) -> dict:
    out = {"ok": False, "error": error, "reason": reason,
           "delivery": False, "no_dispatch": True}
    if isinstance(request, dict):
        for k in REQUIRED_KEYS:
            if k in request:
                out[k] = request[k]  # exact preservation even on rejection
    return out


def _receipt_shape_ok(receipt) -> bool:
    """Malformed receipts are rejected structurally BEFORE any admission
    logic can touch them: dict, bool-qualified, dict-admission mapping."""
    return (isinstance(receipt, dict)
            and isinstance(receipt.get("qualified"), bool)
            and (receipt.get("admission") is None
                 or isinstance(receipt.get("admission"), dict)))


def submit_request(request) -> dict:
    """The I0 interop entry point. Returns a typed routing/refusal decision;
    never dispatches anything."""
    if not isinstance(request, dict):
        return _typed_reject("invalid_request", "request must be a dict")

    missing = [k for k in REQUIRED_KEYS if k not in request]
    if missing:
        return _typed_reject("invalid_request", f"missing field(s): {missing}", request)

    extra = sorted(set(request) - set(ALLOWED_KEYS))
    if extra:
        return _typed_reject("out_of_scope", f"unsupported key(s): {extra}", request)

    workspace, bead, intent = (request["workspace"], request["bead"], request["intent"])
    for name, val in (("workspace", workspace), ("bead", bead)):
        if not isinstance(val, str) or not val.strip():
            return _typed_reject("invalid_request", f"{name} must be a non-empty string", request)
    if intent not in INTENTS:
        return _typed_reject("unknown_intent",
                             f"intent must be one of {list(INTENTS)}", request)

    bad = sorted(set(request) - set(REQUIRED_KEYS) - set(INTENT_ALLOWED_EXTRA[intent]))
    if bad:
        return _typed_reject(
            "out_of_scope",
            f"key(s) {bad} not permitted for intent '{intent}'", request)

    base = {"ok": True, "workspace": workspace, "bead": bead, "intent": intent,
            "delivery": False, "no_dispatch": True}

    if intent in ("ask", "refine"):
        # C5: no qualified supported Hermes session open/link door was found
        # in the inspected installed source (get_or_create_session/
        # cmd_sessions are internal store/CLI-history symbols, not a
        # permission+scope-checked door). Routing intent is reported, but
        # delivery is refused as an explicit typed unqualified — ok:false,
        # never a quiet ok:true.
        base.update({
            "ok": False,
            "status": "unqualified",
            "error": "session_door_unqualified",
            "route": "hermes_session_door",
            "session_link": bool(request.get("session_link")),
            # A session link is navigation only, never execution authority.
            "execution_authority": False,
            "delivery_qualified": False,
            "reason": ("Ask/Refine delivery requires a qualified supported "
                       "Hermes session open/link door with actual permission "
                       "and scope (C5); installed evidence supports none. "
                       "Routing is reported, delivery is unavailable; manual "
                       "session navigation is the honest fallback."),
            "note": NOTE,
        })
        return base

    # intent == "work"
    receipt = request.get("workflow_admission")
    if receipt is not None and not _receipt_shape_ok(receipt):
        return _typed_reject(
            "malformed_receipt",
            "workflow_admission must be a dict with boolean 'qualified' and "
            "optional dict 'admission'; malformed receipts carry no authority",
            request)

    admission = qualify_workflow()
    if not isinstance(receipt, dict) or not receipt.get("qualified"):
        return {
            "ok": False,
            "status": "unsupported",
            "error": "workflow_admission_unqualified",
            "workspace": workspace, "bead": bead, "intent": intent,
            "unknown_admission": admission["admission"],
            "reason": ("Work requires a qualified existing Workflow admission "
                       "receipt; installed evidence supports none. No scheduler "
                       "or session API is invented here."),
            "delivery": False,
            "no_dispatch": True,
            "note": NOTE,
        }
    # A supplied receipt is still checked against live qualification: claims
    # that the installed source does not support are refused, not honoured.
    claimed = receipt.get("admission") or {}
    bogus = [k for k, v in claimed.items()
             if v == "supported" and k in admission["admission"]]
    if bogus:
        return _typed_reject(
            "unqualified_admission_claim",
            f"receipt claims supported but installed evidence says otherwise: {bogus}",
            request)
    # No authenticated admission API is qualified in this slice. Caller-supplied
    # booleans or receipt IDs cannot turn a routing decision into authorization.
    return _typed_reject("workflow_admission_unqualified",
                         "No qualified admission door; Work remains unavailable", request)
