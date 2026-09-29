#!/usr/bin/env python3
"""hbl-pnu.3.5 (Ask/Refine half): bot handoff surface.

Under test:
  1. ask   — read-only bot question scoped to the selected bead context;
             the reply carries the EXACT interop.submit_request typed
             session-door refusal (ok:false, error:session_door_unqualified
             — never ok:true) plus a read-only bead context; the store is
             untouched.
  2. refine — bot proposes a draft; it lands in the Return-to-Draft payload
             for the human draft store (desktop/drafts.mjs via the desktop
             accept path); the plugin NEVER writes the bd store itself
             (description/comments read back unchanged).
  3. work control — present-but-disabled with a typed reason until the
             runner binding (hbl-pnu.3.3) is merged; ONE injection point
             (bind_runner_door) where an admitted runner door plugs in.
             With an admitted door bound, run_work exercises the reused
             primitives for real: claims.claim (read-back verified),
             claims.inspect_after_claim, evidence.WorkerSurface — and the
             handed-off result still says delivery:false / no_dispatch:true.
             Claim conflict is a typed refusal with the door never invoked.

Real pinned bd (v1.3.0) against disposable stores under
tests/fixtures/handoff-runtime (gitignored, own git init). No SQL, no
--force, no shadow store, no Beads memory/mail/formulas, planning store
never touched.

Run: python3 tests/test_bot_handoff.py   (stdlib unittest, self-proven)
"""
import json
import os
import shutil
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, os.path.join(HERE, "fixtures", "handoff-runtime"))

import handoff_bootstrap as bootstrap  # noqa: E402
import bot_handoff                    # noqa: E402
import claims                         # noqa: E402
import evidence                       # noqa: E402

BD_BIN = bootstrap.BD_BIN


class HandoffTest(unittest.TestCase):
    def setUp(self):
        bot_handoff.bind_runner_door(None)  # single injection point, default
        self.store = bootstrap.make_store()

    def tearDown(self):
        bot_handoff.bind_runner_door(None)
        shutil.rmtree(self.store, ignore_errors=True)


class Ask(HandoffTest):
    def test_ask_is_readonly_scoped_context_with_typed_refusal(self):
        iid = bootstrap.seed_bead(self.store, "ask target",
                                  description="the selected bead context")
        comments_before = bootstrap.raw_bd(self.store, "comments", iid,
                                            "--json", readonly=True).stdout
        out = bot_handoff.ask(self.store, iid, question="what blocks this?",
                              bd_bin=BD_BIN)
        # exact identity preserved, read-only, no dispatch
        self.assertEqual(out["workspace"], self.store)
        self.assertEqual(out["bead"], iid)
        self.assertEqual(out["intent"], "ask")
        self.assertTrue(out["read_only"])
        self.assertTrue(out["no_dispatch"])
        # unqualified session door => typed refusal, never ok:true
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "session_door_unqualified")
        self.assertEqual(out["route"], "hermes_session_door")
        self.assertFalse(out["execution_authority"])
        # the scoped, read-only context rode along
        self.assertEqual(out["context"]["id"], iid)
        self.assertEqual(out["context"]["description"],
                          "the selected bead context")
        # store untouched: description + comments byte-identical
        row = bootstrap.show_dict(self.store, iid)
        self.assertEqual(row["description"], "the selected bead context")
        self.assertEqual(bootstrap.raw_bd(
            self.store, "comments", iid, "--json",
            readonly=True).stdout, comments_before)

    def test_ask_empty_question_typed_refusal(self):
        iid = bootstrap.seed_bead(self.store, "ask target")
        out = bot_handoff.ask(self.store, iid, question="   ", bd_bin=BD_BIN)
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "invalid_request")

    def test_ask_unknown_bead_reports_missing_context(self):
        out = bot_handoff.ask(self.store, "nope-999", question="hi",
                              bd_bin=BD_BIN)
        self.assertFalse(out["ok"])
        self.assertIsNone(out["context"])
        self.assertEqual(out["error"], "session_door_unqualified")


class Refine(HandoffTest):
    def test_refine_lands_draft_payload_and_never_writes_store(self):
        iid = bootstrap.seed_bead(self.store, "refine target",
                                  description="original text")
        out = bot_handoff.refine(self.store, iid,
                                 proposed_text="improved text",
                                 bd_bin=BD_BIN)
        self.assertEqual(out["intent"], "refine")
        # the interop authority still owns the session door: typed refusal
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "session_door_unqualified")
        d = out["draft"]
        self.assertEqual(d["text"], "improved text")
        self.assertEqual(d["baseText"], "original text")
        self.assertEqual(d["provenance"], "bot")
        self.assertTrue(d["requires_human_accept"])
        self.assertTrue(out["no_dispatch"])
        # the bot NEVER wrote the store: no replacement, no comment
        row = bootstrap.show_dict(self.store, iid)
        self.assertEqual(row["description"], "original text")
        self.assertEqual(json.loads(bootstrap.raw_bd(
            self.store, "comments", iid, "--json", readonly=True).stdout),
            [])

    def test_refine_carries_return_to_draft_path(self):
        iid = bootstrap.seed_bead(self.store, "rtd target")
        out = bot_handoff.refine(self.store, iid, proposed_text="t",
                                bd_bin=BD_BIN)
        rtd = out["return_to_draft"]
        self.assertEqual(rtd["action"], "reopen_draft")
        self.assertEqual(rtd["bead"], iid)
        self.assertEqual(rtd["workspace"], self.store)


class WorkControl(HandoffTest):
    def test_work_present_but_disabled_until_runner_door_bound(self):
        st = bot_handoff.work_status()
        self.assertTrue(st["present"])
        self.assertFalse(st["enabled"])
        self.assertIn("hbl-pnu.3.3", st["disabledReason"])
        iid = bootstrap.seed_bead(self.store, "work target")
        seen = []
        out = bot_handoff.run_work(self.store, iid, actor="lazy-bot",
                                   bd_bin=BD_BIN)
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "work_surface_disabled")
        self.assertEqual(seen, [])  # door never invoked
        row = bootstrap.show_dict(self.store, iid)
        self.assertIsNone(row.get("assignee") or None)  # never claimed

    def test_admitted_door_flips_surface_and_work_is_claimed_for_real(self):
        iid = bootstrap.seed_bead(self.store, "work target")
        calls = []

        def door(payload):
            calls.append(payload)
            # the reused evidence surface is real: record on our own store
            payload["surface"].record_evidence(iid, attempt="a1",
                                               artifacts=["test_bot_handoff.py"])
            return {"handed_off": True}

        bot_handoff.bind_runner_door(door)
        st = bot_handoff.work_status()
        self.assertTrue(st["enabled"])
        out = bot_handoff.run_work(self.store, iid, actor="bot-worker",
                                   bd_bin=BD_BIN)
        self.assertTrue(out["ok"])
        self.assertTrue(out["handed_off"])
        # handed-off != delivered: the honest dispatch posture rides along
        self.assertFalse(out["delivery"])
        self.assertTrue(out["no_dispatch"])
        self.assertEqual(len(calls), 1)
        # claims.claim actually ran and read back
        self.assertEqual(calls[0]["row"]["assignee"], "bot-worker")
        self.assertEqual(calls[0]["row"]["status"], "in_progress")
        self.assertEqual(calls[0]["reasons"], [])
        self.assertIsInstance(calls[0]["surface"], evidence.WorkerSurface)
        # WorkerSurface.record_evidence actually wrote to OUR store
        comments = json.loads(bootstrap.raw_bd(
            self.store, "comments", iid, "--json", readonly=True).stdout)
        self.assertTrue(any("EVIDENCE attempt=a1" in c["text"]
                            for c in comments))

    def test_claim_conflict_is_typed_refusal_door_never_invoked(self):
        iid = bootstrap.seed_bead(self.store, "contended target")
        claims.claim(self.store, iid, actor="first-bot", bd_bin=BD_BIN)
        seen = []
        bot_handoff.bind_runner_door(seen.append)
        out = bot_handoff.run_work(self.store, iid, actor="second-bot",
                                   bd_bin=BD_BIN)
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "claim_conflict")
        self.assertEqual(out["holder"], "first-bot")
        self.assertEqual(seen, [])

    def test_injection_point_is_single_and_reversible(self):
        seen = []
        bot_handoff.bind_runner_door(seen.append)
        self.assertTrue(bot_handoff.work_status()["enabled"])
        bot_handoff.bind_runner_door(None)
        st = bot_handoff.work_status()
        self.assertFalse(st["enabled"])
        self.assertIn("hbl-pnu.3.3", st["disabledReason"])
        # non-callable junk never counts as an admitted door
        bot_handoff.bind_runner_door("not-a-door")
        self.assertFalse(bot_handoff.work_status()["enabled"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
