"""hermes-beads backend plugin.

Thin handlers over the beads/ package: one smoke tool plus read/claim/guarded
write verbs. All bd access rides the fixed-argv runner in beads/native.py —
no shell, no state mirroring. Closing work stays with plain `bd close`: the
escape hatch is the product.
"""
import json
import os
import shutil

from .beads import claims, native, read_model, write_protocol


def _fail(exc):
    # Honest failure: named error, never an empty list.
    return json.dumps({"ok": False, "error_type": type(exc).__name__,
                       "error": str(exc)})


def _text(args, key):
    v = args.get(key)
    return v if isinstance(v, str) and v else None


def _store(args):
    return _text(args or {}, "workspace")


def _bd():
    # The bd executable is operator config, never a tool argument: a
    # model-chosen argv[0] would run any program with no approval.
    return os.environ.get("HERMES_BEADS_BD_BIN") or shutil.which("bd") or "bd"


def handle_beads_smoke(args):
    workspace = _store(args)
    if not workspace:
        return json.dumps({"error": "workspace must be an explicit absolute path to a bd store"})
    try:
        return json.dumps(native.smoke(workspace, bd_bin=_bd(),
                                       label=(args or {}).get("label")))
    except (native.NativeError, ValueError) as exc:
        # ValueError: fixed-argv/actor gate or field-group refusal — an
        # honest named failure, never an escape out of the tool handler.
        return _fail(exc)


def handle_beads_frontier(args):
    workspace = _store(args)
    if not workspace:
        return json.dumps({"error": "workspace must be an explicit absolute path to a bd store"})
    try:
        rows = read_model.ready(workspace, label=(args or {}).get("label"),
                                bd_bin=_bd())
        return json.dumps({"ok": True, "ready": rows})
    except (native.NativeError, ValueError) as exc:
        return _fail(exc)


def handle_beads_show(args):
    workspace, bead = _store(args), _text(args or {}, "bead")
    if not workspace or not bead:
        return json.dumps({"error": "workspace and bead are required"})
    try:
        return json.dumps({"ok": True, "bead": read_model.show(
            workspace, bead, bd_bin=_bd())})
    except (native.NativeError, ValueError) as exc:
        return _fail(exc)


def handle_beads_claim(args):
    args = args or {}
    workspace, bead, actor = _store(args), _text(args, "bead"), _text(args, "actor")
    if not (workspace and bead and actor):
        return json.dumps({"error": "workspace, bead and actor are required"})
    try:
        row = claims.claim(workspace, bead, actor=actor, bd_bin=_bd())
        return json.dumps({"ok": True, "bead": row})
    except claims.ClaimConflictError as exc:
        return json.dumps({"ok": False, "conflict": True,
                           "holder": getattr(exc, "holder", None),
                           "error": str(exc)})
    except (native.NativeError, ValueError) as exc:
        return _fail(exc)


def handle_beads_update(args):
    args = args or {}
    workspace, bead, actor = _store(args), _text(args, "bead"), _text(args, "actor")
    fields = args.get("fields")
    if not (workspace and bead and actor and isinstance(fields, dict) and fields):
        return json.dumps({"error": "workspace, bead, actor and a non-empty fields object are required"})
    try:
        rec = write_protocol.update_fields(
            workspace, bead, actor=actor, bd_bin=_bd(),
            if_assignee=args.get("if_assignee"), if_status=args.get("if_status"),
            fields=fields)
        return json.dumps(rec)
    except write_protocol.WriteStaleError as exc:
        # exit 13: the guard lost the race — STALE, nothing written, never retried.
        return json.dumps({"ok": False, "stale": True,
                           "holder": getattr(exc, "holder", None),
                           "error": str(exc)})
    except (native.NativeError, ValueError) as exc:
        # ValueError from update_fields (field-group allowlist) or the
        # argv/actor gate: an honest named failure, never an escape.
        return _fail(exc)


def handle_beads_comment(args):
    args = args or {}
    workspace, bead, actor = _store(args), _text(args, "bead"), _text(args, "actor")
    text = _text(args, "text")
    if not (workspace and bead and actor and text):
        return json.dumps({"error": "workspace, bead, actor and text are required"})
    try:
        rows = write_protocol.append_comment(workspace, bead, actor=actor,
                                             text=text, bd_bin=_bd())
        return json.dumps({"ok": True, "comments": rows})
    except (native.NativeError, ValueError) as exc:
        return _fail(exc)


_STORE = {"type": "string",
          "description": "Absolute canonical bd store dir (contains .beads/)"}


def _schema(desc, props, required):
    return {"description": desc,
            "parameters": {"type": "object", "properties": props,
                           "required": required}}


TOOLS = [
    ("beads_smoke",
     _schema("Smoke-check one bd store: installed bd version, store identity, and a bounded ready frontier (epics excluded).",
             {"workspace": _STORE, "label": {"type": "string"}},
             ["workspace"]),
     handle_beads_smoke),
    ("beads_frontier",
     _schema("The scoped ready frontier for one store (blocked and claimed rows excluded, epics excluded). Optional label scope.",
             {"workspace": _STORE, "label": {"type": "string"}},
             ["workspace"]),
     handle_beads_frontier),
    ("beads_show",
     _schema("Read one bead verbatim as bd sees it (status, assignee, notes, deps, metadata).",
             {"workspace": _STORE, "bead": {"type": "string"}},
             ["workspace", "bead"]),
     handle_beads_show),
    ("beads_claim",
     _schema("Claim a bead for one actor, read back after. A held claim refuses honestly and names the holder — never steals.",
             {"workspace": _STORE, "bead": {"type": "string"},
              "actor": {"type": "string"}},
             ["workspace", "bead", "actor"]),
     handle_beads_claim),
    ("beads_update",
     _schema("Guarded metadata update over the allowed field group (priority, notes, due, estimate, external_ref, defer — nothing else, notably NOT status/title/description) under the required --if-assignee/--if-status guard pair: a stale guard reports stale=true and writes nothing. Replacing description/title is unsupported by design — edit content with bd directly.",
             {"workspace": _STORE, "bead": {"type": "string"},
              "actor": {"type": "string"},
              "if_assignee": {"type": "string"}, "if_status": {"type": "string"},
              "fields": {"type": "object"}},
             ["workspace", "bead", "actor", "fields"]),
     handle_beads_update),
    ("beads_comment",
     _schema("Append a comment to a bead (append-only — bd has no comment edit/delete).",
             {"workspace": _STORE, "bead": {"type": "string"},
              "actor": {"type": "string"}, "text": {"type": "string"}},
             ["workspace", "bead", "actor", "text"]),
     handle_beads_comment),
]


def register(ctx):
    for name, schema, handler in TOOLS:
        ctx.register_tool(name=name, toolset="beads", schema=schema,
                          handler=handler)
