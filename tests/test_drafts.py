#!/usr/bin/env python3
"""Draft-store contract tests () — Python wrapper.

Two jobs:

1. Execute the Node draft suite (tests/test_drafts.mjs) via the built-in
   runner and surface its per-test results here.
2. Native, executable evidence for the replacement contract against REAL
   disposable stores (unique runtime fixtures under
   tests/fixtures/drafts-runtime, git-ignored):
     - native concurrent descriptions with UNCHANGED owner/status are NOT
       expected to exit 13: the assignee/status guards cannot see a
       description-only change, so the second blind write exits 0 (proved,
       not asserted from the doc);
     - CONTENT_CAS_SUPPORTED is False in the shipped module — Save must be
       disabled and no fake capability success may exist;
     - the JS draft key is exactly prefix + model.mjs storeIdentityKey +
       '|' + bead id (single keying truth across model/history/drafts).

No new write backend: submissions go through write_protocol's guarded paths
only; this wrapper never edits a description except via the documented
unsafe-blind demonstration (isolated fixture stores, own git root).

Run: python3 tests/test_drafts.py
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

BD_BIN = os.environ.get("BEADS_LAB_BD",
                        "/home/hermes/.hermes/work/beads-lab/bin/bd")
RUNTIME_FIXTURE_ROOT = os.path.join(HERE, "fixtures", "drafts-runtime")

CAPABILITY_KEY_PREFIX = "hbl.draft.v1:"


def node(argv_args):
    return subprocess.run(["node"] + argv_args, capture_output=True, text=True)


def bd(store, args, actor=None, readonly=False):
    argv = [BD_BIN, "-C", store]
    if actor:
        argv += ["--actor", actor]
    if readonly:
        argv += ["--readonly"]
    argv += args
    return subprocess.run(argv, capture_output=True, text=True)


def make_store():
    """Disposable store with its OWN git root (embedded-dolt home resolves
    to the nearest parent git root; sharing the lane repo would leak)."""
    os.makedirs(RUNTIME_FIXTURE_ROOT, exist_ok=True)
    d = tempfile.mkdtemp(prefix=f"run-{uuid.uuid4().hex[:8]}-", dir=RUNTIME_FIXTURE_ROOT)
    subprocess.run(["git", "init", "-q", "."], cwd=d, check=True,
                   capture_output=True)
    p = subprocess.run([BD_BIN, "init", "--prefix", "drf"], cwd=d,
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


class NodeSuiteExecution(unittest.TestCase):
    """The Node draft suite must be green; executed, not trusted."""

    def test_node_draft_suite_green(self):
        p = node(["--test", os.path.join(HERE, "test_drafts.mjs")])
        tail = (p.stdout + p.stderr)[-3000:]
        self.assertEqual(p.returncode, 0, f"node --test failed:\n{tail}")
        m = re.search(r"ℹ pass (\d+)", p.stdout)
        f = re.search(r"ℹ fail (\d+)", p.stdout)
        self.assertIsNotNone(m, "could not parse node test counts")
        self.assertGreaterEqual(int(m.group(1)), 14)
        self.assertEqual(int(f.group(1)), 0)

    def test_pinned_draft_key_matches_model_store_identity(self):
        # JS: draftKey == CAPABILITY_KEY_PREFIX + storeIdentityKey + '|' + bead
        script = f"""
        import {{draftKey}} from {json.dumps(os.path.join(HERE, '..', 'desktop', 'drafts.mjs'))};
        import {{storeIdentityKey}} from {json.dumps(os.path.join(HERE, '..', 'desktop', 'model.mjs'))};
        const si = {{workspace: "/lab/storeA", db: "/lab/storeA/.beads/lab.db"}};
        console.log(JSON.stringify({{
          dk: draftKey(si, "abc"),
          expect: {json.dumps(CAPABILITY_KEY_PREFIX)} + storeIdentityKey(si) + "|abc",
          cas: (await import({json.dumps(os.path.join(HERE, '..', 'desktop', 'drafts.mjs'))})).CONTENT_CAS_SUPPORTED,
        }}));
        """
        p = node(["--input-type=module", "-e", script])
        self.assertEqual(p.returncode, 0, p.stderr[-2000:])
        out = json.loads(p.stdout.strip().splitlines()[-1])
        self.assertEqual(out["dk"], out["expect"])
        self.assertIs(out["cas"], False)


class NativeConcurrencyContract(unittest.TestCase):
    """Executable proof of the guard blind spot on real stores."""

    def setUp(self):
        self.store = make_store()
        self.addCleanup(shutil.rmtree, self.store, True)

    def test_concurrent_description_change_owner_status_unchanged_not_exit13(self):
        iid = create(self.store, "concurrent description", "BASE")
        before = read(self.store, iid)
        # Actor A edits description only (owner/status unchanged).
        pa = bd(self.store, ["update", iid, "--description", "A-EDIT", "--json"],
                actor="actor-a")
        self.assertEqual(pa.returncode, 0, pa.stderr)
        # Actor B — with the ACTUAL available guards, satisfied by the
        # unchanged owner/status — also writes: this is the documented blind
        # spot. NOT expected to exit 13.
        pb = bd(self.store, ["update", iid, "--description", "B-EDIT",
                             "--if-assignee", before.get("assignee") or "",
                             "--if-status", before["status"], "--json"],
                actor="actor-b")
        self.assertNotEqual(pb.returncode, 13,
                            "guards unexpectedly detected a description-only change")
        self.assertEqual(pb.returncode, 0, pb.stderr)
        after = read(self.store, iid)
        self.assertEqual(after["description"], "B-EDIT")
        self.assertEqual(after.get("assignee"), before.get("assignee"))
        self.assertEqual(after["status"], before["status"])

    def test_guard_mismatch_control_still_exits_13(self):
        # Control: the guards DO work for their fields — proves the previous
        # test's exit-0 is the blind spot, not broken guards.
        iid = create(self.store, "guard control", "BASE")
        p = bd(self.store, ["update", iid, "--description", "X",
                            "--if-assignee", "definitely-not-them", "--json"],
               actor="actor-c")
        self.assertEqual(p.returncode, 13, p.stderr)
        self.assertEqual(read(self.store, iid)["description"], "BASE")

    def test_save_disabled_pinned_to_shipped_capability_flag(self):
        # Save disabled is data-driven from the shipped module flag; the
        # decision object never reports a fake capability success.
        script = f"""
        const m = await import({json.dumps(os.path.join(HERE, '..', 'desktop', 'drafts.mjs'))});
        const dec = m.editDecision({{contentCasSupported: m.CONTENT_CAS_SUPPORTED}});
        const d = m.createDraftStore({{}});
        d.saveDraft({{workspace: "/w", db: "/w/.beads/x.db"}}, "b1", "keep", {{baseText: "x"}});
        const res = m.runContentSave(d, {{workspace: "/w", db: "/w/.beads/x.db"}}, "b1", {{
          contentCasSupported: m.CONTENT_CAS_SUPPORTED,
          appendSuggestion: () => ({{ok: true, commentId: "s1"}}),
        }});
        console.log(JSON.stringify({{
          save: dec.saveContent.enabled,
          suggestion: dec.appendSuggestion.enabled,
          draft: dec.draft.enabled,
          saved: res.saved, reason: res.reason, sugg: res.suggestion,
          draftKept: d.getDraft({{workspace: "/w", db: "/w/.beads/x.db"}}, "b1")?.text ?? null,
        }}));
        """
        p = node(["--input-type=module", "-e", script])
        self.assertEqual(p.returncode, 0, p.stderr[-2000:])
        out = json.loads(p.stdout.strip().splitlines()[-1])
        self.assertIs(out["save"], False)
        self.assertIs(out["suggestion"], True)
        self.assertIs(out["draft"], True)
        self.assertIs(out["saved"], False)
        self.assertEqual(out["reason"], "unsupported:no-atomic-content-guard")
        self.assertEqual(out["sugg"], "s1")
        self.assertEqual(out["draftKept"], "keep")


class StaticContractPins(unittest.TestCase):
    """Docs own capability truth; no second write backend may appear."""

    def test_docs_contract_owns_capability_truth(self):
        # Capability truth lives inline where the boundary is crossed.
        src = os.path.join(os.path.dirname(HERE), "beads",
                           "write_protocol.py")
        with open(src, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("unsupported", text.lower())
        self.assertIn("--if-assignee", text)

    def test_drafts_module_declares_no_second_backend(self):
        src = os.path.join(os.path.dirname(HERE), "desktop", "drafts.mjs")
        with open(src, encoding="utf-8") as f:
            code = f.read()
        for banned in ("node:fs", "node:child_process", "child_process",
                       "localStorage", "indexedDB", "window.", "document.",
                       "fetch(", "XMLHttpRequest", "require("):
            self.assertNotIn(banned, code,
                             f"drafts.mjs must stay pure; found {banned!r}")
        self.assertIn("export const CONTENT_CAS_SUPPORTED = false", code)

    def test_runtime_fixture_root_is_git_ignored(self):
        gi = os.path.join(os.path.dirname(HERE), ".gitignore")
        with open(gi, encoding="utf-8") as f:
            lines = [ln.strip() for ln in f]
        self.assertIn("tests/fixtures/drafts-runtime/", lines)


if __name__ == "__main__":
    unittest.main(verbosity=2)
