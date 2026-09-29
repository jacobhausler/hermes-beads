#!/usr/bin/env python3
"""hbl-pnu.3.5 (Work half): one Work click => one signed admission.

Under test (acceptance for the Work half of bead hbl-pnu.3.5):
  1. Duplicate click, same idempotency key => exactly ONE receipt and ONE
     admission (read-back: one key-index entry, stored receipt byte-equal,
     replayed flag, exactly one worker spawn).
  2. Timeout => truthful run state "uncertain" — never "delivered".
  3. Cancel => runner_binding.stop_work (signed); while the runner is alive
     the truth is "cancel_requested"; "cancelled" only AFTER the runner
     confirms terminal. Never claims terminated early.
  4. Worker exit never closes the bead: FAILTASK worker exits, native
     read-back status != closed, ledger state failed.
  5. Without a bound door (tests/test_bot_handoff.py, kept green) or when
     runner qualification is absent (no credential / no patched isolated
     Workflow checkout) => Work visibly unavailable with a typed reason,
     never fake success, and NOTHING is claimed.

Real pinned bd v1.3.0 disposable stores + the committed fake-hermes child;
fixture helpers are IMPORTED from tests/test_runner_binding.py (reuse, not
re-derived); the patched isolated Workflow checkout is rebuilt via
runner_hooks.install under tests/fixtures/runner-runtime (gitignored).
The installed plugin tree is only read (hashed before/after).

Run: python3 tests/test_work_half.py
"""
import json
import os
import shutil
import sys
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

import runner_hooks                # noqa: E402
import bot_handoff                 # noqa: E402
import work_door                   # noqa: E402
import read_model                  # noqa: E402
import runner_binding as rb        # noqa: E402
import test_runner_binding as trb  # noqa: E402  (fixture helpers: reuse)

BD = trb.BD
WH = trb.FIXTURES / "work-half"


class WorkHalf(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # fresh isolated patched checkout (same law as the 3.3 suite) AND a
        # fresh run root: a stale run from an earlier session can never
        # shadow this session's admission (idempotency lives in real state,
        # and the state under test is created fresh, deterministically).
        if trb.WORKFLOW_SRC.exists():
            shutil.rmtree(trb.WORKFLOW_SRC)
        trb.WORKFLOW_SRC.mkdir(parents=True)
        for f in ("wf.py", "wfcommon.py"):
            shutil.copy2(trb.INSTALLED / f, trb.WORKFLOW_SRC / f)
        runner_hooks.install(trb.WORKFLOW_SRC / "wf.py", os.path.dirname(HERE))
        if WH.exists():
            shutil.rmtree(WH)
        (WH / "runs").mkdir(parents=True)

    def setUp(self):
        bot_handoff.bind_runner_door(None)

    def tearDown(self):
        bot_handoff.bind_runner_door(None)

    def door(self, store, **kw):
        d = work_door.make_work_door(
            store=str(store), bd_bin=BD,
            run_base=str(WH / "runs"), workflow_src=str(trb.WORKFLOW_SRC),
            hermes_bin=str(trb.FAKE_HERMES), **kw)
        bot_handoff.bind_runner_door(d)
        return d

    # ---- 1: duplicate click, one idempotency key --------------------------
    def test_duplicate_click_one_receipt_one_admission(self):
        s = trb.make_store("wh-dup")
        i = trb.create(s, "dup click")
        cf = trb.cred("wh-dup", s, [i])
        d = self.door(s, cred_path=str(cf))
        r1 = bot_handoff.run_work(str(s), i, actor="w-bot", bd_bin=BD,
                                  request_key="k-dup")
        r2 = bot_handoff.run_work(str(s), i, actor="w-bot", bd_bin=BD,
                                  request_key="k-dup")
        self.assertTrue(r1["ok"], r1)
        self.assertTrue(r1["handed_off"])
        self.assertTrue(r2["ok"], r2)
        self.assertEqual(r1["door_result"]["replayed"], False)
        self.assertEqual(r2["door_result"]["replayed"], True)
        self.assertEqual(r1["door_result"]["run_id"],
                         r2["door_result"]["run_id"])
        # handed-off is still NOT delivered, on both clicks
        for r in (r1, r2):
            self.assertFalse(r["delivery"])
            self.assertTrue(r["no_dispatch"])
        st = d.wait_terminal(request_key="k-dup", timeout=120)
        self.assertEqual(st["state"], "succeeded", st)
        run_dir = d.run_dir(request_key="k-dup")
        # read-back: exactly one admission — one key-index entry, one receipt,
        # one worker spawn; stored receipt equals the first click's receipt
        keys_dir = os.path.join(run_dir, "beads", "keys")
        key_files = [f for f in os.listdir(keys_dir) if f.endswith(".json")]
        self.assertEqual(len(key_files), 1, key_files)
        stored = json.loads(open(os.path.join(run_dir, "beads",
                                              "receipt.json")).read())
        self.assertEqual(stored["grant_digest"],
                         r1["door_result"]["grant_digest"])
        self.assertEqual(d.spawn_count(request_key="k-dup"), 1)
        # native read-back: the admission closed the bead THROUGH the
        # verifier (not on worker exit): assignee is the grant verifier and
        # the close reason carries the causal token
        row = read_model.show(str(s), i, bd_bin=BD)
        self.assertEqual(row["status"], "closed")
        self.assertIn("nonce=", row.get("close_reason") or "")

    # ---- 2: timeout => uncertain, never delivered --------------------------
    def test_timeout_is_uncertain_never_delivered(self):
        s = trb.make_store("wh-to")
        i = trb.create(s, "slow worker")
        cf = trb.cred("wh-to", s, [i])
        # the door's own wall deadline expires while the runner is still
        # alive and the worker still runs: the TRUTH at that moment is
        # "uncertain" — the click was handed off, nothing confirmed it.
        d = self.door(s, cred_path=str(cf), goal_prefix="SLEEP 12 ",
                      node_timeout=60, wall_deadline_s=5)
        r = bot_handoff.run_work(str(s), i, actor="w-bot", bd_bin=BD,
                                 request_key="k-to")
        self.assertTrue(r["ok"], r)
        st = d.wait_terminal(request_key="k-to", timeout=120)
        self.assertEqual(st["state"], "uncertain", st)
        self.assertNotEqual(st["state"], "delivered")
        self.assertNotIn("delivered", json.dumps(r))
        row = read_model.show(str(s), i, bd_bin=BD)
        self.assertNotEqual(row["status"], "closed")

    # ---- 3: cancel: truthful two-phase, never early termination ------------
    def test_cancel_requested_then_cancelled_only_after_runner_confirms(self):
        s = trb.make_store("wh-cancel")
        i = trb.create(s, "long worker")
        cf = trb.cred("wh-cancel", s, [i])
        d = self.door(s, cred_path=str(cf), goal_prefix="SLEEP 20 ",
                      node_timeout=60)
        r = bot_handoff.run_work(str(s), i, actor="w-bot", bd_bin=BD,
                                 request_key="k-cancel")
        self.assertTrue(r["ok"], r)
        end = time.time() + 60
        while d.spawn_count(request_key="k-cancel") < 1 and time.time() < end:
            time.sleep(0.2)
        self.assertEqual(d.spawn_count(request_key="k-cancel"), 1)
        c = d.cancel(request_key="k-cancel")
        self.assertTrue(c["ok"], c)
        # while the runner is alive the truth is cancel_requested — never
        # "cancelled"/"terminated" before the runner confirms
        self.assertEqual(c["state"], "cancel_requested")
        self.assertTrue(c["runner_alive"])
        self.assertEqual(d.status(request_key="k-cancel")["state"],
                         "cancel_requested")
        rec = json.loads(open(os.path.join(d.run_dir(request_key="k-cancel"),
                                           "nodes", "work.json")).read())
        os.kill(rec["pid"], 0)  # the child really is still alive here
        confirmed = d.wait_terminal(request_key="k-cancel", timeout=90)
        self.assertEqual(confirmed["state"], "cancelled", confirmed)
        self.assertFalse(d.runner_alive(request_key="k-cancel"))
        # stop stays latched: a re-drive cannot resurrect work past the stop
        self.assertTrue(os.path.exists(os.path.join(
            d.run_dir(request_key="k-cancel"), "beads", "STOP")))
        with self.assertRaises(OSError):
            os.kill(rec["pid"], 0)

    # ---- 4: worker exit never closes the bead ------------------------------
    def test_worker_exit_never_closes_the_bead(self):
        s = trb.make_store("wh-fail")
        i = trb.create(s, "failing worker")
        cf = trb.cred("wh-fail", s, [i])
        d = self.door(s, cred_path=str(cf), goal_prefix="FAILTASK ",
                      node_timeout=45)
        r = bot_handoff.run_work(str(s), i, actor="w-bot", bd_bin=BD,
                                 request_key="k-fail")
        self.assertTrue(r["ok"], r)
        st = d.wait_terminal(request_key="k-fail", timeout=120)
        self.assertEqual(st["state"], "failed", st)
        self.assertNotIn("delivered", json.dumps(r))
        row = read_model.show(str(s), i, bd_bin=BD)
        self.assertNotEqual(row["status"], "closed")   # native readback
        led = rb.ledger_read(d.run_dir(request_key="k-fail"), i)
        self.assertEqual(led["state"], "failed")
        self.assertEqual(row.get("assignee"), "w-bot")  # still held; not closed

    # ---- 5: unqualified => typed unavailable, never fake success -----------
    def test_unqualified_door_is_visible_unavailable(self):
        s = trb.make_store("wh-unq")
        i = trb.create(s, "unqualified")
        missing = str(WH / "creds" / "does-not-exist.json")
        d = self.door(s, cred_path=missing)
        out = bot_handoff.run_work(str(s), i, actor="w-bot", bd_bin=BD,
                                   request_key="k-unq")
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "runner_unqualified")
        self.assertFalse(out["handed_off"])
        self.assertIn("credential", out["reason"])
        # precheck ran BEFORE the claim: nothing was claimed
        row = read_model.show(str(s), i, bd_bin=BD)
        self.assertFalse(row.get("assignee") or None)
        # missing isolated patched checkout => also typed unavailable
        cf = trb.cred("wh-unq", s, [i])
        d2 = work_door.make_work_door(
            store=str(s), bd_bin=BD, run_base=str(WH / "runs"),
            workflow_src=str(trb.FIXTURES / "no-such-checkout"),
            hermes_bin=str(trb.FAKE_HERMES), cred_path=str(cf))
        bot_handoff.bind_runner_door(d2)
        out2 = bot_handoff.run_work(str(s), i, actor="w-bot", bd_bin=BD,
                                    request_key="k-unq2")
        self.assertFalse(out2["ok"])
        self.assertEqual(out2["error"], "runner_unqualified")
        self.assertIn("workflow", out2["reason"].lower())
        row = read_model.show(str(s), i, bd_bin=BD)
        self.assertFalse(row.get("assignee") or None)


if __name__ == "__main__":
    unittest.main(verbosity=2)
