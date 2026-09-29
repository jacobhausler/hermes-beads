"""Bot handoff surface (hbl-pnu.3.5, Ask/Refine half).

Three verbs, zero invented authority:

  ask(workspace, bead, question)
      Read-only bot question scoped to the selected bead context. Context is
      one read_model.show; routing/refusal authority stays with
      interop.submit_request (interop.py:575) — the unqualified session door
      surfaces as its typed refusal (ok:false, error:session_door_unqualified),
      never ok:true. Nothing here opens a session or writes anything.

  refine(workspace, bead, proposed_text)
      The bot PROPOSES a draft change; it lands as a return-to-draft payload
      for the human draft store (desktop/drafts.mjs via desktop/bot_action.mjs
      accept path). The plugin NEVER writes the store directly: no update, no
      comment; the human's accept is the only path to content.

  run_work / work_status / bind_runner_door
      Work control is present-but-disabled with a typed reason until the
      runner binding (hbl-pnu.3.3) is merged. bind_runner_door is the SINGLE
      injection point where an admitted runner door plugs in; until then the
      door is never invoked. When bound, run_work reuses the accepted
      primitives — claims.claim (read-back verified), inspect_after_claim,
      evidence.WorkerSurface — and hands the bead to the door. Handed-off is
      still not delivered: delivery:false, no_dispatch:true ride along.

Stdlib only; no Workflow import; no scheduler or session API invented.
"""
import interop

# The single injection point for the (hbl-pnu.3.3) runner door. Nothing else
# may hold runner state; None => the work surface is present-but-disabled.
_runner_door = None

WORK_DISABLED_REASON = (
    "Work disabled: the runner binding (hbl-pnu.3.3) is not merged; no "
    "admitted runner door is bound via bot_handoff.bind_runner_door. "
    "Ask/Refine remain available; the door is never invoked while disabled."
)


def bind_runner_door(door):
    """Bind (or unbind with None) the admitted runner door — THE injection
    point. Only a callable counts; junk is treated as no door, never as an
    admitted runner."""
    global _runner_door
    _runner_door = door if callable(door) else None
    return {"bound": _runner_door is not None}


def work_status():
    """Render-facing state: present, but enabled only while a door is bound
    AND its qualification precheck passes — an unqualified door is visibly
    unavailable with its typed reason, never a button that fails on click."""
    if _runner_door is None:
        return {"present": True, "enabled": False,
                "disabledReason": WORK_DISABLED_REASON}
    precheck = getattr(_runner_door, "precheck_error", None)
    why = None
    if callable(precheck):
        try:
            why = precheck()
        except Exception as exc:  # a crashing precheck is unqualified
            why = f"runner qualification check failed: {exc}"
    if why:
        return {"present": True, "enabled": False,
                "disabledReason": f"runner_unqualified: {why}"}
    return {"present": True, "enabled": True, "disabledReason": None}


def _base(workspace, bead, intent):
    # exact identity preserved on every response, even rejections
    return {"workspace": workspace, "bead": bead, "intent": intent,
            "delivery": False, "no_dispatch": True}


def _interop_refusal(workspace, bead, intent, session_link=False):
    """Route through interop so its typed verdict is authority — copied onto
    our base verbatim (exact IDs already match; never overwritten)."""
    req = {"workspace": workspace, "bead": bead, "intent": intent}
    if session_link:
        req["session_link"] = True
    out = interop.submit_request(req)
    merged = dict(_base(workspace, bead, intent))
    merged.update(out)
    merged.update({"workspace": workspace, "bead": bead, "intent": intent,
                   "delivery": False, "no_dispatch": True})
    return merged


def ask(workspace, bead, *, question, bd_bin="bd"):
    """Read-only bot question scoped to the selected bead's context."""
    out = _base(workspace, bead, "ask")
    out["read_only"] = True
    if not isinstance(question, str) or not question.strip():
        out.update({"ok": False, "error": "invalid_request",
                    "reason": "question must be a non-empty string",
                    "context": None})
        return out
    import read_model
    try:
        row = read_model.show(workspace, bead, bd_bin=bd_bin)
    except Exception:  # a context read that fails yields no context; the
        row = None     # typed interop refusal stays the authority
    context = None if row is None else {
        "id": row.get("id"), "title": row.get("title"),
        "description": row.get("description"),
        "status": row.get("status"), "design": row.get("design"),
        "acceptance_criteria": row.get("acceptance_criteria"),
    }
    out.update(_interop_refusal(workspace, bead, "ask"))
    out["context"] = context
    out["question"] = question
    out["read_only"] = True
    return out


def refine(workspace, bead, *, proposed_text, bd_bin="bd"):
    """Bot proposes a draft change; it lands ONLY in the human draft store.

    The plugin never writes the store: desktop/bot_action.mjs routes the
    returned draft into createDraftStore().saveDraft and the human accept
    flow owns every subsequent write.
    """
    out = _base(workspace, bead, "refine")
    if not isinstance(proposed_text, str) or not proposed_text.strip():
        out.update({"ok": False, "error": "invalid_request",
                    "reason": "proposed_text must be a non-empty string",
                    "draft": None})
        return out
    import read_model
    try:
        row = read_model.show(workspace, bead, bd_bin=bd_bin)
    except Exception as exc:
        out.update({"ok": False, "error": "context_unavailable",
                    "reason": str(exc), "draft": None})
        return out
    if row is None:
        out.update({"ok": False, "error": "bead_not_found", "draft": None})
        return out
    out.update(_interop_refusal(workspace, bead, "refine"))
    out["draft"] = {
        "beadId": bead,
        "text": proposed_text,
        "baseText": row.get("description") or "",
        "provenance": "bot",
        "requires_human_accept": True,
        "neverWritesStore": True,
    }
    # Return-to-draft path: how a human gets BACK to this proposal (and how
    # bot_action.mjs re-opens the draft in the human draft store).
    out["return_to_draft"] = {"action": "reopen_draft",
                              "workspace": workspace, "bead": bead}
    out["no_dispatch"] = True
    return out


def run_work(workspace, bead, *, actor, bd_bin="bd", request_key=None):
    """Hand a claimed bead to the admitted runner door — only while bound.

    request_key is the click's idempotency key: the door replays a
    duplicate click to the SAME admission (one receipt, one worker). When
    the bound door exposes a qualification precheck, it runs BEFORE the
    claim: an unqualified runner never touches the store (no claim, no
    fake success)."""
    out = _base(workspace, bead, "work")
    if _runner_door is None:
        out.update({"ok": False, "error": "work_surface_disabled",
                    "reason": WORK_DISABLED_REASON})
        return out
    precheck = getattr(_runner_door, "precheck_error", None)
    if callable(precheck):
        try:
            why = precheck()
        except Exception as exc:            # a broken door is unqualified
            why = str(exc)
        if why:
            out.update({"ok": False, "error": "runner_unqualified",
                        "reason": why, "handed_off": False})
            return out
    admission = getattr(_runner_door, "admission_error", None)
    if callable(admission):
        try:
            why = admission(bead, request_key, workspace)
        except Exception as exc:            # a broken check refuses
            why = f"admission check failed: {exc}"
        if why:
            # decided BEFORE the claim: nothing touched the store
            out.update({"ok": False, "error": "door_refused",
                        "reason": why, "handed_off": False,
                        "claimed": False})
            return out
    import claims
    try:
        row = claims.claim(workspace, bead, actor=actor, bd_bin=bd_bin)
    except claims.ClaimConflictError as exc:
        out.update({"ok": False, "error": "claim_conflict",
                    "holder": exc.holder, "reason": str(exc)})
        return out
    except claims.ClaimAmbiguityError as exc:
        out.update({"ok": False, "error": "claim_ambiguous",
                    "reasons": exc.reasons, "evidence": exc.evidence})
        return out
    reasons, evidence_pack = claims.inspect_after_claim(
        workspace, bead, actor=actor, bd_bin=bd_bin, row=row)
    if reasons:
        out.update({"ok": False, "error": "post_claim_ambiguity",
                    "reasons": reasons, "evidence": evidence_pack})
        return out
    import evidence as _evidence
    payload = {"workspace": workspace, "bead": bead, "actor": actor,
               "row": row, "reasons": reasons,
               "request_key": request_key,
               "surface": _evidence.WorkerSurface(
                   workspace, actor=actor, bd_bin=bd_bin)}
    try:
        result = _runner_door(payload)
    except Exception as exc:
        # the door refused AFTER the claim: typed refusal, never a fake
        # success — the claim stands for the human to see (nothing was
        # dispatched; delivery stays false either way)
        out.update({"ok": False, "error": "door_refused",
                    "reason": str(exc), "handed_off": False})
        return out
    out.update({"ok": True, "error": None,
                "handed_off": isinstance(result, dict)
                and bool(result.get("handed_off")),
                "door_result": result})
    return out
