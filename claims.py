"""Claim/lease wrapper on native.run_bd + read_model (hbl-pnu.1.2 / N2).

Thin, deliberate: claim, read-back, heartbeat, conditional (CAS) release, and
honest post-claim inspection. NO timers, NO daemon, NO queue, NO close
implementation — heartbeat belongs to the actual dispatch lifetime (the
caller hits heartbeat() at natural tool seams), not to a detached scheduler
this module would have to own.

Every verb here is one fixed-argv bd invocation through native.run_bd (claim)
or read_model (inspection); values are separate argv tokens, never shell
composited. Field values pass through verbatim (statuses/edge types we have
never seen stay untouched).

Native facts this module relies on (pinned bd 1.3.0, f45b249ce — probed via
`bd <cmd> --help` + reports/interop-receipts.json Q1-Q9; nothing invented):
  claim      `bd update <id> --claim --json` — sets assignee=<actor>,
             status=in_progress. Same-actor re-claim: exit 0, lease
             unchanged (idempotent, observed Q2). Distinct-actor on a held
             claim: exit 1 + prose "issue already claimed by X" +
             {"failed":[{id,error}]} naming the holder (observed Q3/Q7).
  read-back  `bd show <id> --json` after EVERY claim mutation; equality of
             assignee/status is asserted, claim success alone is NEVER
             readiness or ownership proof.
  heartbeat  `bd heartbeat <id> --json` — only the current owner may
             heartbeat; pushes lease_expires_at forward. Lease TTL observed
             ~5min; heartbeat lives in an ephemeral node-local table (no
             Dolt commit). Heartbeating a lost/reclaimed claim FAILS so the
             worker learns to stop — surfaced, never swallowed.
  release    `bd unclaim <id> --if-assignee <self>` — the native CAS. Mismatch
             exits 1 (unclaim path, unlike update guards' 13) naming the
             current holder (observed Q8). Holder releases exit 0 (Q9).

DISCLOSED NATIVE LIMITS (not invented around, not advertised as supported):
  * Eligibility-before-claim is NOT atomic. `ready` (via read_model) is an
    advisory read; between it and `--claim` another actor may claim or a new
    blocker may appear. This module therefore INSPECTS blockers/ownership
    AFTER a successful claim and reports honest ambiguity
    (ClaimAmbiguityError with the observed state) instead of pretending a
    guarded claim exists. bd 1.3.0 offers no claim-if-no-new-blockers flag.
  * There is NO conditional claim (--if-* guards cannot combine with
    --claim per `bd update --help`). Release CAS is the ONLY conditional
    primitive; --force is never used here (would let us clobber another
    actor's live claim).
  * `bd unclaim --if-assignee` refuses --force combination and requires a
    non-empty assignee — the CAS release below always passes our own actor.
"""
import os

import native
import read_model

# Observed lease TTL on the pinned binary is ~5min (interop Q-series); the
# caller-facing guidance keeps heartbeat intervals comfortably under TTL/2.
OBSERVED_LEASE_TTL_SECONDS = 300
MAX_HEARTBEAT_INTERVAL_SECONDS = OBSERVED_LEASE_TTL_SECONDS // 2

CLAIM_CONFLICT_MARK = "already claimed by"


class ClaimError(native.NativeError):
    """Named failure in the claim/lease surface."""


class ClaimConflictError(ClaimError):
    """Another actor holds the claim. bd 1.3.0 refuses serially (exit 1,
    failed[] names the holder) — this is contention *observed*, not a
    concurrency guarantee. holder carries the name bd disclosed, if parsed."""

    def __init__(self, issue_id, holder, detail):
        self.issue_id, self.holder, self.detail = issue_id, holder, detail
        super().__init__(
            f"{issue_id}: claim refused — already claimed by {holder!r} "
            f"(never retried blindly; inspect before acting)"
        )


class ClaimAmbiguityError(ClaimError):
    """Claim succeeded yet post-claim inspection found the bead is NOT
    plainly workable: a new open blocker appeared, or ownership moved between
    claim and read-back. The eligibility->claim race is non-atomic by native
    design; this is the honest disclosure, with evidence attached."""

    def __init__(self, issue_id, reasons, evidence):
        self.issue_id, self.reasons, self.evidence = issue_id, reasons, evidence
        super().__init__(
            f"{issue_id}: claimed but ambiguous — " + "; ".join(reasons)
            + " (eligibility-before-claim is non-atomic; not a bug to retry)"
        )


def _parse_holder(exc):
    """Extract the holder name bd discloses in prose/failed[] for a refused
    claim. Returns None when the envelope carries no name — absence is
    reported as unknown, never guessed."""
    blob = " ".join(filter(None, [getattr(exc, "stderr", None),
                                   getattr(exc, "stdout", None)]))
    idx = blob.find(CLAIM_CONFLICT_MARK)
    if idx == -1:
        return None
    rest = blob[idx + len(CLAIM_CONFLICT_MARK):]
    # Prose form: "... already claimed by lab-interop\n{...}" / JSON form:
    # "...already claimed by lab-other2\",...". Take the first token run.
    token = ""
    for ch in rest.lstrip():
        if ch.isalnum() or ch in "-_.@":
            token += ch
        else:
            break
    return token or None


def _holder_from_readback(row, actor):
    """If the bead exists and names an assignee that isn't us, that's the
    holder; empty/None assignee means 'unknown race window', not 'free'."""
    if isinstance(row, dict):
        a = row.get("assignee")
        if a and a != actor:
            return a
    return None


def ready(workspace, issue_id, *, bd_bin="bd", label=None):
    """ADVISORY pre-claim eligibility read — one bounded native `ready` and a
    membership check. Honest limits: (a) this is NOT atomic with claim;
    (b) a bead legitimately leaves ready once claimed, so absence from ready
    before claim means 'not plainly dispatchable' — blockers/ownership are
    only interpretable via inspect_after_claim. Callers must not treat True
    as a claim guarantee."""
    rows = read_model.ready(workspace, bd_bin=bd_bin, label=label)
    return any(r.get("id") == issue_id for r in rows)


def claim(workspace, issue_id, *, actor, bd_bin="bd"):
    """Claim one exact ID with an explicit unique actor, then MANDATORY
    read-back equality, then post-claim inspection (blockers + ownership).

    Returns the read-back row on an unambiguous claim. Raises:
      ClaimConflictError  — another actor holds it (holder name disclosed,
                            never retried blindly);
      ClaimAmbiguityError — claimed yet newly blocked or ownership moved
                            (race disclosed, evidence attached, STOP);
      native named errors — every other bd failure envelope verbatim.
    Same-actor re-claim is idempotent (observed exit 0, lease unchanged) and
    flows through the same read-back path — no special case, no blind retry
    of a conflict."""
    if not actor or not isinstance(actor, str):
        raise ValueError("claim requires an explicit non-empty actor identity")
    try:
        native.run_bd(["update", issue_id, "--claim", "--json"],
                      workspace=workspace, bd_bin=bd_bin, actor=actor)
    except native.BdCommandError as exc:
        holder = _parse_holder(exc)
        if holder and holder != actor:
            # Envelope carried the refusal; the read-back settles the truth.
            back = read_model.show(workspace, issue_id, bd_bin=bd_bin)
            holder = _holder_from_readback(back, actor) or holder
            raise ClaimConflictError(issue_id, holder, str(exc)) from exc
        raise

    back = read_model.show(workspace, issue_id, bd_bin=bd_bin)
    if not back or back.get("assignee") != actor or back.get("status") != "in_progress":
        # Claim "succeeded" yet the store disagrees — report the truth, don't
        # paper over it and don't re-claim.
        holder = _holder_from_readback(back, actor) or "unknown"
        raise ClaimAmbiguityError(
            issue_id,
            [f"read-back after claim shows assignee={back.get('assignee') if back else None!r} "
             f"status={back.get('status') if back else None!r}, expected {actor!r}/in_progress"],
            {"readback": back},
        )
    reasons, evidence = inspect_after_claim(workspace, issue_id, actor=actor,
                                            bd_bin=bd_bin, row=back)
    if reasons:
        raise ClaimAmbiguityError(issue_id, reasons, evidence)
    return back


def inspect_after_claim(workspace, issue_id, *, actor, bd_bin="bd", row=None):
    """Post-claim honesty pass: open blockers + ownership drift.

    Returns (reasons, evidence). reasons empty => claim is plainly workable.
    Non-empty reasons mean the eligibility->claim window was raced (a new
    blocker landed or ownership moved); the caller must STOP and report, not
    retry — bd has no atomic claim-if-unblocked primitive to reach back to.
    Uses bounded read_model reads only; `dep tree`'s [READY] badge is
    non-authoritative (interop fact 8) and deliberately not consulted."""
    if row is None:
        row = read_model.show(workspace, issue_id, bd_bin=bd_bin)
    reasons, evidence = [], {"readback": row}
    if not row:
        return [f"read-back found no row for {issue_id}"], evidence
    if row.get("assignee") != actor or row.get("status") != "in_progress":
        reasons.append(
            f"ownership drift: assignee={row.get('assignee')!r} "
            f"status={row.get('status')!r} expected {actor!r}/in_progress")
    edges = native.run_bd(["dep", "list", issue_id, "--json"],
                          workspace=workspace, bd_bin=bd_bin,
                          readonly=True)[1] or []
    if isinstance(edges, dict):  # tolerate a wrapped shape; records stay verbatim
        edges = edges.get("dependencies") or edges.get("depends_on") or []
    blockers = []
    for dep in edges:
        # Flat array of records; bd also emits flattened "ID: title" strings
        # in some paths — accept both, keep the raw record in evidence.
        dep_id = dep.get("id") if isinstance(dep, dict) else str(dep).split(":", 1)[0].strip()
        dep_type = dep.get("dependency_type") if isinstance(dep, dict) else None
        if not dep_id or dep_type != "blocks":
            continue
        dep_row = read_model.show(workspace, dep_id, bd_bin=bd_bin)
        if dep_row and dep_row.get("status") != "closed":
            blockers.append({"id": dep_id, "status": dep_row.get("status"),
                             "dependency_type": dep_type})
    if blockers:
        reasons.append(f"{len(blockers)} open blocker(s) at post-claim inspection")
    evidence["blockers"] = blockers
    return reasons, evidence


def heartbeat(workspace, issue_id, *, actor, bd_bin="bd"):
    """One lease refresh on the actual dispatch lifetime — the CALLER calls
    this at natural tool seams (interval guidance:
    MAX_HEARTBEAT_INTERVAL_SECONDS); there is deliberately no timer here.
    Owner-only: a lost/reclaimed claim fails with the native envelope so the
    worker learns to stop — never swallowed, never retried into silence."""
    if not actor:
        raise ValueError("heartbeat requires an explicit actor identity")
    _, parsed, _ = native.run_bd(["heartbeat", issue_id, "--json"],
                                 workspace=workspace, bd_bin=bd_bin, actor=actor)
    return parsed


def release(workspace, issue_id, *, actor, reason="", bd_bin="bd"):
    """CAS release: `bd unclaim <id> --if-assignee <self> --json`.

    Conditional (compare-and-swap) release is native-supported ONLY in this
    form; there is no --if-status or lease-token guard on unclaim (probed
    --help) and we never reach for --force. Mismatch exits 1 naming the real
    holder — ClaimConflictError with the holder name, never a silent success
    and never a forced steal."""
    if not actor:
        raise ValueError("release requires the actor identity that must still hold the claim")
    try:
        native.run_bd(["unclaim", issue_id, "--if-assignee", actor,
                       "--json"] + (["--reason", reason] if reason else []),
                      workspace=workspace, bd_bin=bd_bin, actor=actor,
                      expect_json=False)
    except native.BdCommandError as exc:
        holder = _parse_holder(exc)
        if holder is None:
            back = read_model.show(workspace, issue_id, bd_bin=bd_bin)
            holder = _holder_from_readback(back, actor) or "unknown"
        raise ClaimConflictError(issue_id, holder, str(exc)) from exc
    return read_model.show(workspace, issue_id, bd_bin=bd_bin)
