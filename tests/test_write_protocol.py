#!/usr/bin/env python3
"""Guarded-write protocol tests ().

Verdict under test — bd 1.3.0 (f45b249ce) exposes exactly two conditional
guards on `bd update` (--if-assignee / --if-status). A stale guard writes
NOTHING and exits 13 with guard_mismatch:true in the JSON envelope (interop
receipts R1-if-status-mismatch / U4-if-assignee-mismatch). The protocol must
map that to STALE: re-read, abort, tell parent, NEVER retry the same guard.
There is NO --if-revision / content guard: replacement-content writes
(description/title) are labelled explicit-unsupported and disabled by
default — never a read-then-write advertised as no-lost-update.
Append-only comments are separate and need no (nonexistent) guard flags.

Real native evidence, no mocks: every case runs the ACTUAL pinned bd against
a fresh disposable fixture store under tests/.write-fixtures (gitignored;
each fixture owns its own `git init` so the embedded-dolt home never falls
through to the lab repo's databases — the recorded trap). The planning store
is NEVER touched here.

Run: python3 tests/test_write_protocol.py
"""
import ast
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from beads import native, claims, write_protocol  # noqa: E402

BD_BIN = os.environ.get("BEADS_LAB_BD",
                        "bd")
FIXTURE_ROOT = os.path.join(HERE, ".write-fixtures")
os.makedirs(FIXTURE_ROOT, exist_ok=True)


def make_store():
    os.makedirs(FIXTURE_ROOT, exist_ok=True)
    d = tempfile.mkdtemp(dir=FIXTURE_ROOT)
    subprocess.run(["git", "init", "-q", "."], cwd=d, check=True,
                   capture_output=True)
    p = subprocess.run([BD_BIN, "init", "--prefix", "wp"], cwd=d,
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    return d


def actor(prefix="w"):
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def create(store, title, description="d", actor_name="seeder"):
    p = subprocess.run([BD_BIN, "-C", store, "--actor", actor_name,
                        "create", title, "--description", description,
                        "--json"], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    rows = json.loads(p.stdout)
    return (rows[0] if isinstance(rows, list) else rows)["id"]


def show_raw(store, iid):
    """Raw `bd show --json` bytes for byte-identity comparisons."""
    p = subprocess.run([BD_BIN, "-C", store, "--readonly", "show", iid,
                        "--json"], capture_output=True)
    assert p.returncode == 0, p.stderr
    return p.stdout


def show_dict(store, iid):
    return json.loads(show_raw(store, iid))[0]


class R1IfStatusMismatch(unittest.TestCase):
    """Reproduce interop R1: stale --if-status => exit 13, nothing written."""

    def setUp(self):
        self.store = make_store()
        self.iid = create(self.store, "R1 bead")

    def test_r1_mismatch_yields_stale_and_byte_identical_bead(self):
        before = show_raw(self.store, self.iid)
        with self.assertRaises(write_protocol.WriteStaleError) as cm:
            write_protocol.update_fields(
                self.store, self.iid, actor=actor(), bd_bin=BD_BIN,
                if_assignee="", if_status="in_progress",  # actual status: open
                fields={"priority": "1"})
        err = cm.exception
        self.assertEqual(err.exit_code, 13)
        self.assertTrue(err.guard_mismatch, "guard_mismatch:true required")
        # byte-identical bead: same raw stdout AND canonical-JSON equality
        self.assertEqual(show_raw(self.store, self.iid), before)
        self.assertEqual(show_dict(self.store, self.iid), json.loads(before)[0])
        self.assertEqual(show_dict(self.store, self.iid)["priority"], 2)
        # STALE carries the re-read state ("re-read" leg) and never retries
        self.assertIsNotNone(err.fresh)
        self.assertEqual(err.fresh["status"], "open")

    def test_r1_correct_guard_writes_and_readback_confirms(self):
        row = write_protocol.update_fields(
            self.store, self.iid, actor=actor(), bd_bin=BD_BIN,
            if_assignee="", if_status="open", fields={"priority": "1"})
        self.assertTrue(row["readback_verified"])
        self.assertEqual(show_dict(self.store, self.iid)["priority"], 1)


class U4IfAssigneeMismatch(unittest.TestCase):
    """Reproduce interop U4: stale --if-assignee => exit 13, nothing written."""

    def setUp(self):
        self.store = make_store()
        self.iid = create(self.store, "U4 bead")

    def test_u4_mismatch_yields_stale_and_byte_identical_bead(self):
        before = show_raw(self.store, self.iid)
        with self.assertRaises(write_protocol.WriteStaleError) as cm:
            write_protocol.update_fields(
                self.store, self.iid, actor=actor(), bd_bin=BD_BIN,
                if_assignee="ghost-actor",  # actual holder: nobody
                if_status="open", fields={"notes": "x"})
        self.assertEqual(cm.exception.exit_code, 13)
        self.assertTrue(cm.exception.guard_mismatch)
        self.assertEqual(show_raw(self.store, self.iid), before)
        self.assertNotIn("notes", show_dict(self.store, self.iid))

    def test_u4_correct_guard_writes_and_readback_confirms(self):
        write_protocol.update_fields(
            self.store, self.iid, actor=actor(), bd_bin=BD_BIN,
            if_assignee="", if_status="open", fields={"notes": "held note"})
        self.assertEqual(show_dict(self.store, self.iid)["notes"], "held note")


class WorkerClaimedPath(unittest.TestCase):
    """The worker-side write: claimed bead, guarded field-group, heartbeat."""

    def setUp(self):
        self.store = make_store()
        self.iid = create(self.store, "worker bead")
        self.self_actor = actor("worker")
        claims.claim(self.store, self.iid, actor=self.self_actor,
                     bd_bin=BD_BIN)

    def test_guarded_status_group_writes_under_claim(self):
        write_protocol.update_fields(
            self.store, self.iid, actor=self.self_actor, bd_bin=BD_BIN,
            if_assignee=self.self_actor, if_status="in_progress",
            fields={"notes": "phase done"})
        row = show_dict(self.store, self.iid)
        self.assertEqual(row["notes"], "phase done")
        self.assertEqual(row["assignee"], self.self_actor)
        claims.heartbeat(self.store, self.iid, actor=self.self_actor,
                        bd_bin=BD_BIN)

    def test_stale_after_takeover_maps_to_stale_with_current_holder(self):
        thief = actor("thief")
        # honest takeover, no --force: owner CAS-releases, thief claims fresh.
        claims.release(self.store, self.iid, actor=self.self_actor,
                       bd_bin=BD_BIN)
        p = subprocess.run([BD_BIN, "-C", self.store, "--actor", thief,
                            "update", self.iid, "--claim", "--json"],
                           capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        before = show_raw(self.store, self.iid)
        with self.assertRaises(write_protocol.WriteStaleError) as cm:
            write_protocol.update_fields(
                self.store, self.iid, actor=self.self_actor, bd_bin=BD_BIN,
                if_assignee=self.self_actor, if_status="in_progress",
                fields={"notes": "stale write"})
        self.assertEqual(cm.exception.exit_code, 13)
        self.assertTrue(cm.exception.guard_mismatch)
        self.assertEqual(show_raw(self.store, self.iid), before)
        self.assertEqual(cm.exception.fresh.get("assignee"), thief)

    def test_stale_tells_parent_via_append_only_comment(self):
        parent = create(self.store, "parent epic", actor_name="planner")
        try:
            write_protocol.update_fields(
                self.store, self.iid, actor=self.self_actor, bd_bin=BD_BIN,
                if_assignee="ghost", if_status="in_progress",
                fields={"notes": "never"})
            self.fail("expected WriteStaleError")
        except write_protocol.WriteStaleError as err:
            write_protocol.report_stale_to_parent(
                self.store, err, parent_id=parent, actor=self.self_actor,
                bd_bin=BD_BIN)
        comments = json.loads(subprocess.run(
            [BD_BIN, "-C", self.store, "--readonly", "comments", parent,
             "--json"], capture_output=True, text=True).stdout)
        texts = [c["text"] for c in comments]
        self.assertTrue(any(self.iid in t and "STALE" in t for t in texts),
                        texts)
        # the failed write itself left nothing behind
        self.assertNotIn("notes", show_dict(self.store, self.iid))

    def test_no_retry_on_guard_mismatch(self):
        """STALE never retries the same guard: exactly ONE update argv runs."""
        real = native.run_bd
        seen = []

        def spy(argv, *a, **k):
            if argv and argv[0] == "update":
                seen.append(list(argv))
            return real(argv, *a, **k)

        native.run_bd = spy
        try:
            with self.assertRaises(write_protocol.WriteStaleError):
                write_protocol.update_fields(
                    self.store, self.iid, actor=self.self_actor,
                    bd_bin=BD_BIN, if_assignee="ghost",
                    if_status="in_progress", fields={"notes": "x"})
        finally:
            native.run_bd = real
        self.assertEqual(len(seen), 1, f"retried {len(seen)} update calls")


class UnsupportedReplacement(unittest.TestCase):
    """No --if-revision exists: replacement content is disabled by default."""

    def setUp(self):
        self.store = make_store()
        self.iid = create(self.store, "content bead", description="BASE")

    def test_description_replacement_disabled_by_default(self):
        before = show_raw(self.store, self.iid)
        with self.assertRaises(write_protocol.ExplicitUnsupportedError):
            write_protocol.replace_description(
                self.store, self.iid, actor=actor(), bd_bin=BD_BIN,
                description="HIJACK")
        self.assertEqual(show_raw(self.store, self.iid), before)

    def test_title_replacement_disabled_by_default(self):
        before = show_raw(self.store, self.iid)
        with self.assertRaises(write_protocol.ExplicitUnsupportedError):
            write_protocol.replace_title(
                self.store, self.iid, actor=actor(), bd_bin=BD_BIN,
                title="HIJACK")
        self.assertEqual(show_raw(self.store, self.iid), before)

    def test_opt_in_is_explicit_and_goes_through_guards(self):
        claim_actor = actor("owner")
        claims.claim(self.store, self.iid, actor=claim_actor, bd_bin=BD_BIN)
        row = write_protocol.replace_description(
            self.store, self.iid, actor=claim_actor, bd_bin=BD_BIN,
            description="NEW", if_assignee=claim_actor,
            if_status="in_progress", allow_unsupported=True)
        self.assertTrue(row["readback_verified"])
        self.assertEqual(show_dict(self.store, self.iid)["description"], "NEW")

    def test_opt_in_never_promises_no_lost_update(self):
        doc = (write_protocol.replace_description.__doc__ or "").lower()
        self.assertIn("unsupported", doc)
        self.assertIn("not cas", doc)
        self.assertNotIn("no-lost-update", doc)
        self.assertIn("last write wins", doc)


class AppendCommentsSeparate(unittest.TestCase):
    def test_append_comment_needs_no_guard_flags(self):
        store = make_store()
        iid = create(store, "commented")
        a = actor("cmt")
        write_protocol.append_comment(store, iid, actor=a, bd_bin=BD_BIN,
                                      text="evidence line")
        comments = json.loads(subprocess.run(
            [BD_BIN, "-C", store, "--readonly", "comments", iid, "--json"],
            capture_output=True, text=True).stdout)
        self.assertEqual(len(comments), 1)
        self.assertEqual(comments[0]["text"], "evidence line")
        # append-only: a second comment coexists
        write_protocol.append_comment(store, iid, actor=actor("cmt2"),
                                      bd_bin=BD_BIN, text="second")
        comments = json.loads(subprocess.run(
            [BD_BIN, "-C", store, "--readonly", "comments", iid, "--json"],
            capture_output=True, text=True).stdout)
        self.assertEqual([c["text"] for c in comments],
                         ["evidence line", "second"])


class FieldGroupGranularity(unittest.TestCase):
    def test_one_command_per_field_group(self):
        """No batched mega-writes: a 2-field group is ONE update argv."""
        store = make_store()
        iid = create(store, "grouped")
        real = native.run_bd
        updates = []

        def spy(argv, *a, **k):
            if argv and argv[0] == "update":
                updates.append(list(argv))
            return real(argv, *a, **k)

        native.run_bd = spy
        try:
            write_protocol.update_fields(
                store, iid, actor=actor(), bd_bin=BD_BIN,
                if_assignee="", if_status="open",
                fields={"priority": "1", "notes": "both"})
        finally:
            native.run_bd = real
        self.assertEqual(len(updates), 1)
        self.assertIn("--priority", updates[0])
        self.assertIn("--notes", updates[0])


class GuardSurface(unittest.TestCase):
    def test_guardless_update_is_refused(self):
        store = make_store()
        iid = create(store, "guardless")
        with self.assertRaises(ValueError):
            write_protocol.update_fields(store, iid, actor=actor(),
                                         bd_bin=BD_BIN, fields={"priority": "1"})
        with self.assertRaises(ValueError):
            write_protocol.update_fields(store, iid, actor=actor(),
                                         bd_bin=BD_BIN, if_status="open",
                                         fields={"priority": "1"})

    def test_claim_clobber_flags_never_built(self):
        """--claim and --force must never appear as argv tokens in writes."""
        audit = CodePathAudit()
        audit.maxDiff = None
        for lineno, toks in audit._calls():
            for banned in ("--force", "--claim", "--if-revision",
                           "--if-content", "--cas"):
                self.assertNotIn(banned, toks,
                                 f"{banned} built into argv at line {lineno}")


class CodePathAudit(unittest.TestCase):
    """Static audit: EVERY native write call site is guarded or labelled.

    Parses write_protocol.py's AST; for each native.run_bd(...) call whose
    first argument is a list literal, resolves constant tokens (module
    constants too) and asserts: an `update` write carries the --if-assignee
    and --if-status guard tokens; a `comments add` append carries no guard
    (append-only needs none); and any site that writes content without
    guards sits under a preceding EXPLICIT-UNSUPPORTED label.
    """

    LABEL = "EXPLICIT-UNSUPPORTED"

    def _calls(self):
        tree = ast.parse(open(write_protocol.__file__).read())
        consts = {}
        for node in tree.body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                    and isinstance(node.targets[0], ast.Name) \
                    and isinstance(node.value, ast.Constant):
                consts[node.targets[0].id] = node.value.value
        # local list-literal assignments (argv built a line or two up)
        listvars = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                    and isinstance(node.targets[0], ast.Name):
                listvars[node.targets[0].id] = node.value

        def toks_of(expr):
            out = []
            if isinstance(expr, ast.List):
                for el in expr.elts:
                    if isinstance(el, ast.Constant) and isinstance(el.value, str):
                        out.append(el.value)
                    elif isinstance(el, ast.Name) and el.id in consts:
                        out.append(consts[el.id])
            elif isinstance(expr, ast.Name) and expr.id in listvars:
                out.extend(toks_of(listvars[expr.id]))
            elif isinstance(expr, ast.BinOp):
                out.extend(toks_of(expr.left))
                out.extend(toks_of(expr.right))
            return out

        found = []
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "run_bd"
                    and node.args):
                found.append((node.lineno, toks_of(node.args[0])))
        return found

    def test_at_least_the_known_write_sites_exist(self):
        calls = self._calls()
        self.assertTrue(calls, "no run_bd call sites found — audit vacuous")

    def test_every_write_call_site_guarded_or_labelled(self):
        src_lines = open(write_protocol.__file__).read().splitlines()
        checked = 0
        for lineno, toks in self._calls():
            if "update" in toks:
                context = "\n".join(
                    src_lines[max(0, lineno - 11):lineno + 9])
                guarded = (write_protocol.GUARD_IF_ASSIGNEE in toks
                           and write_protocol.GUARD_IF_STATUS in toks)
                labelled = self.LABEL in context
                self.assertTrue(
                    guarded or labelled,
                    f"unguarded, unlabelled update write at line {lineno}: {toks}")
                checked += 1
            elif "comments" in toks:
                self.assertNotIn("--if-", " ".join(toks),
                                 "comments add must not fake guard flags")
                checked += 1
        self.assertGreaterEqual(checked, 2,
                                "audit found no guarded update AND no append-comment site")


if __name__ == "__main__":
    os.makedirs(FIXTURE_ROOT, exist_ok=True)
    unittest.main(verbosity=2)
