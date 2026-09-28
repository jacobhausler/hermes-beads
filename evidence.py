"""Evidence notes + authorization-aware closure separation (hbl-pnu.1.4).

Two surfaces, deliberately apart:

  * WorkerSurface (worker actor): append-only ACCEPTANCE-EVIDENCE comments
    (bd comments add — append-only, no edit/delete exists: interop U1) plus
    optional guarded notes via write_protocol. A worker that asks for closure
    gets WorkerClosureRefusedError + an append-only REQUEST-CLOSURE comment;
    NO close/reopen argv is ever built by the worker surface (enforced by an
    AST audit in tests/test_evidence.py: close/reopen argv tokens live ONLY
    in the authorized_* verbs below).

  * authorized_close / authorized_reopen (owner/parent actor): closure policy
    belongs to CALLER AUTHORIZATION, not a hardcoded human-only role (owner
    ruling). A close here is refused unless ALL hold, exactly scoped to this
    store / this bead / this attempt:
      - a non-empty `authorization` string supplied by the caller;
      - a non-empty `reason` that cites every named artifact;
      - a real evidence comment on THIS bead (read via the comments API —
        show does not embed comment bodies) authored by `evidence_actor`
        carrying `attempt=<attempt>` and citing each artifact.
    After the native close the read-back (bd show --json + comments API)
    MUST show status=closed, closed_at, close_reason==reason, and the
    evidence comment still present; any other shape raises
    ClosureAmbiguityError — never a green success on exit 0 alone.

Boundary honesty (pinned bd 1.3.0, f45b249ce):
  * Native `bd close` accepts an empty reason — the non-empty, artifact-
    citing reason is enforced HERE, in the plugin surface. Native bd close
    remains available and is never patched to enforce plugin policy; this
    module separates the surfaces, it does not sandbox anyone and claims no
    scheduler and no role ACL.
  * Epic closure is inspected only via `bd epic status <id> --json` and
    `bd epic close-eligible --dry-run --json` within the authorized scope —
    never a global sweep (the non-dry-run verb is never built anywhere).
No SQL, no shadow store, no plugin-minted IDs, no --force.
"""
import claims
import native
import read_model
import write_protocol

EVIDENCE_PREFIX = "EVIDENCE"
REQUEST_PREFIX = "REQUEST-CLOSURE"

CLOSE_REFUSAL = (
    "worker surface never closes: closure is the parent's verified act on "
    "the evidence you appended")


class EvidenceError(native.NativeError):
    """Named failure in the evidence/closure surface."""


class WorkerClosureRefusedError(EvidenceError):
    """A worker-role call attempted closure. The plugin surface refuses —
    the worker records evidence; the authorized parent verifies and closes.
    This is surface separation, not a security boundary or an ACL."""


class ClosureRefusedError(EvidenceError):
    """An authorized-surface precondition failed (missing authorization,
    empty/non-citing reason, no matching evidence). Nothing was written."""


class ClosureAmbiguityError(EvidenceError):
    """The close ran but the read-back does not plainly show a properly
    evidenced closure. Success is NOT claimed; the observed evidence is
    attached for the caller to reconcile."""


def evidence_comment_text(attempt, artifacts, summary=""):
    """The machine-checkable append-only envelope: EVIDENCE attempt=<a>
    artifacts=<;sep> [summary]."""
    if not attempt or not str(attempt).strip():
        raise ValueError("evidence requires a non-empty attempt identity")
    if not artifacts:
        raise ValueError("evidence requires at least one cited artifact")
    text = (f"{EVIDENCE_PREFIX} attempt={attempt} "
            f"artifacts={';'.join(artifacts)}")
    if summary:
        text += f" summary={summary}"
    return text


def _find_evidence_comment(workspace, issue_id, *, evidence_actor, attempt,
                           artifacts, bd_bin):
    """Comments API (NOT show — show does not embed comments), exact-scope
    match: same store (workspace), same bead (issue_id), author=
    evidence_actor, contains attempt=<attempt>, cites every artifact.
    Returns the comment or None; a failed comments read raises honestly."""
    rows = read_model.comments(workspace, issue_id, bd_bin=bd_bin)
    token = f"attempt={attempt}"
    for c in rows:
        text = c.get("text") or ""
        if (c.get("author") == evidence_actor
                and text.startswith(EVIDENCE_PREFIX)
                and token in text
                and all(a in text for a in artifacts)):
            return c
    return None


class WorkerSurface:
    """Worker-side calls only: append evidence, heartbeat-adjacent notes,
    REQUEST closure. There is deliberately no close/reopen method here."""

    def __init__(self, workspace, *, actor, bd_bin="bd"):
        if not actor or not str(actor).strip():
            raise ValueError("WorkerSurface requires an explicit actor")
        self.workspace, self.actor, self.bd_bin = workspace, actor, bd_bin

    def record_evidence(self, issue_id, *, attempt, artifacts, summary="",
                        notes=None, if_status="in_progress"):
        """Append-only evidence comment; optional notes via the guarded
        write protocol (worker owns the claim or gets WriteStaleError)."""
        text = evidence_comment_text(attempt, artifacts, summary)
        rows = write_protocol.append_comment(
            self.workspace, issue_id, actor=self.actor, bd_bin=self.bd_bin,
            text=text)
        result = {"issue_id": issue_id, "evidence_comment": text,
                  "comments": rows}
        if notes is not None:
            result["notes_row"] = write_protocol.update_fields(
                self.workspace, issue_id, actor=self.actor,
                bd_bin=self.bd_bin, if_assignee=self.actor,
                if_status=if_status, fields={"notes": notes})
        return result

    def request_closure(self, issue_id, *, detail=""):
        """The honest worker hand-off: append-only REQUEST-CLOSURE comment,
        then the refusal. Never issues close itself."""
        write_protocol.append_comment(
            self.workspace, issue_id, actor=self.actor, bd_bin=self.bd_bin,
            text=f"{REQUEST_PREFIX} by {self.actor}: {CLOSE_REFUSAL}"
                 + (f" detail={detail}" if detail else ""))
        raise WorkerClosureRefusedError(f"{issue_id}: {CLOSE_REFUSAL}")


def authorized_close(workspace, issue_id, *, actor, authorization, reason,
                     evidence_actor, attempt, artifacts, bd_bin="bd"):
    """CLOSE argv lives ONLY here (and in authorized_reopen's reopen argv).
    Requires explicit caller authorization + non-empty artifact-citing
    reason + a matching evidence comment on this exact store/bead/attempt.
    Success only on verified read-back; anything else is ambiguity."""
    if not authorization or not str(authorization).strip():
        raise ClosureRefusedError(
            "closure requires an explicit non-empty authorization from the "
            "caller (policy belongs to caller authorization; nothing is "
            "authorized by default)")
    if not reason or not str(reason).strip():
        raise ClosureRefusedError(
            "close refused: non-empty --reason required (native bd accepts "
            "an empty reason; the plugin surface does not)")
    if not artifacts:
        raise ClosureRefusedError("close refused: no artifacts cited")
    missing_cite = [a for a in artifacts if a not in reason]
    if missing_cite:
        raise ClosureRefusedError(
            f"close refused: reason must cite every artifact; missing "
            f"{missing_cite}")
    if not issue_id or not isinstance(issue_id, str):
        raise ClosureRefusedError("close refused: exact issue ID required")
    ev = _find_evidence_comment(workspace, issue_id,
                                evidence_actor=evidence_actor,
                                attempt=attempt, artifacts=artifacts,
                                bd_bin=bd_bin)
    if ev is None:
        raise ClosureRefusedError(
            f"close refused on {issue_id}: no evidence comment in this "
            f"store/bead authored by {evidence_actor!r} carrying "
            f"attempt={attempt} citing {artifacts}")
    # Native ownership gate (pinned bd 1.3.0): close is refused unless the
    # closer IS the assignee ("reclaim or use --force"). --force is banned
    # here, so the hand-off is cooperative and CAS-guarded: release the
    # worker's claim under its own --if-assignee, then the authorized actor
    # claims atomically before closing. No takeover, no clobber.
    row = read_model.show(workspace, issue_id, bd_bin=bd_bin)
    if not row:
        raise ClosureRefusedError(f"close refused: {issue_id} not found")
    holder = row.get("assignee") or ""
    if holder and holder != actor:
        try:
            claims.release(workspace, issue_id, actor=holder, bd_bin=bd_bin)
        except native.NativeError as exc:
            raise ClosureRefusedError(
                f"close refused on {issue_id}: cooperative release of "
                f"holder {holder!r} failed ({exc}); no forced takeover") from exc
        if (read_model.show(workspace, issue_id, bd_bin=bd_bin) or {}) \
                .get("assignee"):
            raise ClosureRefusedError(
                f"close refused on {issue_id}: still held after release")
    claims.claim(workspace, issue_id, actor=actor, bd_bin=bd_bin)
    native.run_bd(["close", issue_id, "--reason", reason, "--json"],
                  workspace=workspace, bd_bin=bd_bin, actor=actor,
                  expect_json=False)
    return verify_closure(workspace, issue_id, reason=reason,
                          evidence_actor=evidence_actor, attempt=attempt,
                          artifacts=artifacts, bd_bin=bd_bin)


def authorized_reopen(workspace, issue_id, *, actor, authorization, reason,
                      bd_bin="bd"):
    """REOPEN argv lives ONLY here, same explicit-authorization gate."""
    if not authorization or not str(authorization).strip():
        raise ClosureRefusedError("reopen requires explicit authorization")
    if not reason or not str(reason).strip():
        raise ClosureRefusedError("reopen refused: non-empty reason required")
    if not issue_id or not isinstance(issue_id, str):
        raise ClosureRefusedError("reopen refused: exact issue ID required")
    native.run_bd(["reopen", issue_id, "--reason", reason, "--json"],
                  workspace=workspace, bd_bin=bd_bin, actor=actor,
                  expect_json=False)
    row = read_model.show(workspace, issue_id, bd_bin=bd_bin)
    if not row or row.get("status") != "open":
        raise ClosureAmbiguityError(
            f"reopen of {issue_id} ambiguous: read-back {row!r}")
    return {"issue_id": issue_id, "row": row, "readback_verified": True}


def verify_closure(workspace, issue_id, *, reason, evidence_actor, attempt,
                   artifacts, bd_bin="bd"):
    """Verifier read-back: bd show --json (closed_at, close_reason) AND the
    evidence comment via the comments API. Every field must be plainly
    present and equal — otherwise ClosureAmbiguityError, never success."""
    row = read_model.show(workspace, issue_id, bd_bin=bd_bin)
    if not row:
        raise ClosureAmbiguityError(
            f"closure of {issue_id} ambiguous: empty read-back")
    problems = []
    if row.get("status") != "closed":
        problems.append(f"status={row.get('status')!r}")
    if not row.get("closed_at"):
        problems.append("closed_at missing")
    if row.get("close_reason") != reason:
        problems.append(f"close_reason={row.get('close_reason')!r}")
    ev = _find_evidence_comment(workspace, issue_id,
                                evidence_actor=evidence_actor,
                                attempt=attempt, artifacts=artifacts,
                                bd_bin=bd_bin)
    if ev is None:
        problems.append("evidence comment absent on read-back")
    if problems:
        raise ClosureAmbiguityError(
            f"closure of {issue_id} ambiguous: {'; '.join(problems)} "
            f"(success NOT claimed; reconcile)")
    return {"issue_id": issue_id, "row": row, "evidence_comment": ev,
            "readback_verified": True}


def inspect_epic_closure_eligibility(workspace, epic_id, *, bd_bin="bd"):
    """READ-ONLY epic inspection within authorized scope: `bd epic status
    <id> --json` + `bd epic close-eligible --dry-run --json`. The
    non-dry-run close-eligible verb (a global sweep) is NEVER built by this
    module — asserted by the AST audit in the tests."""
    if not epic_id or not isinstance(epic_id, str):
        raise ValueError("epic inspection requires an exact epic ID")
    _, status, _ = native.run_bd(["epic", "status", epic_id, "--json"],
                                 workspace=workspace, bd_bin=bd_bin,
                                 readonly=True)
    _, preview, _ = native.run_bd(
        ["epic", "close-eligible", "--dry-run", "--json"],
        workspace=workspace, bd_bin=bd_bin, readonly=True)
    return {"epic_id": epic_id, "status": status, "eligible_preview": preview,
            "sweep_issued": False}
