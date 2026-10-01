#!/usr/bin/env python3
"""END-TO-END SLICE (): scoped ready -> claim -> guarded edit ->
evidence -> authorized close -> verifier read-back -> newly ready successors.

Verdict under test — the thin dispatch->work->evidence->closure loop holds on
a disposable store built fresh by this test, with EVERY step read back
against the exact native state it must show:

  1. frontier        read_model.ready (bd ready --json --exclude-type=epic
                     --label impl): gate bead present; blocked successor,
                     non-impl bead and the impl EPIC all absent (the epic is
                     present in the raw frontier — the exclusion is real).
  2. claim           claims.claim exact ID -> read-back assignee=worker /
                     status=in_progress; second actor's mid-loop claim
                     attempt fails honestly naming the holder (serial
                     contention, not concurrency).
  3. guarded edit    write_protocol.update_fields (notes) under BOTH native
                     guards --if-assignee/--if-status -> read-back equal.
                     Replacement (description/title) stays DISABLED — a
                     supported guarded metadata update satisfies "edit".
  4. close           plain native `bd close` (the plugin ships no close
                     surface; escape-hatch first).
  5. verifier        read-back: status=closed, closed_at, close_reason.
  6. successors      re-query frontier: gate gone, successor now ready
                     (complete causal loop: the close released the dep).

Negative control: bd 1.3.0 has NO comment edit/delete verb (probed: only
add/list; deletion attempts exit nonzero and the comment remains — no
deletion capability was invented).

Real pinned bd (v1.3.0, f45b249ce) against fresh disposable stores under
tests/fixtures/e2e-runtime (gitignored; bootstrap lives there, databases
never committed; each fixture owns its git init — the embedded-dolt
fall-through trap). The planning store is read-only to this lane and is
never touched: this test only creates and mutates its own throwaway stores.
No SQL, no --force, no shadow store, no Beads memory/mail/formulas.

Run: python3 tests/test_e2e.py     (failures exit nonzero — self-proven)
"""
import json
import os
import shutil
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, os.path.join(HERE, "fixtures", "e2e-runtime"))

import bootstrap                      # noqa: E402  (fresh real stores)
from beads import claims, native, read_model, write_protocol  # noqa: E402

BD_BIN = bootstrap.BD_BIN
FIXTURE_ROOT = bootstrap.FIXTURE_ROOT
make_store = bootstrap.make_store
actor = bootstrap.actor
seed_bead = bootstrap.seed_bead
show_dict = bootstrap.show_dict
comments_list = bootstrap.comments_list
raw_ready = bootstrap.raw_ready

ARTIFACTS = ["tests/test_e2e.py", "reports/drive-e2e.json"]


def seed_world(store):
    """The causal shape the loop must traverse, all label=impl:
    an epic (never dispatchable), a ready gate, a gate-blocked successor
    (deps: dep add successor gate => gate blocks successor), and an off-label
    decoy. Returns exact native IDs."""
    p = bootstrap.raw_bd(store, "create", "e2e real epic", "-t", "epic",
                         "-l", "impl", "--json")
    d = json.loads(p.stdout)
    epic = (d[0] if isinstance(d, list) else d)["id"]
    gate = seed_bead(store, "gate bead", labels=("impl",),
                     description="scoped work under test")
    decoy = seed_bead(store, "off-label decoy", labels=("ops",))
    succ = seed_bead(store, "successor bead", labels=("impl",), deps=(gate,))
    return epic, gate, decoy, succ


class FullCausalLoop(unittest.TestCase):
    """The green scripted e2e run: steps 1-7, every step read back."""

    def setUp(self):
        self.store = make_store()

    def test_ready_claim_edit_evidence_close_successors(self):
        epic, gate, decoy, succ = seed_world(self.store)

        # 1. scoped frontier (read_model.ready = bd ready --exclude-type=epic
        #    --label impl): gate in; blocked successor / decoy / epic absent.
        frontier = [r["id"] for r in
                    read_model.ready(self.store, label="impl", bd_bin=BD_BIN)]
        self.assertIn(gate, frontier)
        self.assertNotIn(succ, frontier, "blocked successor must not be ready")
        self.assertNotIn(decoy, frontier, "off-label bead must not be ready")
        self.assertNotIn(epic, frontier, "epic must never be dispatchable")
        raw = raw_ready(self.store, label="impl", exclude_epics=False)
        self.assertIn(epic, raw,
                      "control: epic IS in the raw frontier — the "
                      "--exclude-type=epic bound is what removes it")

        # 2. claim exact ID + read back owner/status; heartbeat while active.
        w = actor("worker")
        row = claims.claim(self.store, gate, actor=w, bd_bin=BD_BIN)
        self.assertEqual(row["assignee"], w)
        self.assertEqual(row["status"], "in_progress")
        back = show_dict(self.store, gate)
        self.assertEqual((back["assignee"], back["status"]), (w, "in_progress"))
        claims.heartbeat(self.store, gate, actor=w, bd_bin=BD_BIN)

        # 2b. mid-loop second-actor claim attempt fails honestly, naming the
        #     holder (serial contention, never a forced takeover).
        w2 = actor("intruder")
        with self.assertRaises(claims.ClaimConflictError) as cm:
            claims.claim(self.store, gate, actor=w2, bd_bin=BD_BIN)
        self.assertEqual(cm.exception.holder, w)
        self.assertEqual(show_dict(self.store, gate)["assignee"], w)

        # 3. one guarded metadata update (both native guards) + read-back.
        edited = write_protocol.update_fields(
            self.store, gate, actor=w, bd_bin=BD_BIN,
            if_assignee=w, if_status="in_progress",
            fields={"notes": "e2e: guarded metadata edit under live claim"})
        self.assertTrue(edited["readback_verified"])
        self.assertEqual(show_dict(self.store, gate)["notes"],
                         "e2e: guarded metadata edit under live claim")

        # 4-5. close via plain native bd close (the plugin ships no close
        #      surface — `bd close` is the escape hatch; the verifier era is
        #      on the door-stack branch).
        reason = "e2e: full loop green, closed through native bd"
        p = bootstrap.raw_bd(self.store, "close", gate, "--reason", reason,
                             "--json", actor_name=w)
        self.assertEqual(p.returncode, 0, f"native close refused: {p.stderr}")

        # 6. read-back: closed, closed_at, reason.
        closed = show_dict(self.store, gate)
        self.assertEqual(closed["status"], "closed")
        self.assertTrue(closed["closed_at"], "closed_at required")
        self.assertEqual(closed["close_reason"], reason)

        # 7. re-query frontier: gate gone, successor now ready (causal loop).
        frontier2 = [r["id"] for r in
                     read_model.ready(self.store, label="impl", bd_bin=BD_BIN)]
        self.assertNotIn(gate, frontier2)
        self.assertIn(succ, frontier2,
                      "closing the gate must release the successor")


class ReplacementStaysDisabled(unittest.TestCase):
    """The 'edit' step is the SUPPORTED guarded metadata update; replacement
    content stays explicit-unsupported, and a stale guard is STALE — nothing
    written, never retried."""

    def setUp(self):
        self.store = make_store()
        self.iid = seed_bead(self.store, "guard bead", labels=("impl",))
        self.w = actor("worker")
        claims.claim(self.store, self.iid, actor=self.w, bd_bin=BD_BIN)

    def test_replacement_refused_by_default_state_untouched(self):
        before = json.dumps(show_dict(self.store, self.iid), sort_keys=True)
        for verb, kw in ((write_protocol.replace_description,
                          {"description": "clobbered"}),
                         (write_protocol.replace_title,
                          {"title": "clobbered"})):
            with self.assertRaises(write_protocol.ExplicitUnsupportedError):
                verb(self.store, self.iid, actor=self.w, bd_bin=BD_BIN,
                     if_assignee=self.w, if_status="in_progress", **kw)
        self.assertEqual(json.dumps(show_dict(self.store, self.iid),
                                    sort_keys=True), before)

    def test_stale_guard_writes_nothing_and_reports_fresh(self):
        stranger = actor("stranger")
        before = json.dumps(show_dict(self.store, self.iid), sort_keys=True)
        with self.assertRaises(write_protocol.WriteStaleError) as cm:
            write_protocol.update_fields(
                self.store, self.iid, actor=stranger, bd_bin=BD_BIN,
                if_assignee=stranger, if_status="in_progress",
                fields={"notes": "must never land"})
        self.assertEqual(cm.exception.holder, self.w)
        self.assertNotIn("must never land",
                         json.dumps(show_dict(self.store, self.iid)))
        self.assertEqual(json.dumps(show_dict(self.store, self.iid),
                                    sort_keys=True), before)


class AppendOnlyNegativeProbe(unittest.TestCase):
    """No comment-deletion capability is invented: bd 1.3.0 exposes only
    comments add/list; any deletion-shaped argv fails nonzero and the
    comment survives — append-only is native fact, this test only attests
    it (and that the plugin never builds a delete verb)."""

    def test_no_comment_delete_verb(self):
        store = make_store()
        iid = seed_bead(store, "append-only", labels=("impl",))
        write_protocol.append_comment(store, iid, actor=actor("w"),
                                      bd_bin=BD_BIN, text="EVIDENCE pin")
        for argv in (["comments", "delete", iid, "1"],
                     ["comments", "rm", iid, "1"],
                     ["comments", iid, "delete", "1"]):
            p = bootstrap.raw_bd(store, *argv, "--json")
            self.assertNotEqual(p.returncode, 0,
                                f"{argv[:3]} unexpectedly succeeded")
        self.assertEqual(len(comments_list(store, iid)), 1,
                         "comment survives every deletion-shaped attempt")
        for mod in ("native", "read_model", "claims", "write_protocol"):
            with open(os.path.join(os.path.dirname(HERE), "beads",
                           f"{mod}.py")) as f:
                src = f.read()
            for banned in ('"delete"', '"rm "'):
                self.assertNotIn(
                    banned, src,
                    f"{mod}: plugin surface must never build a comment "
                    f"deletion verb — none is supported")


class FailureSurfacesAsNonzeroExit(unittest.TestCase):
    """Harness contract: a FAILING e2e run must produce a nonzero process
    status. Proven with a self-destruct subprocess of this very file."""

    @unittest.skipIf(os.environ.get("E2E_SELF_FAIL"),
                     "self-destruct run must not recurse")
    def test_self_fail_subprocess_exits_nonzero(self):
        env = dict(os.environ, E2E_SELF_FAIL="1")
        p = subprocess.run([sys.executable, os.path.abspath(__file__),
                            "FailureSurfacesAsNonzeroExit."
                            "test_selfdestruct_actually_fails"],
                           capture_output=True, text=True, env=env,
                           cwd=os.path.dirname(HERE))
        self.assertNotEqual(p.returncode, 0,
                            "a failing test run exited 0 — silent green")
        self.assertIn("FAILED", p.stderr + p.stdout)

    @unittest.skipUnless(os.environ.get("E2E_SELF_FAIL"),
                         "runs only under the self-destruct subprocess")
    def test_selfdestruct_actually_fails(self):
        self.fail("deliberate failure: proves nonzero exit status")


if __name__ == "__main__":
    os.makedirs(FIXTURE_ROOT, exist_ok=True)
    try:
        unittest.main(verbosity=2)
    finally:
        # own-dir cleanup only — the root is shared with the
        # scenarios/mounted suites; sweeping it killed their live stores.
        if not os.environ.get("E2E_KEEP_FIXTURES"):
            bootstrap.cleanup_run_stores()
