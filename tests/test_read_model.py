"""Stdlib tests for read_model.py (hbl-pnu.1.1).

Two evidence classes, kept explicitly separate:

1. REAL PROCESSES (parity): the actual installed bd against disposable
   stores with their own `git init` (private dolt home — no fall-through to
   any parent repo's database). Frontier parity compares PARSED native
   fields (ids/status/labels/updated), never JSON serialization bytes
   (owner correction, 2026-09: parsed-field comparison overrides the stale
   byte-comparison sentence in the bead text).

2. SIMULATION (negative-process replay): recorded argv+stdout/exit_code
   from reports/interop-receipts.json replayed through a stubbed
   subprocess.run. These are labeled simulations — they verify our honest
   parsing of the recorded envelope (pipe-masked exit-1, exit-2 breaker),
   NOT live bd behavior.

Never mocks for anything claiming parity; never a stub for what is real.
Run: python3 tests/test_read_model.py
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import native        # noqa: E402
import read_model    # noqa: E402

BD_BIN = os.environ.get("BEADS_LAB_BD",
                        "/home/hermes/.hermes/work/beads-lab/bin/bd")
FIXTURE_ROOT = os.path.join(HERE, ".fixtures-reads")
RECEIPTS = "/home/hermes/.hermes/work/beads-lab/reports/interop-receipts.json"


def make_store(prefix="rdm"):
    """Fresh disposable store. Independent `git init` + one commit so the
    embedded dolt home binds INSIDE the fixture and can never fall through
    to a parent git-common-dir database."""
    d = tempfile.mkdtemp(dir=FIXTURE_ROOT)
    subprocess.run(["git", "init", "-q", "."], cwd=d, check=True,
                   capture_output=True)
    subprocess.run(["git", "-c", "user.name=fixture", "-c",
                    "user.email=fixture@localhost", "commit", "-q",
                    "--allow-empty", "-m", "fixture base"], cwd=d,
                   check=True, capture_output=True)
    p = subprocess.run([BD_BIN, "init", "--prefix", prefix], cwd=d,
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    return d


def bd_cli(store, *args):
    """Raw same-shell bd invocation (no pipe — exit code is the real one)."""
    return subprocess.run([BD_BIN, *args], cwd=store, capture_output=True,
                          text=True)


def seed(store, title, *extra):
    p = bd_cli(store, "create", title, "--json", *extra)
    assert p.returncode == 0, p.stderr
    rows = json.loads(p.stdout)
    return (rows[0] if isinstance(rows, list) else rows)["id"]


class SimulationReplay(unittest.TestCase):
    """SIMULATION, explicitly: recorded argv+stdout/exit_code from
    reports/interop-receipts.json replayed via a stub. Verifies the facade
    + boundary parse the RECORDED envelope honestly; not live-bd evidence."""

    def setUp(self):
        self.store = make_store()

    def _stub(self, stdout, exit_code, stderr=""):
        class FakeProc:
            def __init__(self):
                self.stdout = stdout
                self.returncode = exit_code
                self.stderr = stderr
        proc = FakeProc()
        captured = {}

        def fake_run(argv, **kw):
            captured["argv"] = list(argv)
            return proc
        return fake_run, captured

    def test_receipt_replay_pipe_masked_exit1_is_error_not_empty(self):
        # Recorded receipt Q3-claim-child-distinct-actor: exit 1 with prose
        # + JSON body on stdout (the pipe-masked failure shape). If a read
        # route ever hits this envelope it must surface as a named error
        # with the envelope preserved — never an empty success.
        receipts = json.load(open(RECEIPTS))
        rec = next(r for r in receipts
                   if r["label"] == "Q3-claim-child-distinct-actor")
        self.assertEqual(rec["exit_code"], 1)
        fake, captured = self._stub(rec["stdout"], 1)
        real = native.subprocess.run
        native.subprocess.run = fake
        try:
            with self.assertRaises(native.JsonParseError) as cm:
                read_model.comments(self.store, "hbl-x3s.1", bd_bin=BD_BIN)
        finally:
            native.subprocess.run = real
        exc = cm.exception
        self.assertEqual(exc.exit_code, 1)                  # honest exit
        self.assertIn("already claimed by lab-interop", exc.stdout)  # envelope verbatim
        self.assertEqual(captured["argv"][0], BD_BIN)
        self.assertIn("--readonly", captured["argv"])       # reads are readonly-flagged

    def test_receipt_replay_stdout_failed_body_exit0_is_rejected(self):
        # Failure contract replay: an exit-0 body carrying failed[] (shape
        # recorded on Q3's JSON tail) must never read as success.
        fake, _ = self._stub('{"failed":[{"id":"hbl-x3s.1","error":"boom"}],'
                             '"schema_version":1}', 0)
        real = native.subprocess.run
        native.subprocess.run = fake
        try:
            with self.assertRaises(native.JsonParseError):
                read_model.list_issues(self.store, bd_bin=BD_BIN)
        finally:
            native.subprocess.run = real

    def test_replay_exit2_is_circuit_breaker_not_empty(self):
        # Recorded breaker stderr (same wording observed live on bd 1.3.0):
        # exit 2 must map to CircuitBreakerError, never an empty list.
        fake, _ = self._stub(
            "", 2,
            "Error: too many rows: 2 found, --max-rows=1 exceeded.")
        real = native.subprocess.run
        native.subprocess.run = fake
        try:
            with self.assertRaises(native.CircuitBreakerError) as cm:
                read_model.list_issues(self.store, max_rows=1, bd_bin=BD_BIN)
        finally:
            native.subprocess.run = real
        self.assertEqual(cm.exception.exit_code, 2)

    def test_verbatim_preservation_of_unknown_values(self):
        # SIMULATION of an unknown-schema row: the facade must pass unknown
        # statuses/types/edge types through untouched (no whitelist, no fix).
        weird = [{"id": "z-1", "status": "quantum_leaped",
                  "issue_type": "flux", "unknown_field": {"deep": [1, 2]},
                  "edges": [{"type": "made-up-edge-kind", "to": "z-0"}]}]
        fake, _ = self._stub(json.dumps(weird), 0)
        real = native.subprocess.run
        native.subprocess.run = fake
        try:
            rows = read_model.ready(self.store, bd_bin=BD_BIN)
        finally:
            native.subprocess.run = real
        self.assertEqual(rows, weird)  # deep-equal, nothing normalized away


class BoundContract(unittest.TestCase):
    """Every read verb is bounded; unbounded usage is a named error."""

    def setUp(self):
        self.store = make_store()

    def test_unbounded_usage_refused(self):
        calls = [
            lambda: read_model.ready(self.store, limit=0, bd_bin=BD_BIN),
            lambda: read_model.ready(self.store, max_rows=0, bd_bin=BD_BIN),
            lambda: read_model.list_issues(self.store, limit=-1, bd_bin=BD_BIN),
            # bd history --limit 0 means ALL (probed) — facade must refuse it
            lambda: read_model.history(self.store, "x-1", limit=0, bd_bin=BD_BIN),
            lambda: read_model.comments(self.store, "x-1", limit=0, bd_bin=BD_BIN),
            lambda: read_model.blocked(self.store, limit=0, bd_bin=BD_BIN),
            lambda: read_model.query_parent(self.store, "x-1", limit=0, bd_bin=BD_BIN),
        ]
        for call in calls:
            with self.assertRaises(read_model.ReadModelError):
                call()

    def test_empty_ids_refused_before_any_process(self):
        for call in (
            lambda: read_model.show(self.store, "", bd_bin=BD_BIN),
            lambda: read_model.children(self.store, None, bd_bin=BD_BIN),
            lambda: read_model.query_parent(self.store, "", bd_bin=BD_BIN),
            lambda: read_model.comments(self.store, "", bd_bin=BD_BIN),
            lambda: read_model.history(self.store, "", bd_bin=BD_BIN),
        ):
            with self.assertRaises(read_model.ReadModelError):
                call()

    def test_fixed_argv_shape_no_shell_composition(self):
        captured = {}
        class FakeProc:
            returncode, stderr, stdout = 0, "", "[]"
        def fake_run(argv, **kw):
            captured["argv"] = list(argv)
            return FakeProc()
        real = native.subprocess.run
        native.subprocess.run = fake_run
        try:
            read_model.ready(self.store, label="l; rm -rf /", bd_bin=BD_BIN)
        finally:
            native.subprocess.run = real
        argv = captured["argv"]
        self.assertIsInstance(argv, list)
        self.assertTrue(all(isinstance(t, str) for t in argv))
        # hostile value stayed ONE token in its position
        self.assertIn("l; rm -rf /", argv)
        # spec argv: fixed ready shape
        self.assertEqual(argv[1:], ["--readonly", "ready", "--json",
                                    "--exclude-type=epic", "-n", "100",
                                    "--max-rows", "1000",
                                    "--label", "l; rm -rf /"])


class RealStoreParity(unittest.TestCase):
    """REAL PROCESSES: actual installed bd, fresh fixture store. Parsed-
    field comparison (owner contract), never serialized-byte comparison."""

    def setUp(self):
        self.store = make_store()

    def _seed_tree(self):
        epic = seed(self.store, "parent epic", "-t", "epic")
        kid_a = seed(self.store, "kid a", "--parent", epic)
        kid_b = seed(self.store, "kid b", "--parent", epic)
        blocker = seed(self.store, "blocker")
        # kid_b blocked by a real dependency on `blocker`
        p = bd_cli(self.store, "dep", "add", kid_b, blocker, "--json")
        assert p.returncode == 0, p.stderr
        return epic, kid_a, kid_b, blocker

    def _comparable(self, rows):
        """Parsed-field projection: native identities/fields, key-sorted so
        serialization order can never be the comparison."""
        return sorted(rows, key=lambda r: r.get("id") or "")

    def test_frontier_parity_same_shell_native_ready(self):
        # Task-targeted verification: plugin frontier == same-shell
        # `bd ready --json --exclude-type=epic` on a fresh fixture store.
        # Comparison is on PARSED native fields (owner correction).
        epic, kid_a, kid_b, blocker = self._seed_tree()
        shell = bd_cli(self.store, "ready", "--json", "--exclude-type=epic")
        self.assertEqual(shell.returncode, 0, shell.stderr)
        shell_rows = json.loads(shell.stdout)
        got = read_model.ready(self.store, bd_bin=BD_BIN)
        self.assertEqual(self._comparable(got), self._comparable(shell_rows))
        # frontier semantics preserved from native: blocked kid is out
        ids = {r["id"] for r in got}
        self.assertIn(kid_a, ids)
        self.assertNotIn(kid_b, ids)     # blocked by dep — native semantics
        self.assertNotIn(epic, ids)      # epic excluded
        # freshness + workspace identity shown (owner contract)
        ident = read_model.info(self.store, bd_bin=BD_BIN)
        self.assertEqual(ident["workspace"], os.path.realpath(self.store))
        self.assertTrue(ident["observed_at"])
        self.assertIn("1.3.0", ident["bd_version"])

    def test_show_list_children_query_parent_parity(self):
        epic, kid_a, kid_b, blocker = self._seed_tree()

        row = read_model.show(self.store, kid_a, bd_bin=BD_BIN)
        self.assertEqual(row["id"], kid_a)
        self.assertEqual(row["title"], "kid a")   # native fields verbatim

        shell = bd_cli(self.store, "list", "--json", "-n", "100")
        self.assertEqual(shell.returncode, 0, shell.stderr)
        got = read_model.list_issues(self.store, bd_bin=BD_BIN)
        self.assertEqual(self._comparable(got),
                         self._comparable(json.loads(shell.stdout)))

        shell = bd_cli(self.store, "children", epic, "--json")
        self.assertEqual(shell.returncode == 0, True, shell.stderr)
        got = read_model.children(self.store, epic, bd_bin=BD_BIN)
        self.assertEqual(self._comparable(got),
                         self._comparable(json.loads(shell.stdout)))
        self.assertEqual({r["id"] for r in got}, {kid_a, kid_b})

        got = read_model.query_parent(self.store, epic, bd_bin=BD_BIN)
        self.assertEqual(self._comparable(got),
                         self._comparable(json.loads(shell.stdout)))

    def test_children_include_closed_like_native_alias(self):
        # bd children == `list --parent X --status all` (documented alias);
        # closed kids must still appear (no readiness reconstruction).
        epic, kid_a, kid_b, blocker = self._seed_tree()
        p = bd_cli(self.store, "close", kid_a, "--reason", "done")
        self.assertEqual(p.returncode == 0, True, p.stderr)
        shell = bd_cli(self.store, "children", epic, "--json")
        got = read_model.children(self.store, epic, bd_bin=BD_BIN)
        self.assertEqual(self._comparable(got),
                         self._comparable(json.loads(shell.stdout)))
        self.assertEqual({(r["id"], r["status"]) for r in got},
                         {(kid_a, "closed"), (kid_b, "open")})

    def test_blocked_uses_native_semantics_with_client_bound(self):
        epic, kid_a, kid_b, blocker = self._seed_tree()
        shell = bd_cli(self.store, "blocked", "--json")
        self.assertEqual(shell.returncode == 0, True, shell.stderr)
        native_rows = json.loads(shell.stdout)
        self.assertEqual({r["id"] for r in native_rows}, {kid_b})
        got = read_model.blocked(self.store, bd_bin=BD_BIN)
        self.assertEqual(self._comparable(got), self._comparable(native_rows))
        # honest client bound (bd has no --max-rows here — probed)
        seed(self.store, "x1")
        b2 = seed(self.store, "parent2", "-t", "epic")
        k2 = seed(self.store, "kid blocked 2", "--parent", b2)
        p = bd_cli(self.store, "dep", "add", k2, blocker)
        self.assertEqual(p.returncode == 0, True, p.stderr)
        with self.assertRaises(read_model.ClientBoundError) as cm:
            read_model.blocked(self.store, limit=1, bd_bin=BD_BIN)
        self.assertEqual(cm.exception.verb, "blocked")  # ours, not exit 2

    def test_comments_history_parity(self):
        epic, kid_a, kid_b, blocker = self._seed_tree()
        p = bd_cli(self.store, "comment", kid_a, "first note")
        self.assertEqual(p.returncode == 0, True, p.stderr)
        p = bd_cli(self.store, "comment", kid_a, "second note")
        self.assertEqual(p.returncode == 0, True, p.stderr)
        shell = bd_cli(self.store, "comments", kid_a, "--json")
        self.assertEqual(shell.returncode == 0, True, shell.stderr)
        got = read_model.comments(self.store, kid_a, bd_bin=BD_BIN)
        self.assertEqual({r["text"] for r in got},
                         {r["text"] for r in json.loads(shell.stdout)})
        # history: real bounded call, parsed rows
        h = read_model.history(self.store, kid_a, limit=5, bd_bin=BD_BIN)
        self.assertTrue(len(h) >= 1)
        self.assertTrue(all("Issue" in row or "CommitHash" in row for row in h))
        # empty comments == native [] (not None, not error)
        self.assertEqual(read_model.comments(self.store, kid_b, bd_bin=BD_BIN), [])

    def test_missing_id_preserves_native_error_envelope(self):
        # bd exits 1 with {"error":...} on stdout for a missing ID; the
        # named error must carry that envelope verbatim (no invention).
        with self.assertRaises(native.BdCommandError) as cm:
            read_model.show(self.store, "rdm-nope-nope", bd_bin=BD_BIN)
        self.assertIn("no issues found", cm.exception.stdout.lower())
        self.assertEqual(cm.exception.exit_code, 1)

    def test_live_circuit_breaker_native_semantics(self):
        # REAL exit 2 through the facade where bd enforces it (list):
        # breaker error, never an empty success.
        for i in range(3):
            seed(self.store, f"flood {i}")
        with self.assertRaises(native.CircuitBreakerError) as cm:
            read_model.list_issues(self.store, max_rows=1, bd_bin=BD_BIN)
        self.assertEqual(cm.exception.exit_code, 2)

    def test_hostile_inputs_fail_named_never_empty(self):
        with self.assertRaises(native.BdNotFoundError):
            read_model.ready(self.store, bd_bin="/nonexistent/bd")
        with self.assertRaises(native.WorkspaceError):
            read_model.ready("/nonexistent/dir-at-all", bd_bin=BD_BIN)


if __name__ == "__main__":
    os.makedirs(FIXTURE_ROOT, exist_ok=True)
    unittest.main(verbosity=2)
