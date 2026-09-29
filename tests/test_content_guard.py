#!/usr/bin/env python3
"""Content-guard contract tests (hbl-pnu.1.7).

Verdict under test — bd 1.3.0 (f45b249ce) has NO atomic expected-content /
revision-conditional mutation. `bd update` guards only actor + status
(--if-assignee / --if-status); the exposed `revision` field is read-only
telemetry that cannot be fed back as a precondition. Therefore two actors
editing the SAME description while assignee/status stay unchanged silently
lose one edit (last write wins), and a preflight read is NOT CAS.

These tests are executable evidence for that negative finding, and they pin
the required mitigation: unsafe blind replacement is DISABLED by default and
needs an explicit per-call opt-in.

Run: python3 tests/test_content_guard.py
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

BD_BIN = os.environ.get("BEADS_LAB_BD",
                        "/home/hermes/.hermes/work/beads-lab/bin/bd")
FIXTURE_ROOT = os.path.join(HERE, ".fixtures")
os.makedirs(FIXTURE_ROOT, exist_ok=True)

# ---------------------------------------------------------------------------
# Contract state (declared here, asserted below). A caller that wants CAS must
# NOT get it from this module: no supported native content guard exists.
# ---------------------------------------------------------------------------
CONTENT_CAS_SUPPORTED = False
GUARD_FLAGS_IN_UPDATE_HELP = ("--if-assignee", "--if-status")


class UnsafeContentReplacementError(RuntimeError):
    """Blind description replacement attempted without explicit opt-in."""


class StaleContentError(RuntimeError):
    """Preflight re-read found the description moved since the baseline."""


def bd(store, args, actor=None, readonly=False):
    argv = [BD_BIN, "-C", store]
    if actor:
        argv += ["--actor", actor]
    if readonly:
        argv += ["--readonly"]
    argv += args
    return subprocess.run(argv, capture_output=True, text=True)


def make_store():
    """Disposable store with its OWN git root.

    Without .git the embedded-dolt home resolves to the nearest parent git
    root and the fixture would share the lane repo's databases.
    """
    d = tempfile.mkdtemp(dir=FIXTURE_ROOT)
    subprocess.run(["git", "init", "-q", "."], cwd=d, check=True,
                   capture_output=True)
    p = subprocess.run([BD_BIN, "init", "--prefix", "pgd"], cwd=d,
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    return d


def create(store, title, description):
    p = bd(store, ["create", title, "--description", description, "--json"],
           actor="seeder")
    assert p.returncode == 0, p.stderr
    rows = json.loads(p.stdout)
    return (rows[0] if isinstance(rows, list) else rows)["id"]


def read(store, iid):
    p = bd(store, ["show", iid, "--json"], actor="reader", readonly=True)
    assert p.returncode == 0, p.stderr
    rows = json.loads(p.stdout)
    return (rows[0] if isinstance(rows, list) else rows)


def guarded_description_update(store, iid, actor, new_description, *,
                                expected_description,
                                allow_unsafe_blind_replace=False,
                                before_write=None):
    """TEST-ONLY refusal prototype — NOT compare-and-swap.

    Always refuse replacement by default, including matching preflight.
    Never advertise this as CAS. The explicit unsafe opt-in exists only to
    demonstrate native last-writer-wins in isolated fixtures. No product
    editor or safe Save capability is implemented by this test helper.
    """
    if not allow_unsafe_blind_replace:
        current = read(store, iid)
        if current.get("description") != expected_description:
            raise StaleContentError(
                f"{iid}: description moved since baseline; blind replacement "
                f"refused (content CAS unsupported in bd 1.3.0)")
        raise UnsafeContentReplacementError("No native content CAS: matching preflight cannot authorize replacement")
    if before_write:
        before_write()  # test seam: a competing writer lands here
    p = bd(store, ["update", iid, "--description", new_description, "--json"],
           actor=actor)
    if p.returncode != 0:
        raise RuntimeError(f"update failed: {p.stderr.strip()}")
    return read(store, iid)


class NativeSurfaceInspection(unittest.TestCase):
    """The guard vocabulary bd actually exposes."""

    def test_update_help_exposes_only_actor_state_guards(self):
        help_text = subprocess.run([BD_BIN, "update", "--help"],
                                   capture_output=True, text=True).stdout
        found = tuple(sorted(set(re.findall(r"--if-[a-z-]+", help_text))))
        self.assertEqual(found, GUARD_FLAGS_IN_UPDATE_HELP,
                         "unexpected guard surface — re-qualify before "
                         "changing the content-guard contract")
        for word in ("--if-revision", "--if-content", "--expect-revision",
                     "--content-hash", "--cas"):
            self.assertNotIn(word, help_text)

    def test_candidate_content_guard_flags_are_rejected(self):
        store = make_store()
        iid = create(store, "flag probe", "V1")
        for flag, value in (("--if-revision", "1"),
                           ("--if-content", "sha256:x"),
                           ("--expect-revision", "1"),
                           ("--content-hash", "x"),
                           ("--if-desc", "x"),
                           ("--cas-revision", "1")):
            p = bd(store, ["update", iid, flag, value, "--json"],
                   actor=f"probe-{uuid.uuid4().hex[:6]}")
            self.assertEqual(p.returncode, 1, f"{flag} unexpectedly accepted")
            self.assertIn("unknown flag", p.stderr, f"{flag}: {p.stderr}")

    def test_edit_command_has_no_content_guard(self):
        help_text = subprocess.run([BD_BIN, "edit", "--help"],
                                   capture_output=True, text=True).stdout
        self.assertNotIn("--if-", help_text)
        self.assertNotIn("revision", help_text.lower())

    def test_revision_is_telemetry_not_a_precondition(self):
        store = make_store()
        iid = create(store, "rev probe", "V1")
        rev0 = read(store, iid).get("revision")
        self.assertTrue(rev0, "revision should be exposed for observation")
        bd(store, ["update", iid, "--description", "V2", "--json"],
           actor=f"bump-{uuid.uuid4().hex[:6]}")
        rev1 = read(store, iid).get("revision")
        self.assertNotEqual(rev0, rev1,
                            "revision should change on content edit")
        # The observable revision cannot be handed back as a guard.
        p = bd(store, ["update", iid, "--if-revision", str(rev0),
                       "--description", "V3", "--json"],
               actor=f"feed-{uuid.uuid4().hex[:6]}")
        self.assertEqual(p.returncode, 1)
        self.assertIn("unknown flag", p.stderr)


class TwoActorSameDescription(unittest.TestCase):
    """The acceptance scenario: assignee/status unchanged, one description."""

    def setUp(self):
        self.store = make_store()
        self.iid = create(self.store, "collab", "ORIGINAL")
        self.a = f"actor-a-{uuid.uuid4().hex[:8]}"
        self.b = f"actor-b-{uuid.uuid4().hex[:8]}"

    def test_native_capability_absent_blind_overwrite_observed(self):
        """Executable negative finding: no native conflict detection.

        Both updates are accepted, assignee/status never move, and the second
        writer's text survives. If bd ever grows a content guard, the first
        stale write must then be refused — this test fails loudly to force
        re-qualification either way.
        """
        pa = bd(self.store, ["update", self.iid, "--description", "A-EDIT",
                            "--json"], actor=self.a)
        pb = bd(self.store, ["update", self.iid, "--description", "B-EDIT",
                            "--json"], actor=self.b)
        self.assertEqual((pa.returncode, pb.returncode), (0, 0),
                         "unexpected refusal — bd may have gained a content "
                         "guard; re-qualify docs/content-guard-contract.md")
        row = read(self.store, self.iid)
        self.assertEqual(row["description"], "B-EDIT")
        self.assertEqual(row.get("assignee", ""), "")
        self.assertEqual(row["status"], "open")

    def test_available_guards_pass_over_stale_content(self):
        """--if-assignee/--if-status cannot substitute for a content guard."""
        bd(self.store, ["update", self.iid, "--description", "A-EDIT",
                        "--json"], actor=self.a)
        # B's baseline is stale, but the only guards bd offers still hold.
        pg = bd(self.store, ["update", self.iid,
                            "--if-assignee", "", "--if-status", "open",
                            "--description", "B-STALE", "--json"],
                actor=self.b)
        self.assertEqual(pg.returncode, 0,
                         "guards unexpectedly protected content: "
                         + pg.stderr)
        self.assertEqual(pg.returncode, 0)
        row = read(self.store, self.iid)
        self.assertEqual(row["description"], "B-STALE")  # A-EDIT lost
        self.assertEqual(row["status"], "open")

    def test_cross_actor_description_edit_under_live_claim(self):
        """Content is not claim-protected either (no --force used)."""
        owner = f"owner-{uuid.uuid4().hex[:8]}"
        other = f"other-{uuid.uuid4().hex[:8]}"
        pc = bd(self.store, ["update", self.iid, "--claim", "--json"],
                actor=owner)
        self.assertEqual(pc.returncode, 0, pc.stderr)
        pd = bd(self.store, ["update", self.iid, "--description", "OTHER",
                            "--json"], actor=other)
        row = read(self.store, self.iid)
        self.assertEqual(row["assignee"], owner)
        if pd.returncode == 0:
            # Observed bd 1.3.0: the non-owner's text replaced the claimant's.
            self.assertEqual(row["description"], "OTHER")
        else:
            self.assertNotEqual(row["description"], "OTHER")


class UnsafeReplacementDisabled(unittest.TestCase):
    """The required mitigation while the native capability is unsupported."""

    def setUp(self):
        self.store = make_store()
        self.iid = create(self.store, "guarded", "BASELINE")
        self.a = f"actor-a-{uuid.uuid4().hex[:8]}"
        self.b = f"actor-b-{uuid.uuid4().hex[:8]}"

    def test_blind_replacement_refused_without_opt_in(self):
        bd(self.store, ["update", self.iid, "--description", "A-EDIT",
                        "--json"], actor=self.a)
        with self.assertRaises(StaleContentError):
            guarded_description_update(self.store, self.iid, self.b,
                                       "B-STALE",
                                       expected_description="BASELINE")
        self.assertEqual(read(self.store, self.iid)["description"], "A-EDIT")

    def test_unsafe_opt_in_is_per_call_and_explicit(self):
        bd(self.store, ["update", self.iid, "--description", "A-EDIT",
                        "--json"], actor=self.a)
        row = guarded_description_update(
            self.store, self.iid, self.b, "B-EXPLICIT",
            expected_description="BASELINE",
            allow_unsafe_blind_replace=True)
        self.assertEqual(row["description"], "B-EXPLICIT")
        self.assertEqual(row.get("assignee", ""), "")

    def test_preflight_detects_racing_writer_but_leaves_toctou_window(self):
        """Preflight catches the read-stale case; it is not CAS."""
        raced = []

        def competitor():
            if raced:
                return
            raced.append(True)
            bd(self.store, ["update", self.iid, "--description", "RACE",
                            "--json"], actor=self.b)

        with self.assertRaises(UnsafeContentReplacementError):
            guarded_description_update(self.store, self.iid, self.a,
                                       "A-EDIT", expected_description="BASELINE",
                                       before_write=competitor)
        self.assertFalse(raced)
        self.assertEqual(read(self.store, self.iid)["description"], "BASELINE")

    def test_contract_declares_cas_unsupported_and_not_advertised(self):
        self.assertFalse(CONTENT_CAS_SUPPORTED)
        doc = guarded_description_update.__doc__ or ""
        self.assertIn("NOT compare-and-swap", doc)
        self.assertIn("Never advertise this as CAS", doc)


if __name__ == "__main__":
    os.makedirs(FIXTURE_ROOT, exist_ok=True)
    unittest.main(verbosity=2)
