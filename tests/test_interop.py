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

    def test_empty_self_qualified_receipt_cannot_admit(self):
        out = interop.submit_request(dict(REQ, intent="work",
            workflow_admission={"qualified": True}))
        self.assertFalse(out["ok"])
        self.assertNotEqual(out.get("status"), "admitted")

    def test_workflow_never_imported(self):
        self.assertFalse(any("hermes-workflows" in m or m == "wf"
                             for m in sys.modules))


class MalformedReceipts(unittest.TestCase):
    """Structurally malformed receipts are refused before admission logic."""

    def _work(self, receipt):
        return interop.submit_request(dict(REQ, intent="work",
                                           workflow_admission=receipt))

    def test_non_dict_receipt_rejected(self):
        for bad in ("receipt-id-123", 42, ["qualified"], True):
            out = self._work(bad)
            self.assertFalse(out["ok"], f"receipt {bad!r} was not rejected")
            self.assertEqual(out["error"], "malformed_receipt")
            self.assertEqual(out["bead"], REQ["bead"])  # exact preserved
            self.assertFalse(out["delivery"])
            self.assertTrue(out["no_dispatch"])

    def test_non_bool_qualified_rejected(self):
        # A truthy string is not a boolean grant — caller flags are data.
        for bad in ("true", 1, {"yes": True}):
            out = self._work({"qualified": bad})
            self.assertEqual(out["error"], "malformed_receipt",
                             f"qualified={bad!r} accepted")

    def test_non_dict_admission_rejected(self):
        out = self._work({"qualified": True, "admission": "supported"})
        self.assertEqual(out["error"], "malformed_receipt")

    def test_missing_bead_preserved_on_malformed_receipt(self):
        out = interop.submit_request({"workspace": "/w", "intent": "work",
                                      "workflow_admission": "nope"})
        self.assertEqual(out["error"], "invalid_request")  # missing field first


class MissingWorkflowInstallation(unittest.TestCase):
    """When the Workflow plugin is NOT installed, qualification must degrade
    honestly: no primitives claimed, Work still typed unsupported."""

    def setUp(self):
        self._real = interop.WORKFLOWS_PLUGIN
        self._missing = pathlib.Path("/nonexistent/workflows-plugin-xyz")
        interop.WORKFLOWS_PLUGIN = self._missing

    def tearDown(self):
        interop.WORKFLOWS_PLUGIN = self._real

    def test_qualify_reports_not_found_without_primitives(self):
        q = interop.qualify_workflow()
        self.assertFalse(q["plugin_found"])
        self.assertIsNone(q["functions"]["act_run"]["signature"])
        self.assertEqual(q["supported_primitives"], [])
        self.assertFalse(q["qualified_for_work"])
        self.assertEqual(q["note"], interop.NOTE)

    def test_work_without_plugin_stays_typed_unsupported(self):
        out = interop.submit_request(dict(REQ, intent="work"))
        self.assertFalse(out["ok"])
        self.assertEqual(out["status"], "unsupported")
        self.assertEqual(out["error"], "workflow_admission_unqualified")
        self.assertFalse(out["delivery"])
        self.assertTrue(out["no_dispatch"])
        # still carries the bounded unsupported-admission evidence
        self.assertIn("durable_work_admission", out["unknown_admission"])

    def test_fake_receipt_without_plugin_still_refused(self):
        out = interop.submit_request(dict(
            REQ, intent="work",
            workflow_admission={"qualified": True,
                                "admission": {"durable_work_admission": "supported"}}))
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "unqualified_admission_claim")


class DoorClassification(unittest.TestCase):
    """Reopened I0: every named C4/C5 door has an exact classification and
    bounded evidence; nothing is 'supported' from a function name."""

    NAMED_DOORS = (
        "c5_session_door_ask_refine",
        "c4_admission_door",
        "c4_exact_replay_key",
        "c4_durable_grant_replay_receipt",
        "c4_pre_popen_launch_intent",
        "c4_authenticated_principal_check",
        "c4_runner_owned_progression",
        "c4_no_reminder_recovery",
        "c4_cancellation",
        "c4_durable_cancellation",
        "c4_liveness",
        "c4_restart_recovery",
    )

    @classmethod
    def setUpClass(cls):
        cls.ev = interop.capture_evidence()

    def test_every_named_door_classified_with_evidence(self):
        for door in self.NAMED_DOORS:
            d = self.ev["doors"][door]
            self.assertIn(d["classification"], interop.CLASSIFICATIONS, door)
            if d["classification"] == "source_observed_primitive":
                self.assertIn("evidence", d, door)      # bounded source evidence
            elif d["classification"] == "unsupported":
                self.assertIn("evidence", d, door)      # names the observed gap
            else:
                self.assertIn("evidence", d, door)      # states the UNKNOWN reason

    def test_nothing_claimed_qualified_runtime(self):
        # In this slice no live dispatch/session probe is permitted, so the
        # qualified_runtime bucket MUST stay empty — a filled bucket without
        # captured argv+output would be a fabricated claim.
        self.assertEqual(self.ev["qualified_runtime_doors"], [])

    def test_session_door_is_source_observed_not_supported(self):
        d = self.ev["doors"]["c5_session_door_ask_refine"]
        self.assertEqual(d["classification"], "source_observed_primitive")
        self.assertFalse(d["qualified_for_delivery"])
        probe = d["evidence"]["argv_probe"]
        self.assertTrue(probe["attempted"])
        self.assertEqual(probe["argv"], list(interop.SESSIONS_LIST_ARGV))
        # argv evidence is real capture, not a name-based claim
        if probe.get("returncode") is not None:
            self.assertIsInstance(probe["returncode"], int)
            self.assertIn("stdout_excerpt", probe)

    def test_hash_pairs_cover_exact_scope_only(self):
        self.assertTrue(self.ev["hash_pairs_unchanged"])
        self.assertEqual(set(self.ev["hash_before"]),
                         set(interop.INSPECTED_FILES))
        self.assertEqual(self.ev["hash_before"], self.ev["hash_after"])
        for h in self.ev["hash_before"].values():
            self.assertIsNotNone(h)
            self.assertEqual(len(h), 64)  # SHA-256 hex
        self.assertIn("EXACTLY the enumerated", self.ev["hash_scope_note"])

    def test_git_tracked_diff_proven_or_reported_partial(self):
        for path, st in self.ev["git_tracked_diff"].items():
            if st["is_git_repo"]:
                self.assertTrue(st["tracked_diff_proven"], path)
            else:
                # never a silent pass: absence of a repo must be declared
                self.assertTrue(st["verdict"].startswith("partial:"), path)
                self.assertFalse(st["tracked_diff_proven"])

    def test_ask_refine_delivery_flag_unqualified(self):
        out = interop.submit_request(dict(REQ, intent="ask"))
        self.assertFalse(out["delivery_qualified"])
        self.assertFalse(out["delivery"])
        self.assertEqual(out["route"], "hermes_session_door")

    def test_evidence_carries_argv_signature_and_body(self):
        """Bounded exact argv + signature + body evidence for inspected
        primitives — the reopened acceptance item (names are not evidence)."""
        f = self.ev["functions"]
        self.assertTrue(f["act_run"]["signature"].startswith("def act_run("))
        self.assertIn("time.strftime", f["act_run"]["body_excerpt"]
                      + str(f["act_run"]["signature"]))
        # Hermes-side door symbols carry the same bounded evidence shape
        for label in ("cmd_sessions", "get_or_create_session",
                      "SubagentLifecycleManager.cancel"):
            self.assertIsNotNone(f[label]["signature"], label)
            self.assertIsInstance(f[label]["body_lineno"], int, label)
            self.assertTrue(f[label]["body_excerpt"], label)
        probe = self.ev["doors"]["c5_session_door_ask_refine"]["evidence"]["argv_probe"]
        self.assertEqual(probe["argv"], list(interop.SESSIONS_LIST_ARGV))
        if probe["attempted"] and probe.get("returncode") is not None:
            self.assertEqual(probe["returncode"], 0,
                             "read-only sessions list probe must succeed on this host")


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
