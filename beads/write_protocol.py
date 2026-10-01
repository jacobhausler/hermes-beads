"""Guarded-write protocol — the worker-side write discipline.

Every field-group write goes through exactly one fixed-argv `bd update`
carrying BOTH native guards --if-assignee <self> and --if-status <expected>.
A stale guard (exit 13 and/or guard_mismatch:true in the JSON failure
envelope) means NOTHING was written and another actor won the race: this
module maps that to WriteStaleError = STALE — the exception carries a fresh
re-read of the bead; the caller aborts and tells the parent (see
report_stale_to_parent). The same guarded argv is NEVER retried: per native
update help, "retrying the same guard is pointless".

Capability honesty (pinned bd 1.3.0, f45b249ce; `bd update --help` + interop
receipts R1/U4/Q-series):
  * --if-assignee / --if-status protect ONLY those two fields' expectations.
    They do NOT protect concurrent content: two guarded edits of the same
    description while owner/status hold is native last write wins. No
    updated_at/revision preflight is presented as CAS here (owner ruling: a
    read-then-write is not atomic) and none is implemented.
  * Replacement content (description/title) has NO native guard flag
    (no --if-revision exists): the replacement verbs below are labelled
    EXPLICIT-UNSUPPORTED and DISABLED by default. The opt-in per-call flag
    exists to demonstrate/allow isolated writes; it never promises
    no-lost-update, and it still requires the ownership guards.
  * Append-only comments (bd comments add) are a separate, append-only path:
    no update guard flags exist or are needed there.
  * Commit granularity = one bd command per field-group (one update argv per
    group); no batched mega-writes, no --force anywhere, no close here
    (closure is the parent's verified act).
"""
import json

from . import native
from . import read_model

GUARD_IF_ASSIGNEE = "--if-assignee"
GUARD_IF_STATUS = "--if-status"
WRITE_UPDATE = "update"
WRITE_COMMENTS_ADD = "comments"

# Field-group allowlist for guarded writes: one `bd update` carries the
# guard pair + these tokens. description/title are deliberately absent —
# replacement content lives behind the explicit-unsupported verbs.
FIELD_FLAGS = {
    "priority": "--priority",
    "notes": "--notes",
    "due": "--due",
    "estimate": "--estimate",
    "external_ref": "--external-ref",
    "defer": "--defer",
}

REPLACEMENT_FIELDS = ("description", "title")


class WriteStaleError(native.NativeError):
    """STALE: the assignee/status guard no longer held (exit 13 /
    guard_mismatch:true). Nothing was written. The same guarded argv is
    NEVER retried — carry the fresh re-read, abort, tell the parent via
    report_stale_to_parent()."""

    def __init__(self, issue_id, exit_code, guard_mismatch, argv, fresh,
                 detail):
        self.issue_id = issue_id
        self.exit_code = exit_code
        self.guard_mismatch = guard_mismatch
        self.argv = list(argv)
        self.fresh = fresh          # re-read state at detection time
        self.holder = (fresh or {}).get("assignee") or ""
        self.status = (fresh or {}).get("status")
        super().__init__(
            f"STALE write on {issue_id}: guard mismatch (exit {exit_code}, "
            f"guard_mismatch={guard_mismatch}); nothing written; NOT retried. "
            f"current holder={self.holder!r} status={self.status}. {detail}")


class ExplicitUnsupportedError(native.NativeError):
    """This write class has no native guard flag (no content CAS exists).
    Refused by default; requires a per-call allow_unsupported=True opt-in
    and still never promises no-lost-update."""


def _envelope_flag_guard_mismatch(stdout_text):
    """Parse guard_mismatch:true from the bd failure envelope, if present."""
    start = (stdout_text or "").find("{")
    if start < 0:
        return False
    try:
        env = json.loads(stdout_text[start:])
    except json.JSONDecodeError:
        return False
    for f in env.get("failed", []) or []:
        if f.get("guard_mismatch") is True:
            return True
    return False


def _validate_guard_pair(if_assignee, if_status):
    if if_assignee is None or if_status is None:
        raise ValueError(
            "guarded writes require BOTH if_assignee and if_status — "
            "a field write without the native guard pair is refused "
            "(no unguarded write path exists in this module)")


def _guarded_update(workspace, issue_id, field_tokens, *, actor, bd_bin,
                    if_assignee, if_status):
    """ONE `bd update` argv: the guard pair + one field-group. Fixed argv,
    actor bound, no expect_json parsing dependency for the mutation itself;
    verification is the mandatory read-back below."""
    _validate_guard_pair(if_assignee, if_status)
    # Literal argv keeps the static code-path audit (tests/) able to see
    # every token of every write call site.
    guard_tokens = [GUARD_IF_ASSIGNEE, if_assignee,
                    GUARD_IF_STATUS, if_status]
    argv = [WRITE_UPDATE, issue_id] + guard_tokens + ["--json"] \
        + list(field_tokens)
    try:
        native.run_bd(argv, workspace=workspace, bd_bin=bd_bin, actor=actor,
                      expect_json=False)
    except native.GuardMismatchError as exc:
        stale = _envelope_flag_guard_mismatch(exc.stdout) or exc.exit_code == 13
        fresh = read_model.show(workspace, issue_id, bd_bin=bd_bin)
        raise WriteStaleError(issue_id, exc.exit_code, bool(stale),
                              [WRITE_UPDATE, issue_id],
                              fresh, exc.stderr.strip()[:300]) from exc
    # Mandatory read-back: the caller gets the re-read row, verified.
    row = read_model.show(workspace, issue_id, bd_bin=bd_bin)
    if row is None:
        raise native.BdCommandError([WRITE_UPDATE, issue_id], 0,
                                    f"read-back of {issue_id} empty")
    return row


def _verify(row, fields):
    return all(str(row.get(k)) == str(v) for k, v in fields.items())


def update_fields(workspace, issue_id, *, actor, bd_bin="bd",
                  if_assignee=None, if_status=None, fields):
    """Guarded field-group write: one bd update, both guards, read-back.

    fields keys are limited to FIELD_FLAGS (priority/notes/due/estimate/
    external_ref/defer). description/title are NOT accepted here — they are
    replacement content: use replace_description/replace_title, which are
    EXPLICIT-UNSUPPORTED by default. Raises WriteStaleError on guard
    mismatch — never retries, abort and tell the parent.
    """
    if not fields:
        raise ValueError("empty field-group: nothing to write")
    illegal = set(fields) - set(FIELD_FLAGS)
    if illegal or not fields:
        raise ValueError(
            f"fields outside the guarded field-group allowlist: "
            f"{sorted(illegal)} (replacement content goes through the "
            f"explicit-unsupported verbs)")
    tokens = []
    for key, value in fields.items():
        tokens += [FIELD_FLAGS[key], str(value)]
    row = _guarded_update(workspace, issue_id, tokens, actor=actor,
                          bd_bin=bd_bin, if_assignee=if_assignee,
                          if_status=if_status)
    ok = _verify(row, fields)
    if not ok:
        raise native.BdCommandError(
            ["update", issue_id], 0,
            f"read-back mismatch after guarded write on {issue_id}: {row}")
    row = dict(row)
    row["readback_verified"] = True
    return row


def replace_description(workspace, issue_id, *, actor, bd_bin="bd",
                        description, if_assignee=None, if_status=None,
                        allow_unsupported=False):
    """EXPLICIT-UNSUPPORTED: description replacement has NO native content
    guard (no --if-revision exists), so concurrent guarded edits of the same
    description are native last write wins. This verb is disabled by
    default; a matching preflight read would NOT make it CAS. The per-call
    allow_unsupported=True opt-in writes under the ownership guard pair
    only — it is guarded, it is NOT CAS, and it makes no no-loss promise.
    Prefer append_comment (append-only, separate path) or upstream N6
    capability."""
    return _replacement(workspace, issue_id, "--description", description,
                        field="description", value=description, actor=actor,
                        bd_bin=bd_bin, if_assignee=if_assignee,
                        if_status=if_status, allow=allow_unsupported)


def replace_title(workspace, issue_id, *, actor, bd_bin="bd",
                  title, if_assignee=None, if_status=None,
                  allow_unsupported=False):
    """EXPLICIT-UNSUPPORTED: title replacement, same capability status as
    replace_description — no native content guard exists; guarded, NOT CAS,
    last write wins under concurrency; disabled by default."""
    return _replacement(workspace, issue_id, "--title", title,
                        field="title", value=title, actor=actor,
                        bd_bin=bd_bin, if_assignee=if_assignee,
                        if_status=if_status, allow=allow_unsupported)


def _replacement(workspace, issue_id, flag, text, *, field, value, actor,
                 bd_bin, if_assignee, if_status, allow):
    if not allow:
        raise ExplicitUnsupportedError(
            f"{field} replacement is EXPLICIT-UNSUPPORTED: bd 1.3.0 has no "
            f"content guard flag (no --if-revision); guarded replacement is "
            f"last write wins, NOT CAS. Disabled by default — a per-call "
            f"allow_unsupported=True is required and still makes no "
            f"lost-update promise.")
    row = _guarded_update(workspace, issue_id, [flag, text], actor=actor,
                          bd_bin=bd_bin, if_assignee=if_assignee,
                          if_status=if_status)
    ok = str(row.get(field)) == str(value)
    if not ok:
        raise native.BdCommandError(
            ["update", issue_id], 0,
            f"read-back mismatch after {field} replacement on {issue_id}")
    row = dict(row)
    row["readback_verified"] = True
    return row


def append_comment(workspace, issue_id, *, actor, bd_bin="bd", text):
    """Append-only comment: the separate, guardless path (native append
    semantics; no update guard flags exist for comments and none is faked).
    One bd command, text passed as its own argv token."""
    if not text or not text.strip():
        raise ValueError("empty comment text")
    # "--": text is positional even when it starts with a dash (probed bd 1.3.0:
    # a bare "--db=/x" comment was parsed as a flag and refused).
    native.run_bd([WRITE_COMMENTS_ADD, "add", issue_id, "--", text],
                  workspace=workspace, bd_bin=bd_bin, actor=actor,
                  expect_json=False)
    rows = read_model.comments(workspace, issue_id, bd_bin=bd_bin)
    return rows


def report_stale_to_parent(workspace, err, *, parent_id, actor, bd_bin="bd"):
    """Tell the parent bead about a STALE abort: an append-only comment
    carrying the exact bead ID, exit 13, guard_mismatch and the current
    holder from the re-read. This is the honest handoff — no retry, no
    silent swallow, and never a close (closure is the parent's verified
    act)."""
    if not isinstance(err, WriteStaleError):
        raise ValueError("report_stale_to_parent requires a WriteStaleError")
    text = (f"STALE write aborted on {err.issue_id}: exit {err.exit_code}, "
            f"guard_mismatch:{str(err.guard_mismatch).lower()}; nothing "
            f"written, never retried; current holder={err.holder!r} "
            f"status={err.status}. Worker actor={actor} re-read and stopped "
            f"per guarded-write protocol.")
    return append_comment(workspace, parent_id, actor=actor, bd_bin=bd_bin,
                          text=text)
