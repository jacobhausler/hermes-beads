"""I0 interop tests: exact ID preservation, rejections, typed unsupported.

Run: python3 -m unittest tests.test_interop  (from repo root; stdlib only)
"""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import interop  # noqa: E402

REQ = {"workspace": "/work/proj-a", "bead": "hbl-pnu.3.4", "intent": "ask"}


class ExactIdentity(unittest.TestCase):
    def test_ask_preserves_exact_ids(self):
        out = interop.submit_request(dict(REQ))
        self.assertEqual(out["workspace"], REQ["workspace"])
        self.assertEqual(out["bead"], REQ["bead"])
        self.assertEqual(out["intent"], "ask")
        self.assertFalse(out["delivery"])
        self.assertTrue(out["no_dispatch"])

    def test_refine_routes_session_door_without_authority(self):
        out = interop.submit_request(dict(REQ, intent="refine", session_link=True))
        self.assertEqual(out["route"], "hermes_session_door")
        self.assertTrue(out["session_link"])
        self.assertFalse(out["execution_authority"])

    def test_session_link_cannot_authorize_work(self):
        out = interop.submit_request(dict(REQ, intent="work", session_link=True))
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "out_of_scope")
        self.assertEqual(out["intent"], "work")  # exact preservation on rejection


class BadRequests(unittest.TestCase):
    def test_missing_fields_rejected(self):
        out = interop.submit_request({"bead": "x", "intent": "ask"})
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "invalid_request")

    def test_unknown_intent_rejected(self):
        out = interop.submit_request(dict(REQ, intent="delete"))
        self.assertEqual(out["error"], "unknown_intent")

    def test_out_of_scope_key_rejected(self):
        out = interop.submit_request(dict(REQ, force=True))
        self.assertEqual(out["error"], "out_of_scope")
        self.assertEqual(out["workspace"], REQ["workspace"])  # exact preserved

    def test_non_dict_rejected(self):
        out = interop.submit_request("hbl-pnu.3.4")
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "invalid_request")


class WorkUnsupported(unittest.TestCase):
    def test_work_without_receipt_is_typed_unsupported(self):
        out = interop.submit_request(dict(REQ, intent="work"))
        self.assertFalse(out["ok"])
        self.assertEqual(out["status"], "unsupported")
        self.assertEqual(out["error"], "workflow_admission_unqualified")
        self.assertFalse(out["delivery"])
        self.assertTrue(out["no_dispatch"])
        self.assertIn("durable_work_admission", out["unknown_admission"])
        self.assertEqual(out["bead"], REQ["bead"])

    def test_fake_receipt_claiming_supported_is_refused(self):
        fake = {"qualified": True,
                "admission": {"exact_replay_key": "supported"},
                "receipt_id": "wf-1"}
        out = interop.submit_request(dict(REQ, intent="work",
                                          workflow_admission=fake))
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "unqualified_admission_claim")
        self.assertFalse(out["delivery"])

    def test_workflow_never_imported(self):
        self.assertFalse(any("hermes-workflows" in m or m == "wf"
                             for m in sys.modules))


class Qualification(unittest.TestCase):
    def test_records_primitives_and_unknown_admission(self):
        q = interop.qualify_workflow()
        self.assertEqual(q["note"], interop.NOTE)
        self.assertFalse(q["qualified_for_work"])
        self.assertTrue(q["admission"])
        self.assertTrue(all("unsupported" in v or "unknown" in v
                            for v in q["admission"].values()))

    def test_reads_actual_installed_signatures(self):
        q = interop.qualify_workflow()
        if not q["plugin_found"]:
            self.skipTest("workflow plugin not installed here")
        self.assertTrue(q["functions"]["act_run"]["signature"].startswith("def act_run("))
        self.assertTrue(q["functions"]["write_spawn_record"]["signature"].startswith(
            "def write_spawn_record("))
        self.assertIn("spawn_journal", "".join(q["supported_primitives"]))


if __name__ == "__main__":
    unittest.main()
