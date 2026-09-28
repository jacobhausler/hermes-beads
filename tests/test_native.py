"""Stdlib tests for the native bd boundary (hbl-pnu.1.6 / N0).

Real evidence, not mocks: every positive case runs the ACTUAL installed bd
against a disposable store created under tests/.fixtures (never the real
Hermes home, never the planning store). Each test owns its store, so no test
inherits another's claim state. Negative controls force the named errors.
Run: python3 tests/test_native.py
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import native  # noqa: E402

BD_BIN = os.environ.get("BEADS_LAB_BD",
                        "/home/hermes/.hermes/work/beads-lab/bin/bd")
FIXTURE_ROOT = os.path.join(HERE, ".fixtures")


def make_store():
    """Fresh disposable store with its own .beads dir.

    The embedded-dolt home binds to the nearest git root: without its own .git
    a fixture store would share the parent repo's dolt databases. A private
    `git init` isolates the store inside the fixture dir itself.
    """
    d = tempfile.mkdtemp(dir=FIXTURE_ROOT)
    subprocess.run(["git", "init", "-q", "."], cwd=d, check=True,
                   capture_output=True)
    p = subprocess.run([BD_BIN, "init", "--prefix", "tst"], cwd=d,
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    return d


def seed(store, title="work item"):
    p = subprocess.run([BD_BIN, "create", title, "--json"], cwd=store,
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    rows = json.loads(p.stdout)
    return (rows[0] if isinstance(rows, list) else rows)["id"]


class BoundaryContract(unittest.TestCase):
    def test_argv_must_be_fixed_list(self):
        with self.assertRaises(ValueError):
            native.run_bd("ready --json", workspace="/")

    def test_absent_bd_is_named_error_not_empty(self):
        store = make_store()
        with self.assertRaises(native.BdNotFoundError):
            native.ready_frontier(store, bd_bin="/nonexistent/bd-binary")

    def test_hostile_workspace_named_errors(self):
        with self.assertRaises(native.WorkspaceError):
            native.ready_frontier("relative/path")
        with self.assertRaises(native.WorkspaceError):
            native.ready_frontier("/nonexistent/dir-at-all")
        empty = tempfile.mkdtemp(dir=FIXTURE_ROOT)
        with self.assertRaises(native.WorkspaceError):
            native.ready_frontier(empty)


class RealStoreSmoke(unittest.TestCase):
    """Positive controls against the actual installed binary (bd 1.3.0)."""

    def setUp(self):
        self.store = make_store()

    def test_version_and_smoke_receipt(self):
        id_a, id_b = seed(self.store, "alpha"), seed(self.store, "beta")
        receipt = native.smoke(self.store, bd_bin=BD_BIN)
        self.assertTrue(receipt["ok"])
        self.assertIn("1.3.0", receipt["bd_version"])  # observed installed version
        self.assertEqual(set(receipt["ready_ids"]), {id_a, id_b})

    def test_store_info_identity(self):
        info = native.store_info(self.store, bd_bin=BD_BIN)
        self.assertEqual(info["workspace"], os.path.realpath(self.store))
        # exact pair for H1's storeIdentityKey: real parsed database path,
        # from native `bd info --json` output mode, not plain text
        self.assertTrue(info["db"], "store_info must return parsed database_path")
        self.assertTrue(os.path.isabs(info["db"]))
        self.assertIn(".beads", info["db"])

    def test_exit0_failed_json_is_rejected(self):
        # Failure contract: exit-0 stdout carrying failed[]/error is never a
        # success. Inject a fake proc (real bd refuses via exit 1; this guards
        # the boundary against any path that exits 0 with a failure body).
        class FakeProc:
            returncode, stderr = 0, ""
            stdout = '{"failed": [{"id": "x", "error": "boom"}]}'
        real = native.subprocess.run
        native.subprocess.run = lambda *a, **k: FakeProc()
        try:
            with self.assertRaises(native.JsonParseError):
                native.run_bd(["update", "x", "--notes", "n", "--json"],
                              workspace=self.store, bd_bin=BD_BIN)
        finally:
            native.subprocess.run = real
        class FakeErr(FakeProc):
            stdout = '{"error": "silent refusal"}'
        native.subprocess.run = lambda *a, **k: FakeErr()
        try:
            with self.assertRaises(native.JsonParseError):
                native.run_bd(["update", "x", "--notes", "n", "--json"],
                              workspace=self.store, bd_bin=BD_BIN)
        finally:
            native.subprocess.run = real

    def test_show_returns_exact_id(self):
        issue = seed(self.store, "shown")
        row = native.show(self.store, issue, bd_bin=BD_BIN)
        self.assertEqual(row["id"], issue)
        self.assertEqual(row["status"], "open")

    def test_ready_scoped_by_label_is_honest_empty(self):
        seed(self.store, "unlabeled")
        self.assertEqual(
            native.ready_frontier(self.store, bd_bin=BD_BIN, label="no-such-label"), [])

    def test_max_rows_circuit_breaker_is_error_not_empty(self):
        seed(self.store, "one")
        seed(self.store, "two")
        # Verified on `list` (bd 1.3.0 honors --max-rows there; `ready` does
        # not enforce it — recorded as contract friction in the capability doc).
        with self.assertRaises(native.CircuitBreakerError) as cm:
            native.run_bd(["list", "--json", "--max-rows", "1"],
                          workspace=self.store, bd_bin=BD_BIN, readonly=True)
        self.assertEqual(cm.exception.exit_code, 2)

    def test_claim_readback_and_heartbeat(self):
        issue = seed(self.store, "claimable")
        actor = f"lab-native-test-{uuid.uuid4().hex[:8]}"
        row = native.claim(self.store, issue, bd_bin=BD_BIN, actor=actor)
        self.assertEqual(row["assignee"], actor)
        self.assertEqual(row["status"], "in_progress")
        self.assertTrue(row.get("lease_expires_at"))
        # same-actor re-claim is idempotent (observed bd 1.3.0 exit 0)
        again = native.claim(self.store, issue, bd_bin=BD_BIN, actor=actor)
        self.assertEqual(again["assignee"], actor)
        hb = native.heartbeat(self.store, issue, bd_bin=BD_BIN, actor=actor)
        self.assertEqual(hb.get("status"), "heartbeat")
        self.assertEqual(hb.get("owner"), actor)

    def test_claim_conflict_never_silent_steal(self):
        issue = seed(self.store, "contended")
        owner = f"owner-{uuid.uuid4().hex[:8]}"
        native.claim(self.store, issue, bd_bin=BD_BIN, actor=owner)
        thief = f"thief-{uuid.uuid4().hex[:8]}"
        try:
            native.claim(self.store, issue, bd_bin=BD_BIN, actor=thief)
        except native.BdCommandError:
            pass  # bd refused the claim (exit 1) — expected shape
        back = native.show(self.store, issue, bd_bin=BD_BIN)
        self.assertEqual(back["assignee"], owner)  # ownership never silently moved

    def test_guard_mismatch_exit_13_writes_nothing(self):
        issue = seed(self.store, "guarded")
        with self.assertRaises(native.GuardMismatchError) as cm:
            native.run_bd(["update", issue, "--if-assignee", "ghost-actor",
                           "--append-notes", "should not land", "--json"],
                          workspace=self.store, bd_bin=BD_BIN, actor="tester")
        self.assertEqual(cm.exception.exit_code, 13)
        back = native.show(self.store, issue, bd_bin=BD_BIN)
        self.assertNotIn("should not land", json.dumps(back))

    def test_readonly_blocks_writes(self):
        seed(self.store, "seeded")
        with self.assertRaises(native.BdCommandError) as cm:
            native.run_bd(["create", "sneaky write"], workspace=self.store,
                          bd_bin=BD_BIN, readonly=True, expect_json=False)
        self.assertEqual(cm.exception.exit_code, 1)


if __name__ == "__main__":
    os.makedirs(FIXTURE_ROOT, exist_ok=True)
    unittest.main(verbosity=2)
