#!/usr/bin/env python3
"""hermes.workflow.* correlation convention tests (hbl-pnu.3.1).

Verdict under test — bd 1.3.0 (f45b249ce) stores namespaced dotted metadata
keys verbatim via `bd update --set-metadata key=value`, filters them with
`--metadata-field key=value` and `--has-metadata-key`, and the keys SURVIVE
status changes and closure (probe receipts: closed bead kept all three keys
and stayed findable with --all). Foreign keys are untouched by operations on
our namespace; `--unset-metadata` removes only the named key.

Real native evidence, no mocks: every case runs the ACTUAL pinned bd against
a fresh disposable fixture store under tests/fixtures/correlation-runtime
(unique-named per test, gitignored; each fixture owns its own `git init` so
the embedded-dolt home never falls through to the lab repo — the recorded
trap). The planning store is NEVER touched here.

Run: python3 tests/test_correlation.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
LANE = os.path.dirname(HERE)
sys.path.insert(0, LANE)

import native            # noqa: E402  (reuse anchor — not rewritten)
import read_model        # noqa: E402  (reuse anchor — not rewritten)
import write_protocol    # noqa: E402  (reuse anchor — not rewritten)
import correlation       # noqa: E402  (the module under test)

BD_BIN = os.environ.get("BEADS_LAB_BD",
                        "/home/hermes/.hermes/work/beads-lab/bin/bd")
FIXTURE_ROOT = os.path.join(HERE, "fixtures", "correlation-runtime")
RUNNER_ROOT = "/home/hermes/.hermes/plugins/hermes-workflows"


def make_store(case):
    d = tempfile.mkdtemp(dir=FIXTURE_ROOT, prefix=f"{case}-{uuid.uuid4().hex[:8]}-")
    subprocess.run(["git", "init", "-q", "."], cwd=d, check=True,
                   capture_output=True)
    p = subprocess.run([BD_BIN, "init", "--prefix", "cr"], cwd=d,
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    return d


def actor(prefix="corr"):
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def create(store, title, actor_name="seeder"):
    p = subprocess.run([BD_BIN, "-C", store, "--actor", actor_name,
                        "create", title, "--description", "d", "--json"],
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    rows = json.loads(p.stdout)
    return (rows[0] if isinstance(rows, list) else rows)["id"]


class CorrelationCase(unittest.TestCase):
    def store(self, case):
        d = make_store(case)
        self.addCleanup(shutil.rmtree, d, True)
        return d


class R4RoundTrip(CorrelationCase):
    """Interop R4: set -> filter by field -> filter by has-key -> survives close."""

    def test_full_round_trip(self):
        store = self.store("r4")
        iid = create(store, "R4 bead")
        written = correlation.attach_correlation(
            store, iid, actor=actor(), bd_bin=BD_BIN,
            run_id="run-r4", node_id="node-a", attempt=1,
            if_assignee="", if_status="open")
        got = {k: str(v) for k, v in written.items()}
        self.assertEqual(got, {"hermes.workflow.run_id": "run-r4",
                               "hermes.workflow.node_id": "node-a",
                               "hermes.workflow.attempt": "1"})

        # filter by exact field
        by_field = _list(store, ["--metadata-field",
                                 "hermes.workflow.run_id=run-r4"])
        self.assertEqual([r["id"] for r in by_field], [iid])

        # filter by has-key (presence query, any value)
        by_key = _list(store, ["--has-metadata-key", "hermes.workflow.run_id"])
        self.assertEqual([r["id"] for r in by_key], [iid])

        # negative field filter proves the filter filters
        self.assertEqual(_list(store, ["--metadata-field",
                                       "hermes.workflow.run_id=other"]), [])

        # survives closure: metadata intact, still reconcilable by run_id
        p = subprocess.run([BD_BIN, "-C", store, "--actor", "seeder",
                            "close", iid, "--reason", "R4 verified"],
                           capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        row = read_model.show(store, iid, bd_bin=BD_BIN)
        self.assertEqual(row["status"], "closed")
        md = row.get("metadata") or {}
        self.assertEqual(md.get("hermes.workflow.run_id"), "run-r4")
        self.assertEqual(md.get("hermes.workflow.node_id"), "node-a")
        self.assertEqual(str(md.get("hermes.workflow.attempt")), "1")

        found = correlation.find_by_run(store, "run-r4", bd_bin=BD_BIN,
                                        include_closed=True)
        self.assertEqual([r["id"] for r in found], [iid])
        self.assertEqual(found[0]["correlation"]["hermes.workflow.node_id"],
                         "node-a")
        # default (open-only) reconciliation no longer returns it
        self.assertEqual(correlation.find_by_run(store, "run-r4",
                                                 bd_bin=BD_BIN), [])


class ForeignKeys(CorrelationCase):
    def test_operations_never_touch_foreign_keys(self):
        store = self.store("foreign")
        iid = create(store, "foreign bead")
        # seed a foreign key plus a foreign key inside OUR namespace shape
        p = subprocess.run([BD_BIN, "-C", store, "--actor", "seeder",
                            "update", iid,
                            "--set-metadata", "team=platform",
                            "--set-metadata", "hermes.workflow.custom=x",
                            "--json"], capture_output=True, text=True)
        self.assertEqual(p.returncode == 0, True, p.stderr)

        correlation.attach_correlation(
            store, iid, actor=actor(), bd_bin=BD_BIN,
            run_id="run-f", node_id="n1", attempt=2,
            if_assignee="", if_status="open")
        md = read_model.show(store, iid, bd_bin=BD_BIN)["metadata"]
        self.assertEqual(md["team"], "platform")
        self.assertEqual(md["hermes.workflow.custom"], "x")

        correlation.clear_correlation(
            store, iid, actor=actor(), bd_bin=BD_BIN,
            keys=("hermes.workflow.run_id",),
            if_assignee="", if_status="open")
        md = read_model.show(store, iid, bd_bin=BD_BIN)["metadata"]
        self.assertNotIn("hermes.workflow.run_id", md)
        for k in ("team", "hermes.workflow.custom",
                  "hermes.workflow.node_id", "hermes.workflow.attempt"):
            self.assertIn(k, md, f"{k} must survive our targeted unset")

    def test_refuses_to_unset_foreign_keys(self):
        store = self.store("refuse")
        iid = create(store, "refuse bead")
        subprocess.run([BD_BIN, "-C", store, "--actor", "seeder", "update",
                        iid, "--set-metadata", "team=platform", "--json"],
                       capture_output=True, text=True, check=True)
        before = _raw_show(store, iid)
        with self.assertRaises(ValueError):
            correlation.clear_correlation(
                store, iid, actor=actor(), bd_bin=BD_BIN,
                keys=("team", "hermes.workflow.run_id"),
                if_assignee="", if_status="open")
        self.assertEqual(_raw_show(store, iid), before)

    def test_default_clear_removes_only_our_three_keys(self):
        store = self.store("clearall")
        iid = create(store, "clear bead")
        correlation.attach_correlation(
            store, iid, actor=actor(), bd_bin=BD_BIN,
            run_id="run-c", node_id="n", attempt=1,
            if_assignee="", if_status="open")
        md = read_model.show(store, iid, bd_bin=BD_BIN)["metadata"]
        self.assertEqual(
            sorted(k for k in md if k.startswith("hermes.workflow.")),
            ["hermes.workflow.attempt", "hermes.workflow.node_id",
             "hermes.workflow.run_id"])
        correlation.clear_correlation(store, iid, actor=actor(),
                                      bd_bin=BD_BIN,
                                      if_assignee="", if_status="open")
        md = read_model.show(store, iid, bd_bin=BD_BIN).get("metadata") or {}
        self.assertEqual([k for k in md if k.startswith("hermes.workflow.")],
                         [])


class AbsenceIsNotAnError(CorrelationCase):
    """Presence-independent behavior: every flow passes with keys absent."""

    def test_flows_pass_with_keys_absent(self):
        store = self.store("absent")
        iid = create(store, "no metadata bead")
        self.assertIsNone(read_model.show(store, iid, bd_bin=BD_BIN)
                          .get("metadata") or None)
        # read returns {} — never an error, never raises
        self.assertEqual(correlation.correlation_for(store, iid,
                                                     bd_bin=BD_BIN), {})
        self.assertEqual(correlation.find_by_run(store, "nope",
                                                 bd_bin=BD_BIN), [])
        # native flows unaffected: field write, ready, show all normal
        row = write_protocol.update_fields(
            store, iid, actor=actor(), bd_bin=BD_BIN,
            if_assignee="", if_status="open", fields={"priority": "1"})
        self.assertTrue(row["readback_verified"])
        frontier = read_model.ready(store, bd_bin=BD_BIN)
        self.assertIn(iid, [r["id"] for r in frontier])
        self.assertEqual(correlation.correlation_for(store, iid,
                                                     bd_bin=BD_BIN), {})


class GuardedWhereAvailable(CorrelationCase):
    """--set-metadata rides `bd update`: our writes carry the guard pair."""

    def test_stale_guard_writes_nothing(self):
        store = self.store("guard")
        iid = create(store, "guard bead")
        before = _raw_show(store, iid)
        with self.assertRaises(write_protocol.WriteStaleError) as cm:
            correlation.attach_correlation(
                store, iid, actor=actor(), bd_bin=BD_BIN, run_id="r",
                if_assignee="", if_status="in_progress",  # actual: open
            )
        self.assertEqual(cm.exception.exit_code, 13)
        self.assertEqual(_raw_show(store, iid), before)

    def test_values_are_small_and_transcript_free(self):
        store = self.store("vals")
        iid = create(store, "vals bead")
        for bad in ("line1\nline2", "x" * 257, "", "  "):
            with self.assertRaises(ValueError):
                correlation.attach_correlation(
                    store, iid, actor=actor(), bd_bin=BD_BIN, run_id=bad,
                    if_assignee="", if_status="open")
        self.assertEqual(correlation.correlation_for(store, iid,
                                                     bd_bin=BD_BIN), {})


class CouplingGates(unittest.TestCase):
    """Zero-coupling gates: correlation is plugin-side only; Workflow core
    stays beads-free; nothing imports the module implicitly."""

    def test_runner_sources_have_zero_beads_hits(self):
        hits = []
        for root, dirs, files in os.walk(RUNNER_ROOT):
            dirs[:] = [d for d in dirs if d not in
                       ("node_modules", ".git", "graphify-out")]
            for f in files:
                if f.endswith(".py"):
                    p = os.path.join(root, f)
                    with open(p, encoding="utf-8",
                              errors="replace") as fh:
                        text = fh.read()
                    if "beads" in text.lower():
                        hits.append(p)
        self.assertEqual(hits, [], f"runner sources must stay 0-hit: {hits}")

    def test_correlation_module_has_no_workflow_import(self):
        with open(os.path.join(LANE, "correlation.py"),
                  encoding="utf-8") as fh:
            src = fh.read()
        for tok in ("import wf", "from wf", "hermes_workflow",
                    "hermes-workflows", "workflow.runner"):
            self.assertNotIn(tok, src, f"forbidden coupling token: {tok}")

    def test_module_is_additive_nothing_imports_it(self):
        importers = []
        for f in os.listdir(LANE):
            if f.endswith(".py") and f != "correlation.py":
                with open(os.path.join(LANE, f), encoding="utf-8",
                          errors="replace") as fh:
                    text = fh.read()
                if "import correlation" in text:
                    importers.append(f)
        self.assertEqual(importers, [],
                         "correlation absence must not be loadable anywhere")


# --- small local helpers (no shared-fixture framework) ----------------------

def _list(store, extra_tokens, include_closed=False):
    argv = [BD_BIN, "-C", store, "--readonly", "list", "--json"]
    if include_closed:
        argv.append("--all")
    argv += extra_tokens
    p = subprocess.run(argv, capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    rows = json.loads(p.stdout) if p.stdout.strip() else []
    return rows if isinstance(rows, list) else [rows]


def _raw_show(store, iid):
    p = subprocess.run([BD_BIN, "-C", store, "--readonly", "show", iid,
                        "--json"], capture_output=True)
    assert p.returncode == 0, p.stderr
    return p.stdout


if __name__ == "__main__":
    os.makedirs(FIXTURE_ROOT, exist_ok=True)
    unittest.main(verbosity=2)
