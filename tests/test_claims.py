"""Stdlib tests for the claim/lease wrapper (hbl-pnu.1.2 / N2).

Real evidence, not mocks: every case runs the ACTUAL pinned bd (1.3.0)
against a fresh disposable fixture store under tests/.claims-fixtures
(gitignored; each fixture owns its own `git init` so the embedded-dolt home
never falls through to the lab repo's databases — the recorded trap). The
planning store is NEVER touched here.

Replays the recorded Q1-Q9 claim sequence (reports/interop-receipts.json):
  Q1 claim exit 0 -> read-back equality (assignee/status/lease_expires_at)
  Q2 same-actor re-claim idempotent (exit 0 path, lease honored)
  Q3 distinct-actor conflict names holder, ownership unmoved
  Q4/Q7 show read-back truth; former loser re-claim refused, holder intact
  Q5 holder release exit 0
  Q6 another actor claims after release
  Q8 CAS release with stale expectation refused, holder named, state intact
  Q9 holder releases
Plus the recorded hard facts this module encodes:
  - claim bypasses readiness (Q1 note: blocked descendant claimed exit 0) =>
    post-claim inspection must surface the open blocker as ambiguity;
  - eligibility-before-claim is NON-ATOMIC => never advertised as atomic;
  - same-actor re-claim is idempotent; conflicts are never retried blindly;
  - release is CAS-only (--if-assignee); no --force anywhere.
Run: python3 tests/test_claims.py
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
import claims  # noqa: E402
import native  # noqa: E402
import read_model  # noqa: E402

BD_BIN = os.environ.get("BEADS_LAB_BD",
                        "/home/hermes/.hermes/work/beads-lab/bin/bd")
FIXTURE_ROOT = os.path.join(HERE, ".claims-fixtures")


def make_store():
    """Fresh disposable store with its OWN git root.

    Independent `git init` is mandatory: without it the embedded-dolt home
    resolves to the nearest ancestor git root (the lab repo) and the fixture
    silently shares the lab databases (recorded git-common-dir fall-through).
    """
    os.makedirs(FIXTURE_ROOT, exist_ok=True)
    d = tempfile.mkdtemp(dir=FIXTURE_ROOT)
    subprocess.run(["git", "init", "-q", "."], cwd=d, check=True,
                   capture_output=True)
    p = subprocess.run([BD_BIN, "init", "--prefix", "clf"], cwd=d,
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    return d


def seed(store, title="work item"):
    p = subprocess.run([BD_BIN, "create", title, "--json"], cwd=store,
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    rows = json.loads(p.stdout)
    return (rows[0] if isinstance(rows, list) else rows)["id"]


def actor(tag="a"):
    return f"lab-claims-{tag}-{uuid.uuid4().hex[:8]}"


class Q1Q9SequenceReplay(unittest.TestCase):
    """The recorded interop claim sequence, replayed on a fresh store with
    read-back equality asserted after EVERY claim mutation."""

    def setUp(self):
        self.store = make_store()
        self.issue = seed(self.store, "worker task")

    def _assert_readback(self, actor, expected_status="in_progress"):
        back = read_model.show(self.store, self.issue, bd_bin=BD_BIN)
        self.assertEqual(back["status"], expected_status)
        if expected_status == "in_progress":
            self.assertEqual(back["assignee"], actor)
            self.assertTrue(back.get("lease_expires_at"),
                            "claim must carry a lease expiry (read-back field)")
        return back

    def test_q1_claim_readback_equality(self):
        a = actor("q1")
        row = claims.claim(self.store, self.issue, actor=a, bd_bin=BD_BIN)
        self.assertEqual(row["assignee"], a)
        self.assertEqual(row["status"], "in_progress")
        self._assert_readback(a)  # independent read-back equality

    def test_q2_same_actor_reclaim_idempotent(self):
        a = actor("q2")
        first = claims.claim(self.store, self.issue, actor=a, bd_bin=BD_BIN)
        again = claims.claim(self.store, self.issue, actor=a, bd_bin=BD_BIN)
        self.assertEqual(again["assignee"], a)
        self.assertEqual(again["status"], "in_progress")
        # lease unchanged by idempotent re-claim (observed bd fact: re-claim
        # does not push the lease; only heartbeat does)
        self.assertEqual(again.get("lease_expires_at"),
                         first.get("lease_expires_at"))

    def test_q3_distinct_actor_conflict_names_holder(self):
        owner, thief = actor("owner"), actor("thief")
        claims.claim(self.store, self.issue, actor=owner, bd_bin=BD_BIN)
        with self.assertRaises(claims.ClaimConflictError) as cm:
            claims.claim(self.store, self.issue, actor=thief, bd_bin=BD_BIN)
        self.assertEqual(cm.exception.holder, owner)  # holder named, not guessed
        back = self._assert_readback(owner)            # ownership never moved

    def test_q6_claim_after_release_by_another(self):
        a, b = actor("first"), actor("second")
        claims.claim(self.store, self.issue, actor=a, bd_bin=BD_BIN)
        released = claims.release(self.store, self.issue, actor=a, bd_bin=BD_BIN)
        self.assertEqual(released["status"], "open")
        self.assertNotIn("assignee", released, "unclaim must clear assignee")
        claims.claim(self.store, self.issue, actor=b, bd_bin=BD_BIN)
        self._assert_readback(b)

    def test_q7_former_loser_reclaim_refused_holder_intact(self):
        owner, loser = actor("owner"), actor("loser")
        claims.claim(self.store, self.issue, actor=owner, bd_bin=BD_BIN)
        claims.release(self.store, self.issue, actor=owner, bd_bin=BD_BIN)
        claims.claim(self.store, self.issue, actor=loser, bd_bin=BD_BIN)
        with self.assertRaises(claims.ClaimConflictError) as cm:
            claims.claim(self.store, self.issue, actor=owner, bd_bin=BD_BIN)
        self.assertEqual(cm.exception.holder, loser)
        self._assert_readback(loser)

    def test_q8_cas_release_mismatch_names_holder_writes_nothing(self):
        holder, stranger = actor("holder"), actor("stranger")
        claims.claim(self.store, self.issue, actor=holder, bd_bin=BD_BIN)
        # stranger's CAS names ITSELF (stranger): mismatch -> refusal naming
        # the real holder, state untouched (the Q8 envelope shape).
        with self.assertRaises(claims.ClaimConflictError) as cm:
            claims.release(self.store, self.issue, actor=stranger, bd_bin=BD_BIN)
        self.assertEqual(cm.exception.holder, holder)
        back = self._assert_readback(holder)
        self.assertEqual(back["status"], "in_progress")

    def test_q9_holder_release_succeeds(self):
        holder = actor("holder")
        claims.claim(self.store, self.issue, actor=holder, bd_bin=BD_BIN)
        row = claims.release(self.store, self.issue, actor=holder,
                             reason="probe done", bd_bin=BD_BIN)
        self.assertEqual(row["status"], "open")
        self.assertNotIn("assignee", row)
        # a released claim can be re-taken (loop closes)
        fresh = actor("fresh")
        claims.claim(self.store, self.issue, actor=fresh, bd_bin=BD_BIN)
        self._assert_readback(fresh)


class HeartbeatLifetime(unittest.TestCase):
    """Heartbeat is one call on the dispatch lifetime — no timers, no
    daemon, and losing the claim makes it fail loudly."""

    def setUp(self):
        self.store = make_store()
        self.issue = seed(self.store, "hb subject")

    def test_owner_heartbeat_refreshes_lease(self):
        a = actor("hb")
        row = claims.claim(self.store, self.issue, actor=a, bd_bin=BD_BIN)
        first_lease = row["lease_expires_at"]
        receipt = claims.heartbeat(self.store, self.issue, actor=a, bd_bin=BD_BIN)
        self.assertEqual(receipt.get("status"), "heartbeat")
        self.assertEqual(receipt.get("owner"), a)
        back = read_model.show(self.store, self.issue, bd_bin=BD_BIN)
        self.assertGreaterEqual(back["lease_expires_at"], first_lease)

    def test_non_owner_heartbeat_is_named_failure(self):
        owner, other = actor("own"), actor("noth")
        claims.claim(self.store, self.issue, actor=owner, bd_bin=BD_BIN)
        with self.assertRaises(native.BdCommandError):
            claims.heartbeat(self.store, self.issue, actor=other, bd_bin=BD_BIN)

    def test_heartbeat_after_release_survives_or_refuses_honestly(self):
        # After release the issue is open/unassigned: heartbeat by the former
        # owner must either fail (worker learns to stop) — never silently
        # resurrect a claim.
        a = actor("gone")
        claims.claim(self.store, self.issue, actor=a, bd_bin=BD_BIN)
        claims.release(self.store, self.issue, actor=a, bd_bin=BD_BIN)
        try:
            claims.heartbeat(self.store, self.issue, actor=a, bd_bin=BD_BIN)
            resurrected = False
        except native.BdCommandError:
            resurrected = False
        else:
            resurrected = read_model.show(
                self.store, self.issue, bd_bin=BD_BIN).get("assignee") == a
        self.assertFalse(resurrected,
                         "heartbeat must never silently re-own a released claim")


class RaceHonesty(unittest.TestCase):
    """The eligibility->claim window is non-atomic: the wrapper inspects
    AFTER claim and reports ambiguity with evidence instead of pretending."""

    def setUp(self):
        self.store = make_store()

    def test_claim_bypasses_readiness_and_surfaces_blocker(self):
        # Recorded fact: `--claim` bypasses readiness (blocked descendant
        # claimed exit 0). Our post-claim inspection must catch the open
        # blocker and refuse to call the claim plainly workable.
        blocker = seed(self.store, "prereq")
        blocked = seed(self.store, "blocked child")
        p = subprocess.run([BD_BIN, "dep", blocker, "--blocks", blocked],
                           cwd=self.store, capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        a = actor("race")
        self.assertFalse(claims.ready(self.store, blocked, bd_bin=BD_BIN))
        with self.assertRaises(claims.ClaimAmbiguityError) as cm:
            claims.claim(self.store, blocked, actor=a, bd_bin=BD_BIN)
        self.assertEqual(cm.exception.evidence["blockers"][0]["id"], blocker)
        # honest stop: the claim itself did land (bd's doing) — the error is
        # the disclosure, not a rollback we pretend to own.
        back = read_model.show(self.store, blocked, bd_bin=BD_BIN)
        self.assertEqual(back["assignee"], a)

    def test_ready_is_advisory_and_absent_after_claim(self):
        issue = seed(self.store, "advisory")
        a = actor("adv")
        self.assertTrue(claims.ready(self.store, issue, bd_bin=BD_BIN))
        claims.claim(self.store, issue, actor=a, bd_bin=BD_BIN)
        # a claimed bead leaves ready: absence is NOT an error, just no
        # longer dispatchable (recorded ready-exclusion behavior).
        self.assertFalse(claims.ready(self.store, issue, bd_bin=BD_BIN))

    def test_blocked_then_closed_unblocks_clean_claim(self):
        blocker = seed(self.store, "prereq")
        issue = seed(self.store, "child")
        subprocess.run([BD_BIN, "dep", blocker, "--blocks", issue],
                       cwd=self.store, capture_output=True, text=True)
        a = actor("clean")
        p = subprocess.run([BD_BIN, "--actor", a, "close", blocker,
                            "--reason", "done", "--json"],
                           cwd=self.store, capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        row = claims.claim(self.store, issue, actor=a, bd_bin=BD_BIN)
        self.assertEqual(row["assignee"], a)  # closed blocker != ambiguity


class BoundaryContract(unittest.TestCase):
    def test_claim_requires_explicit_actor(self):
        store = make_store()
        issue = seed(store, "x")
        with self.assertRaises(ValueError):
            claims.claim(store, issue, actor="")
        with self.assertRaises(ValueError):
            claims.heartbeat(store, issue, actor="")
        with self.assertRaises(ValueError):
            claims.release(store, issue, actor="")

    def test_missing_issue_surfaces_named_native_error(self):
        store = make_store()
        with self.assertRaises(native.BdCommandError):
            claims.claim(store, "no-such-id-999", actor=actor("ghost"),
                         bd_bin=BD_BIN)

    def test_no_force_flags_anywhere(self):
        # EXCLUSION contract: this module must never reach for --force.
        with open(os.path.join(os.path.dirname(HERE), "claims.py")) as f:
            src = f.read()
        code = src.split('"""')[2]  # after the module docstring
        self.assertNotIn("--force", code)


if __name__ == "__main__":
    unittest.main(verbosity=2)
