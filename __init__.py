"""hermes-beads backend plugin — initial smoke door (hbl-pnu.1.6 / N0).

One tool proves the boundary end to end: version + store identity + bounded
ready frontier against an explicitly supplied canonical workspace. All real
work goes through native.py's fixed-argv runner; nothing here shells out,
imports Workflow, or mirrors graph state.
"""
import json
import os

import native

TOOL_SCHEMA = {
    "description": (
        "Smoke-check the native Beads backend for one store: installed bd version, "
        "canonical workspace identity, and a bounded ready frontier (epics excluded). "
        "workspace must be an absolute path to the bd store directory; actor is an "
        "explicit audit identity."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "workspace": {"type": "string", "description": "Absolute canonical bd store dir (contains .beads/)"},
            "label": {"type": "string", "description": "Optional label scoping for the ready frontier"},
            "bd_bin": {"type": "string", "description": "Explicit bd binary path (default: 'bd' on PATH)"},
        },
        "required": ["workspace"],
    },
}


def handle_beads_smoke(args):
    args = args or {}
    workspace = args.get("workspace")
    if not isinstance(workspace, str) or not workspace:
        return {"error": "workspace must be an explicit absolute path to a bd store"}
    try:
        receipt = native.smoke(
            workspace,
            bd_bin=args.get("bd_bin") or "bd",
            label=args.get("label"),
        )
        return json.dumps(receipt)
    except native.NativeError as exc:
        # Honest failure: named error, never an empty list.
        return json.dumps({"ok": False,
                           "error_type": type(exc).__name__,
                           "error": str(exc)})


def register(ctx):
    ctx.register_tool(name="beads_smoke", toolset="beads",
                      schema=TOOL_SCHEMA, handler=handle_beads_smoke)
