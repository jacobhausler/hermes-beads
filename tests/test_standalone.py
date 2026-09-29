"""STANDALONE ISOLATION + foreign-cwd parity (hbl-pnu.3.2).

Claim under test: the hermes-beads plugin needs NOTHING from
hermes-workflows. Two halves, both run against the REAL binaries — no mocks,
no fixtures faked:

  1. import-isolation. A fresh subprocess runs the Hermes venv python with
     HERMES_HOME pointed at a disposable home under tests/.standalone-runtime
     that contains ONLY this plugin (a plain copy of the lane tree — installed
     plugins are never renamed or disabled; the temp home simply has no
     hermes-workflows). The subprocess imports the plugin through the real SDK
     path (hermes_cli.plugins.discover_plugins), snapshots sys.modules, then
     drives the FULL native loop on a disposable store it owns:
        read_model.ready -> claims.claim -> native read-back -> heartbeat
        -> write_protocol.update_fields (both guard flags)
        -> evidence.WorkerSurface.record_evidence -> evidence.authorized_close
        -> verify read-back.
     It prints ONE JSON receipt; the parent asserts loop_ok, that the plugin
     really loaded, and that NO module whose name contains "workflow" entered
     sys.modules across import+loop. Plus a parent-side AST scan of the core
     modules for any import naming "workflow".
     Both detectors are proven LIVE by controls: the sys.modules scan must
     report a module injected into the copied plugin's register() path
     (workflow_control_probe), and the AST scan must report a temp dir whose
     file imports hermes_workflows.runner / workflow_engine. A dead detector
     fails its control.

  2. foreign-cwd parity. The identical op sequence is driven through the
     stock pinned bd (v1.3.0) as `bd -C <store> --db <store>/.beads/<p>.db`
     with cwd=/tmp (probed: --db ALONE from a foreign cwd loses config.yaml
     discovery — "database not initialized: issue_prefix config is missing";
     both flags together keep stdout clean JSON). The normalized native
     readbacks (show row, claim row, evidence comment, post-close frontier)
     must equal the plugin-loop store's readbacks step for step. Volatile
     fields dropped: timestamps, per-store ID suffixes, count columns.
     NOT volatile and compared exactly: status, assignee, notes, title,
     labels, priority, issue_type, close_reason, comment author+text.

Store hygiene: disposable stores are created here under
tests/.standalone-runtime (gitignored) and each owns its `git init` (the
embedded-dolt fall-through trap). No --force, no SQL, no shadow stores, no
memory/mail/formulas, planning store never touched.

Run: python3 tests/test_standalone.py   (stdlib-only unittest; nonzero exit on failure)
"""
import ast
import json
import os
import random
import shutil
import string
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
LANE = os.path.dirname(HERE)
sys.path.insert(0, LANE)

BD_BIN = os.environ.get(
    "BEADS_LAB_BD", "/home/hermes/.hermes/work/beads-lab/bin/bd")
HERMES_ROOT = os.environ.get("HERMES_ROOT", "/opt/hermes")
VENV_PY = os.environ.get(
    "HERMES_VENV_PY", os.path.join(HERMES_ROOT, ".venv", "bin", "python"))
FIXTURE_ROOT = os.path.join(HERE, ".standalone-runtime")
ARTIFACTS = ["tests/test_standalone.py"]
WORKER, PARENT = "sa-worker", "sa-parent"
NOTE = "standalone: guarded edit"
ATTEMPT = "a-standalone"
REASON = "Verified: " + " + ".join(ARTIFACTS)
CORE_MODULES = ["__init__.py", "native.py", "read_model.py", "claims.py",
                "evidence.py", "write_protocol.py", "correlation.py",
                "interop.py"]
# Volatile = per-run clock values and per-store generated identities ONLY.
# Everything else (status/assignee/notes/title/labels/priority/issue_type/
# close_reason/comment author+text) participates in the EXACT comparison.
VOLATILE = {"created_at", "updated_at", "closed_at", "started_at",
            "dependency_count", "dependent_count", "comment_count",
            "id", "issue_id", "revision"}

# ---- subprocess driver: isolated SDK import + full native loop -------------

PLUGIN_DRIVER = r'''
import json, os, sys
home, store, label = sys.argv[1], sys.argv[2], sys.argv[3]
CFG = json.loads(os.environ["SA_CFG"])
os.environ["HERMES_HOME"] = home
sys.path.insert(0, CFG["pkg"])   # the COPIED package — isolation under test
W, P, NOTE, ATT = CFG["w"], CFG["p"], CFG["note"], CFG["att"]
ARTS, REASON, BD = CFG["arts"], CFG["reason"], CFG["bd"]

def bail(where, exc):
    print(json.dumps({"loop_ok": False, "where": where,
                      "error_type": type(exc).__name__,
                      "error": str(exc)[:400]}))
    sys.exit(1)

try:
    import hermes_cli.plugins as sdk
    sdk.discover_plugins(force=True)
    pm = sdk.get_plugin_manager()
    loaded = sorted(getattr(pm, "_plugins", {}) or {})
except Exception as exc:
    bail("sdk_discover", exc)

try:
    import claims, evidence, read_model, write_protocol
    ready = [r["id"] for r in read_model.ready(store, label=label, bd_bin=BD)]
    gate = ready[0]
    row = claims.claim(store, gate, actor=W, bd_bin=BD)
    back = read_model.show(store, gate, bd_bin=BD)
    if back.get("assignee") != W or back.get("status") != "in_progress":
        raise AssertionError("claim read-back mismatch: " + repr(back))
    claims.heartbeat(store, gate, actor=W, bd_bin=BD)
    edited = write_protocol.update_fields(
        store, gate, actor=W, bd_bin=BD,
        if_assignee=W, if_status="in_progress", fields={"notes": NOTE})
    if not edited.get("readback_verified"):
        raise AssertionError("guarded update not readback_verified")
    ws = evidence.WorkerSurface(store, actor=W, bd_bin=BD)
    ws.record_evidence(gate, attempt=ATT, artifacts=ARTS,
                       summary="standalone loop")
    rec = evidence.authorized_close(
        store, gate, actor=P, bd_bin=BD,
        authorization="parent-verified: hbl-pnu.3.2 standalone",
        reason=REASON, evidence_actor=W, attempt=ATT, artifacts=ARTS)
    final = read_model.show(store, gate, bd_bin=BD)
    comments = read_model.comments(store, gate, bd_bin=BD)
    after = [r["id"] for r in read_model.ready(store, label=label, bd_bin=BD)]
    hits = sorted(m for m in sys.modules if "workflow" in m.lower())
    origins = {m: getattr(sys.modules[m], "__file__", None)
               for m in ("native", "claims", "evidence", "read_model",
                         "write_protocol")}
    print(json.dumps({
        "loop_ok": True, "loaded": loaded, "hits": hits, "gate": gate,
        "origins": origins,
        "claim_row": {k: row.get(k) for k in ("assignee", "status")},
        "edited_verified": bool(edited.get("readback_verified")),
        "close_verified": bool(rec.get("readback_verified")),
        "final": final, "comments": comments, "after": after}))
except Exception as exc:
    bail("loop", exc)
sys.exit(0)
'''

# ---- subprocess driver: stock bd --db from a foreign cwd -------------------

STOCK_DRIVER = r'''
import json, os, subprocess, sys
store, label = sys.argv[1], sys.argv[2]
CFG = json.loads(os.environ["SA_CFG"])
bd, prefix = CFG["bd"], "stl"
W, P, NOTE, ATT = CFG["w"], CFG["p"], CFG["note"], CFG["att"]
ARTS, REASON = CFG["arts"], CFG["reason"]

def run(*argv, actor, readonly=False, parse=True):
    full = [bd, "-C", store, "--db",
            os.path.join(store, ".beads", prefix + ".db")]
    if readonly:
        full.append("--readonly")
    full += ["--actor", actor] + list(argv)
    p = subprocess.run(full, cwd="/tmp", capture_output=True, text=True)
    if p.returncode != 0:
        print(json.dumps({"loop_ok": False, "argv": list(argv),
                          "rc": p.returncode, "stderr": p.stderr[:400]}))
        sys.exit(1)
    out = p.stdout.strip()
    if not out or not parse:
        return None            # prose acks ("Comment added", "Closed")
    return json.loads(out)

def one(r):
    return r[0] if isinstance(r, list) and r else r

try:
    created = one(run("create", "standalone gate", "-l", label, "--json",
                      actor="sa-seeder"))
    gate = created["id"]
    run("update", gate, "--claim", "--json", actor=W)
    claim_row = one(run("show", gate, "--json", actor=W, readonly=True))
    run("heartbeat", gate, "--json", actor=W)
    run("update", gate, "--if-assignee", W, "--if-status",
        "in_progress", "--notes", NOTE, "--json", actor=W)
    text = ("EVIDENCE attempt=" + ATT + " artifacts=" + ";".join(ARTS)
            + " summary=standalone loop")
    run("comments", "add", gate, text, actor=W, parse=False)
    run("unclaim", gate, "--if-assignee", W, "--json", actor=W)
    run("update", gate, "--claim", "--json", actor=P)
    run("close", gate, "--reason", REASON, actor=P, parse=False)
    final = one(run("show", gate, "--json", actor=P, readonly=True))
    # revision: per-store content hash — differs by store identity, not by
    # op path; normalized away (see VOLATILE).
    comments = run("comments", gate, "--json", actor=P, readonly=True)
    after = [r["id"] for r in (run("ready", "--json", "--exclude-type=epic",
                                   "-l", label, actor=P, readonly=True) or [])]
    print(json.dumps({"loop_ok": True, "gate": gate,
                      "claim_row": {k: (claim_row or {}).get(k) for k in
                                    ("assignee", "status")},
                      "final": final, "comments": comments, "after": after}))
except Exception as exc:
    print(json.dumps({"loop_ok": False, "where": "stock",
                      "error_type": type(exc).__name__,
                      "error": str(exc)[:400]}))
    sys.exit(1)
sys.exit(0)
'''


def _rand():
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=6))


def make_store(prefix):
    """Disposable store: unique dir under the gitignored fixture root, own
    git repo (embedded-dolt fall-through trap), own bd store."""
    os.makedirs(FIXTURE_ROOT, exist_ok=True)
    d = tempfile.mkdtemp(dir=FIXTURE_ROOT, prefix=f"store-{prefix}-")
    subprocess.run(["git", "init", "-q", "."], cwd=d, check=True,
                   capture_output=True)
    p = subprocess.run([BD_BIN, "init", "--prefix", prefix], cwd=d,
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    return d


def seed_gate(store, label="standalone"):
    p = subprocess.run([BD_BIN, "-C", store, "--actor", "sa-seeder",
                        "create", "standalone gate", "-l", label, "--json"],
                       cwd=store, capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    rows = json.loads(p.stdout)
    return (rows[0] if isinstance(rows, list) else rows)["id"]


def isolated_home():
    """Temp HERMES_HOME containing ONLY a copy of this lane's plugin —
    installed plugins untouched, hermes-workflows simply not present."""
    home = tempfile.mkdtemp(dir=FIXTURE_ROOT, prefix="home-")
    plugins = os.path.join(home, "plugins")
    os.makedirs(plugins)
    shutil.copytree(LANE, os.path.join(plugins, "hermes-beads"),
                    symlinks=False,
                    ignore=shutil.ignore_patterns(
                        "tests", ".git", "desktop", "docs", "__pycache__",
                        ".pytest_cache", "node_modules"))
    assert os.listdir(plugins) == ["hermes-beads"]
    # User plugins are opt-in: the disposable home enables ITS OWN plugin
    # (this is the temp home's config, not an installed config).
    with open(os.path.join(home, "config.yaml"), "w",
              encoding="utf-8") as fh:
        fh.write("plugins:\n  enabled:\n    - hermes-beads\n")
    return home


def run_driver(script, *args, cwd="/tmp", home=None):
    cfg = {"lane": LANE, "bd": BD_BIN, "w": WORKER, "p": PARENT,
           "note": NOTE, "att": ATTEMPT, "arts": ARTIFACTS, "reason": REASON,
           "pkg": os.path.join(home, "plugins", "hermes-beads")
           if home else LANE}
    env = dict(os.environ, SA_CFG=json.dumps(cfg))
    return subprocess.run([VENV_PY, "-c", script, *args],
                          cwd=cwd, capture_output=True, text=True,
                          env=env, timeout=300)


def receipt(p):
    try:
        return json.loads(p.stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError(
            f"driver stdout is not clean JSON ({exc}); rc={p.returncode} "
            f"stdout={p.stdout[:500]!r} stderr={p.stderr[:300]!r}") from exc


def workflow_imports_in(root):
    """AST scan: every Import/ImportFrom form whose dotted name (or an alias)
    contains 'workflow', across all .py under root (skips __pycache__)."""
    hits = []
    for dirpath, _dirs, files in os.walk(root):
        if "__pycache__" in dirpath:
            continue
        for f in files:
            if not f.endswith(".py"):
                continue
            path = os.path.join(dirpath, f)
            tree = ast.parse(open(path, encoding="utf-8").read(), path)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom):
                    base = "." * (node.level or 0) + (node.module or "")
                    names = [f"{base}.{a.name}" for a in node.names]
                else:
                    continue
                for n in names:
                    if "workflow" in n.lower():
                        hits.append(f"{os.path.relpath(path, root)}:"
                                    f"{getattr(node, 'lineno', '?')}:{n}")
    return hits


def norm(row):
    out = {}
    for k, v in sorted((row or {}).items()):
        if k in VOLATILE:
            continue
        if isinstance(v, str) and k in ("title",):
            pass
        out[k] = v
    return out


def norm_id(iid):
    return iid.rsplit("-", 1)[0] if "-" in iid else iid


def norm_comment(c):
    return {k: v for k, v in sorted((c or {}).items())
            if k not in (VOLATILE | {"id", "issue_id"})}


class ImportIsolation(unittest.TestCase):
    def test_full_native_loop_isolated_and_workflow_free(self):
        home = isolated_home()
        store = make_store("stl")
        seed_gate(store)
        p = run_driver(PLUGIN_DRIVER, home, store, "standalone", home=home)
        r = receipt(p)
        self.assertTrue(r.get("loop_ok"),
                        f"isolated loop failed: {r!r} stderr={p.stderr[:400]}")
        for m, f in r["origins"].items():
            self.assertTrue(f and f.startswith(home),
                            f"{m} loaded from {f!r}, not the isolated copy")
        self.assertTrue(any("beads" in k for k in r["loaded"]),
                        f"plugin did not load via SDK discovery: {r['loaded']}")
        self.assertEqual(r["hits"], [],
                         "workflow-named modules entered sys.modules during "
                         f"import+loop: {r['hits']}")
        self.assertEqual(r["claim_row"], {"assignee": WORKER,
                                          "status": "in_progress"})
        self.assertTrue(r["edited_verified"] and r["close_verified"])
        self.assertEqual(r["final"]["status"], "closed")
        self.assertEqual(r["final"]["assignee"], PARENT)
        self.assertTrue(r["final"]["closed_at"])
        self.assertEqual(r["final"]["notes"], NOTE)
        self.assertEqual(r["final"]["close_reason"], REASON)
        envs = [c for c in r["comments"] if c["author"] == WORKER
                and c["text"].startswith(f"EVIDENCE attempt={ATTEMPT}")]
        self.assertEqual(len(envs), 1, "exact evidence envelope present")
        self.assertNotIn(r["gate"], r["after"], "closed gate no longer ready")
        shutil.rmtree(home, ignore_errors=True)

    def test_sysmodules_scan_is_live_control(self):
        """Inject a workflow-named import into the copied plugin: either the
        subprocess dies naming it, or the scan REPORTS it. A dead scan would
        report [] and this test fails."""
        home = isolated_home()
        pkg = os.path.join(home, "plugins", "hermes-beads")
        with open(os.path.join(pkg, "workflow_control_probe.py"), "w",
                  encoding="utf-8") as fh:
            fh.write("PROBE = True  # isolation-scan control module\n")
        with open(os.path.join(pkg, "__init__.py"), "a",
                  encoding="utf-8") as fh:
            fh.write("\nimport sys as _s, os as _o;"
                     "_s.path.insert(0, _o.path.dirname(__file__));"
                     "import workflow_control_probe  # scan control\n")
        store = make_store("ctl")
        seed_gate(store)
        p = run_driver(PLUGIN_DRIVER, home, store, "standalone", home=home)
        if p.returncode != 0 or "loop_ok" not in p.stdout:
            self.assertIn("workflow_control_probe", p.stdout + p.stderr)
        else:
            self.assertIn("workflow_control_probe", receipt(p)["hits"])
        shutil.rmtree(home, ignore_errors=True)

    def test_core_ast_scan_is_clean(self):
        for m in CORE_MODULES:
            path = os.path.join(LANE, m)
            self.assertTrue(os.path.exists(path), f"core module missing: {path}")
        # Scope the scan to the core surface only (tests/docs may mention the
        # word; this asserts nothing CORE imports it).
        core = set(CORE_MODULES)
        hits = [h for h in workflow_imports_in(LANE)
                if h.split(":", 1)[0] in core]
        self.assertEqual(hits, [], f"workflow imports in core modules: {hits}")

    def test_ast_scan_is_live_control(self):
        d = tempfile.mkdtemp(dir=FIXTURE_ROOT, prefix="astctl-")
        try:
            with open(os.path.join(d, "sneaky.py"), "w",
                      encoding="utf-8") as fh:
                fh.write("import hermes_workflows.runner\n"
                         "from workflow_engine import thing\n")
            hits = workflow_imports_in(d)
            self.assertEqual(len(hits), 2,
                             f"AST scan missed injected imports: {hits}")
        finally:
            shutil.rmtree(d, ignore_errors=True)


class ForeignCwdParity(unittest.TestCase):
    def test_stock_bd_foreign_cwd_readbacks_match_plugin_loop(self):
        home = isolated_home()
        store_a = make_store("stl")
        seed_gate(store_a)
        pa = run_driver(PLUGIN_DRIVER, home, store_a, "standalone", home=home)
        ra = receipt(pa)
        self.assertTrue(ra.get("loop_ok"), f"plugin loop failed: {ra!r}")
        store_b = make_store("stl")   # stock driver seeds its own gate
        pb = run_driver(STOCK_DRIVER, store_b, "standalone")
        rb = receipt(pb)
        self.assertTrue(rb.get("loop_ok"), f"stock loop failed: {rb!r} "
                                           f"stderr={pb.stderr[:400]}")
        self.assertEqual(norm(ra["claim_row"]), norm(rb["claim_row"]),
                         "claim read-back diverges plugin vs stock")
        self.assertEqual(norm(ra["final"]), norm(rb["final"]),
                         "final show read-back diverges plugin vs stock")
        self.assertEqual([norm_comment(c) for c in ra["comments"]],
                         [norm_comment(c) for c in rb["comments"]],
                         "evidence comment read-back diverges")
        self.assertEqual([norm_id(i) for i in ra["after"]],
                         [norm_id(i) for i in rb["after"]],
                         "post-close frontier diverges")
        # The stock half must genuinely have run from the foreign cwd:
        self.assertEqual(rb["final"]["close_reason"], REASON)
        shutil.rmtree(home, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
