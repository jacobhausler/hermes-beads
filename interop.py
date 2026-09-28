"""I0 interop boundary for the Beads execution candidate (stdlib only).

Product boundary:
  - Exact (workspace, bead, intent) is preserved verbatim on every response.
  - Ask/Refine route via the Hermes session door; a session link NEVER
    confers execution authority.
  - Work returns an explicit typed ``unsupported`` until a qualified
    existing Workflow admission receipt is supplied. This module invents
    no scheduler/session API and does not import or dispatch Workflow.

Qualification (`qualify_workflow`) reads the ACTUAL installed Workflow
plugin source and records supported primitives vs UNKNOWN/unsupported
admission capabilities. Source inspection is NOT a runtime guarantee.
"""
from __future__ import annotations

import ast
import pathlib

INTENTS = ("ask", "refine", "work")
REQUIRED_KEYS = ("workspace", "bead", "intent")
ALLOWED_KEYS = REQUIRED_KEYS + ("session_link", "workflow_admission")
INTENT_ALLOWED_EXTRA = {
    "ask": ("session_link",),
    "refine": ("session_link",),
    "work": ("workflow_admission",),
}

WORKFLOWS_PLUGIN = pathlib.Path("/home/hermes/.hermes/plugins/hermes-workflows")

# (function, source file) — inspected 2026-09-27; signatures re-read live.
PRIMITIVES = (
    ("act_run", "__init__.py"),
    ("act_wait", "__init__.py"),
    ("act_stop", "__init__.py"),
    ("act_list", "__init__.py"),
    ("write_spawn_record", "wf.py"),
    ("runner_alive", "wfcommon.py"),
)

# What the evidence in those functions supports as durable-ish primitives.
SUPPORTED_PRIMITIVES = (
    "spawn_journal(write_spawn_record)",
    "stop_request_latch(stop.request marker)",
    "runner_liveness(runner_alive)",
    "state_read(act_wait/act_list)",
)

# Admission properties that remain UNKNOWN/unsupported on the installed
# source; each carries the bounded evidence for the verdict.
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

NOTE = "source inspection is not a runtime guarantee"


def _source_signature(func: str, rel: str):
    """Return the installed source signature of `func`, or None if absent."""
    path = WORKFLOWS_PLUGIN / rel
    if not path.is_file():
        return None
    tree = ast.parse(path.read_text(), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func:
            return f"def {func}({ast.unparse(node.args)})"
    return None


def qualify_workflow() -> dict:
    """Inspect the installed Workflow plugin; record supported primitives
    and UNKNOWN/unsupported admission. Does not import or execute plugin code."""
    present = {
        func: {"source": rel, "signature": _source_signature(func, rel)}
        for func, rel in PRIMITIVES
    }
    found = all(v["signature"] for v in present.values())
    return {
        "plugin_path": str(WORKFLOWS_PLUGIN),
        "plugin_found": WORKFLOWS_PLUGIN.is_dir(),
        "functions": present,
        "supported_primitives": list(SUPPORTED_PRIMITIVES) if found else [],
        "admission": {k: v for k, v in ADMISSION_UNKNOWN.items()},
        "qualified_for_work": False,  # no installed evidence supports admission
        "note": NOTE,
    }


def _typed_reject(error: str, reason: str, request=None) -> dict:
    out = {"ok": False, "error": error, "reason": reason,
           "delivery": False, "no_dispatch": True}
    if isinstance(request, dict):
        for k in REQUIRED_KEYS:
            if k in request:
                out[k] = request[k]  # exact preservation even on rejection
    return out


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
        base.update({
            "route": "hermes_session_door",
            "session_link": bool(request.get("session_link")),
            # C5: a session link is navigation only, never execution authority.
            "execution_authority": False,
        })
        return base

    # intent == "work"
    admission = qualify_workflow()
    receipt = request.get("workflow_admission")
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
    return {
        "ok": True, "workspace": workspace, "bead": bead, "intent": intent,
        "status": "admitted",
        "route": "workflow_admission_door",
        "receipt_id": receipt.get("receipt_id"),
        "delivery": False,
        "no_dispatch": True,  # this lane never dispatches; integration owns effects
    }
