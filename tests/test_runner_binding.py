#!/usr/bin/env python3
"""hbl-pnu.3.3 runner-binding acceptance tests — TDD RED/GREEN.

Every test drives the REAL pinned bd (v1.3.0) and, for the runner-path
probes, REAL subprocess execution of a PATCHED COPY of the Workflow runner
under tests/fixtures/runner-runtime/ (gitignored). The installed plugin
tree is only ever READ (hashed before/after); it is never patched. The
child is the committed fake-hermes fixture — fixture mocks alone do NOT
qualify real-Hermes autonomy (docs/runner-binding.md says so plainly).

Ports of the reviewer probes (reports/finish-runner-review.json):
  R1  p2_runner_kill.py / p2b_diag.py   -> test_r1_*   (adopt, never re-Popen)
  R2  p3_auth_replay.py                 -> test_r2_*   (host secret + HMAC +
                                            mandatory approved set + env strip)
  R3  p4_forged_close_replay.py P4/P4b  -> test_r3_*   (runner-held nonce)
  R4  P6                                -> test_r4_*   (intent fsynced before
                                            the claim)
Plus the pre-existing stop-race (t6) and the no-second-scheduler audit.
"""
import hashlib
import json
import os
import pathlib
import shutil
import signal
import subprocess
import sys
import time
import unittest

HERE = pathlib.Path(__file__).resolve().parent
LANE = HERE.parent
FIXTURES = HERE / "fixtures" / "runner-runtime"
WORKFLOW_SRC = FIXTURES / "workflow-source"
FAKE_HERMES = HERE / "fixtures" / "runner-binding" / "fake-hermes"
INSTALLED = pathlib.Path("/home/hermes/.hermes/plugins/hermes-workflows")
BD = os.environ.get("BEADS_LAB_BD",
                    "/home/hermes/.hermes/work/beads-lab/bin/bd")

sys.path.insert(0, str(LANE))
import runner_binding as rb        # noqa: E402
import runner_hooks                # noqa: E402
import evidence as ev_mod          # noqa: E402

for d in ("runs", "stores", "homes", "logs", "creds"):
    (FIXTURES / d).mkdir(parents=True, exist_ok=True)


def _sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


INSTALLED_BEFORE = {}


def sh(argv, cwd):
    return subprocess.run([str(a) for a in argv], cwd=str(cwd),
                          capture_output=True, text=True)


def make_store(name):
    s = FIXTURES / "stores" / name
    if s.exists():
        shutil.rmtree(s)
    s.mkdir(parents=True)
    sh(["git", "init", "-q", "."], s)
    sh(["git", "config", "user.email", "lane@test"], s)
    sh(["git", "config", "user.name", "lane"], s)
    p = sh([BD, "init", "--non-interactive"], s)
    if p.returncode:
        p = sh([BD, "init"], s)
    assert p.returncode == 0, p.stderr
    return s


def create(store, title):
    d = json.loads(sh([BD, "create", title, "--json"], store).stdout)
    return (d[0] if isinstance(d, list) else d)["id"]


def show(store, i):
    d = json.loads(sh([BD, "show", i, "--json"], store).stdout)
    return d[0] if isinstance(d, list) and d else None


def comments(store, i):
    out = sh([BD, "comments", i, "--json"], store).stdout
    return json.loads(out) if out.strip() else []


HOST_HOME = FIXTURES / "host-home"   # the in-process host's HERMES_HOME
os.environ["HERMES_HOME"] = str(HOST_HOME)


def cred(name, store, approved, principal="lab-human",
         secret="a" * 64, with_approved=True, with_store=True):
    """Host provisioning at THE fixed credential path (owner-only dir+file).
    Tests pin a known secret so signatures are reproducible; production uses
    rb.provision_credential (random secret), covered by test_r2g."""
    p = pathlib.Path(rb.credential_path())
    p.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(p.parent, 0o700)
    d = {"principal": principal, "secret": secret}
    if with_store:
        d["store"] = str(pathlib.Path(store).resolve())
    if with_approved:
        d["approved_beads"] = list(approved)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(d))
    os.chmod(tmp, 0o600)
    os.replace(tmp, p)
    return p


def make_run(run_id, nodes):
    r = FIXTURES / "runs" / run_id
    if r.exists():
        shutil.rmtree(r)
    (r / "nodes").mkdir(parents=True)
    (r / "gates").mkdir()
    (r / "graph.json").write_text(json.dumps({"name": run_id,
                                              "nodes": nodes}))
    (r / "run.json").write_text(json.dumps({
        "hermes_bin": str(FAKE_HERMES),
        "concurrency": 2, "node_timeout": 40}))
    home = FIXTURES / "homes" / run_id
    if home.exists():
        shutil.rmtree(home)
    home.mkdir(parents=True)
    lg = FIXTURES / "logs" / f"{run_id}.log"
    if lg.exists():
        lg.unlink()
    return r, home


def goal(store_run, bead, artifact_name, worker="worker-r", attempt=1,
         prefix=""):
    art = FIXTURES / "logs" / artifact_name
    if art.exists():
        art.unlink()
    return (f"{prefix}RUNNER-TASK worker BEAD={bead} ATTEMPT={attempt} "
            f"ARTIFACT={art} WORKER={worker}"), str(art)


def env_for(run_id, home, store, credfile=None, extra=None):
    e = dict(os.environ,
             WF_RUNS_ROOT=str(FIXTURES / "runs"),
             HERMES_HOME=str(home),
             FAKE_LOG=str(FIXTURES / "logs" / f"{run_id}.log"),
             BEADS_STORE=str(store), BEADS_LANE=str(LANE), BEADS_BD=BD)
    if credfile:
        e["BEADS_ADMISSION_CREDENTIAL_FILE"] = str(credfile)
    if extra:
        e.update(extra)
    return e


def wf_run(run_id, env, timeout=180):
    return subprocess.run([sys.executable, str(WORKFLOW_SRC / "wf.py"),
                           "run", run_id],
                          env=env, capture_output=True, text=True,
                          timeout=timeout)


def wf_popen(run_id, env):
    return subprocess.Popen([sys.executable, str(WORKFLOW_SRC / "wf.py"),
                             "run", run_id], env=env,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True)


def spawns(run_id):
    p = FIXTURES / "logs" / f"{run_id}.log"
    return p.read_text().strip().splitlines() if p.exists() else []


def request(store, beads, key):
    return {"authority_domain": "lab", "profile": "p", "store": str(store),
            "beads": list(beads), "worker": "worker-r",
            "verifier": "verifier-r", "request_key": key}


def admit(run_dir, req, credfile, principal="lab-human", secret="a" * 64,
          signature="MISSING"):
    if signature == "MISSING":
        signature = rb.sign_request(secret, req, principal)
    return rb.admit_work(req,
                         authenticated_context={"principal": principal,
                                                "signature": signature},
                         run_root=str(run_dir), bd_bin=BD)


def wait_until(fn, timeout=30.0, every=0.25):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(every)
    return None


class RunnerBindingTests(unittest.TestCase):
    """Real bd, real runner subprocess, fixture child. Autonomy here is
    fixture-qualified only — see docs/runner-binding.md."""

    @classmethod
    def setUpClass(cls):
        global INSTALLED_BEFORE
        INSTALLED_BEFORE = {p.name: _sha(p) for p in
                            (INSTALLED / "wf.py", INSTALLED / "wfcommon.py")}
        # fresh isolated checkout copy every session, patched by runner_hooks
        if WORKFLOW_SRC.exists():
            shutil.rmtree(WORKFLOW_SRC)
        WORKFLOW_SRC.mkdir(parents=True)
        for f in ("wf.py", "wfcommon.py"):
            shutil.copy2(INSTALLED / f, WORKFLOW_SRC / f)
        runner_hooks.install(WORKFLOW_SRC / "wf.py", LANE)

    @classmethod
    def tearDownClass(cls):
        after = {p.name: _sha(p) for p in
                 (INSTALLED / "wf.py", INSTALLED / "wfcommon.py")}
        assert after == INSTALLED_BEFORE, \
            "installed plugin tree was mutated — lab-only rule broken"

    # ---------- happy path (port of P1) ----------
    def test_r0_happy_a_to_b(self):
        s = make_store("r0")
        A, B = create(s, "A"), create(s, "B")
        sh([BD, "dep", A, "--blocks", B], s)
        gA, _ = goal("r0", A, "r0-A.artifact")
        gB, _ = goal("r0", B, "r0-B.artifact")
        nodes = [{"id": "A", "type": "agent", "goal": gA, "context": "x",
                  "max_turns": 3, "timeout": 60},
                 {"id": "B", "type": "agent", "after": ["A"], "goal": gB,
                  "context": "x", "max_turns": 3, "timeout": 60}]
        run, home = make_run("r0", nodes)
        cf = cred("r0", s, [A, B])
        admit(run, request(s, [A, B], "r0k"), cf)
        p = wf_run("r0", env_for("r0", home, s, cf))
        self.assertIn("WORKFLOW_DONE r0", p.stdout, p.stdout[-400:])
        self.assertEqual(spawns("r0"), [f"SPAWN {A}", f"SPAWN {B}"])
        for x in (A, B):
            r = show(s, x)
            self.assertEqual(r["status"], "closed")
            self.assertEqual(r["assignee"], "verifier-r")
            self.assertIn("nonce=", r.get("close_reason") or "")
            self.assertEqual(
                rb.ledger_read(str(run), x)["state"], "closed_verified")
        # honest causality: reason cites the runner-held nonce verbatim
        intent = None
        idir = run / "beads" / "intents"
        for fn in os.listdir(idir):
            rec = json.loads((idir / fn).read_text())
            if rec.get("bead") == A and rec.get("operation") == "launch":
                intent = rec
        self.assertIsNotNone(intent)
        self.assertIn(f"nonce={intent['nonce']}",
                      show(s, A)["close_reason"])

    # ---------- R1: runner-only death adopts; NEVER a second Popen ----------
    def test_r1_runner_only_death_never_respawns_live_child(self):
        s = make_store("r1")
        A, B = create(s, "A"), create(s, "B")
        sh([BD, "dep", A, "--blocks", B], s)
        gA, _ = goal("r1", A, "r1-A.artifact", prefix="SLEEP 8 ")
        gB, _ = goal("r1", B, "r1-B.artifact")
        nodes = [{"id": "A", "type": "agent", "goal": gA, "context": "x",
                  "max_turns": 3, "timeout": 60},
                 {"id": "B", "type": "agent", "after": ["A"], "goal": gB,
                  "context": "x", "max_turns": 3, "timeout": 60}]
        run, home = make_run("r1", nodes)
        cf = cred("r1", s, [A, B])
        admit(run, request(s, [A, B], "r1k"), cf)
        env = env_for("r1", home, s, cf)
        pr = wf_popen("r1", env)
        got = wait_until(lambda: spawns("r1") or None)
        self.assertTrue(got, "first runner never spawned A")
        time.sleep(1.0)                       # child is mid-SLEEP, alive
        os.kill(pr.pid, signal.SIGKILL)
        pr.wait()
        # runner-only death: the child outlives its spawner
        p = wf_run("r1", env)                 # re-drive adopts/observes
        self.assertIn("WORKFLOW_DONE r1", p.stdout, p.stdout[-500:])
        lines = spawns("r1")
        self.assertEqual(lines.count(f"SPAWN {A}"), 1,
                         f"live orphan was RE-SPAWNED: {lines}")
        self.assertEqual(lines.count(f"SPAWN {B}"), 1)
        ev = [c for c in comments(s, A)
              if (c.get("text") or "").startswith("EVIDENCE attempt=1")]
        self.assertEqual(len(ev), 1, f"duplicate worker effect: {ev}")
        self.assertEqual(rb.ledger_read(str(run), A)["state"],
                         "closed_verified")

    # ---------- R2: host-held secret, HMAC, mandatory ceiling ----------
    def test_r2a_self_issued_credential_refused(self):
        s = make_store("r2a")
        A = create(s, "A")
        mine = FIXTURES / "creds" / "self-issued.json"
        mine.write_text(json.dumps({"principal": "anyone-i-like"}))
        os.chmod(mine, 0o600)
        os.environ["BEADS_ADMISSION_CREDENTIAL_FILE"] = str(mine)
        run = FIXTURES / "runs" / "r2a"
        shutil.rmtree(run, ignore_errors=True)
        run.mkdir(parents=True)
        with self.assertRaises(rb.BindingRefusal):
            rb.admit_work(request(s, [A], "p3a"),
                          authenticated_context={"principal":
                                                 "anyone-i-like"},
                          run_root=str(run), bd_bin=BD)

    def test_r2b_unsigned_or_forged_signature_refused(self):
        s = make_store("r2b")
        A = create(s, "A")
        cf = cred("r2b", s, [A])
        run = FIXTURES / "runs" / "r2b"
        shutil.rmtree(run, ignore_errors=True)
        run.mkdir(parents=True)
        req = request(s, [A], "r2bk")
        os.environ["BEADS_ADMISSION_CREDENTIAL_FILE"] = str(cf)
        with self.assertRaises(rb.BindingRefusal):       # no signature
            rb.admit_work(req,
                          authenticated_context={"principal": "lab-human"},
                          run_root=str(run), bd_bin=BD)
        with self.assertRaises(rb.BindingRefusal):       # wrong signature
            rb.admit_work(req,
                          authenticated_context={
                              "principal": "lab-human",
                              "signature": "0" * 64},
                          run_root=str(run), bd_bin=BD)
        # honest signed admission still works
        out = rb.admit_work(
            req, authenticated_context={
                "principal": "lab-human",
                "signature": rb.sign_request("a" * 64, req, "lab-human")},
            run_root=str(run), bd_bin=BD)
        self.assertTrue(out["ok"])

    def test_r2c_scope_ceiling_mandatory(self):
        s = make_store("r2c")
        A = create(s, "A")
        C = create(s, "C-out-of-grant")
        cf = cred("r2c", s, [], with_approved=False)      # no approved set
        run = FIXTURES / "runs" / "r2c"
        shutil.rmtree(run, ignore_errors=True)
        run.mkdir(parents=True)
        req = request(s, [A, C], "r2ck")
        os.environ["BEADS_ADMISSION_CREDENTIAL_FILE"] = str(cf)
        with self.assertRaises(rb.BindingRefusal):
            rb.admit_work(req,
                          authenticated_context={
                              "principal": "lab-human",
                              "signature": rb.sign_request(
                                  "a" * 64, req, "lab-human")},
                          run_root=str(run), bd_bin=BD)
        cf2 = cred("r2c2", s, [A])                        # ceiling present
        run2 = FIXTURES / "runs" / "r2c2"
        shutil.rmtree(run2, ignore_errors=True)
        run2.mkdir(parents=True)
        os.environ["BEADS_ADMISSION_CREDENTIAL_FILE"] = str(cf2)
        req2 = request(s, [A, C], "r2ck2")
        with self.assertRaises(rb.BindingRefusal):        # C out of ceiling
            rb.admit_work(req2,
                          authenticated_context={
                              "principal": "lab-human",
                              "signature": rb.sign_request(
                                  "a" * 64, req2, "lab-human")},
                          run_root=str(run2), bd_bin=BD)

    def test_r2f_self_minted_credential_elsewhere_never_consulted(self):
        """Reviewer bypass F2s: attacker mints a 0600 file WITH its own secret
        and approved set [A, C], points the legacy env var at it and signs
        with that secret. The door reads only credential_path()."""
        s = make_store("r2f")
        A = create(s, "A")
        C = create(s, "C-out-of-grant")
        cred("r2f", s, [A])                               # legit host cred
        mine = FIXTURES / "creds" / "attacker.json"
        mine.parent.mkdir(parents=True, exist_ok=True)
        own = "b" * 64
        mine.write_text(json.dumps({"principal": "anyone-i-like",
                                    "secret": own, "approved_beads": [A, C]}))
        os.chmod(mine, 0o600)
        os.environ["BEADS_ADMISSION_CREDENTIAL_FILE"] = str(mine)
        try:
            run = FIXTURES / "runs" / "r2f"
            shutil.rmtree(run, ignore_errors=True)
            run.mkdir(parents=True)
            req = request(s, [A, C], "r2fk")
            for principal, secret in (("anyone-i-like", own),
                                      ("lab-human", own)):
                with self.subTest(principal=principal):
                    with self.assertRaises(rb.BindingRefusal):
                        rb.admit_work(req, authenticated_context={
                            "principal": principal,
                            "signature": rb.sign_request(secret, req, principal)},
                            run_root=str(run), bd_bin=BD)
            self.assertEqual(show(s, C)["status"], "open")
        finally:
            os.environ.pop("BEADS_ADMISSION_CREDENTIAL_FILE", None)

    def test_r2g_provenance_enforced_and_provisioning_random(self):
        s = make_store("r2g")
        A = create(s, "A")
        p = pathlib.Path(rb.credential_path())
        if p.exists() or p.is_symlink():
            p.unlink()
        secret = rb.provision_credential("lab-human", [A], s)
        self.assertGreaterEqual(len(secret), 64)
        self.assertNotEqual(secret, "a" * 64)
        with self.assertRaises(rb.BindingRefusal):        # no silent overwrite
            rb.provision_credential("lab-human", [A], s)
        req = request(s, [A], "r2gk")
        run = FIXTURES / "runs" / "r2g"
        shutil.rmtree(run, ignore_errors=True)
        run.mkdir(parents=True)
        ctx = {"principal": "lab-human",
               "signature": rb.sign_request(secret, req, "lab-human")}
        # group-writable parent -> refused
        os.chmod(p.parent, 0o770)
        try:
            with self.assertRaises(rb.BindingRefusal):
                rb.admit_work(req, authenticated_context=ctx,
                              run_root=str(run), bd_bin=BD)
        finally:
            os.chmod(p.parent, 0o700)
        # symlinked credential -> refused
        real = p.with_name("real.json")
        os.replace(p, real)
        p.symlink_to(real)
        try:
            with self.assertRaises(rb.BindingRefusal):
                rb.admit_work(req, authenticated_context=ctx,
                              run_root=str(run), bd_bin=BD)
        finally:
            p.unlink()
            os.replace(real, p)
        # provisioned + provenance ok -> admitted
        out = rb.admit_work(req, authenticated_context=ctx,
                            run_root=str(run), bd_bin=BD)
        self.assertEqual(out["receipt"]["beads"], [A])

    def test_r2d_credential_never_inherited_by_child(self):
        s = make_store("r2d")
        A = create(s, "A")
        gA, _ = goal("r2d", A, "r2d-A.artifact", prefix="SLEEP 5 ")
        nodes = [{"id": "A", "type": "agent", "goal": gA, "context": "x",
                  "max_turns": 3, "timeout": 60}]
        run, home = make_run("r2d", nodes)
        cf = cred("r2d", s, [A])
        admit(run, request(s, [A], "r2dk"), cf)
        env = env_for("r2d", home, s, cf)   # runner carries the cred file
        pr = wf_popen("r2d", env)
        pid = None
        try:
            wait_until(lambda: spawns("r2d") or None)

            def child_env():
                out = subprocess.run(
                    ["pgrep", "-f", f"runs/r2d/logs"],
                    capture_output=True, text=True).stdout.split()
                for p in out:
                    try:
                        raw = pathlib.Path(f"/proc/{p}/environ").read_bytes()
                    except OSError:
                        continue
                    return p, dict(kv.split("=", 1) for kv
                                   in raw.decode().split("\x00") if "=" in kv)
                return None
            seen = wait_until(child_env, timeout=15)
            self.assertIsNotNone(seen, "fake-hermes child never appeared")
            pid, cenv = seen
            self.assertIn("BEADS_STORE", cenv)            # env does flow
            self.assertNotIn("BEADS_ADMISSION_CREDENTIAL_FILE", cenv,
                             "admission credential leaked to the child")
        finally:
            if pid:
                try:
                    os.kill(int(pid), signal.SIGKILL)
                except OSError:
                    pass
            try:
                pr.kill()
                pr.wait()
            except Exception:
                pass

    def test_r2e_stop_requires_signed_context(self):
        s = make_store("r2e")
        A = create(s, "A")
        gA, _ = goal("r2e", A, "r2e-A.artifact")
        run, _home = make_run("r2e", [{"id": "A", "type": "agent",
                                       "goal": gA, "context": "x"}])
        cf = cred("r2e", s, [A])
        admit(run, request(s, [A], "r2ek"), cf)
        with self.assertRaises(rb.BindingRefusal):        # env-only principal
            rb.stop_work(str(run),
                         authenticated_context={"principal": "lab-human"})
        out = rb.stop_work(
            str(run), authenticated_context={
                "principal": "lab-human",
                "signature": rb.sign_stop("a" * 64, str(run),
                                          "lab-human")})
        self.assertTrue(out["ok"])

    # ---------- R3: forged close reason must NOT unlock the successor ----------
    def test_r3a_forged_close_token_not_credited(self):
        s = make_store("r3a")
        A, B = create(s, "A"), create(s, "B")
        sh([BD, "dep", A, "--blocks", B], s)
        gA, _ = goal("r3a", A, "r3a-A.artifact")
        gB, _ = goal("r3a", B, "r3a-B.artifact")
        nodes = [{"id": "A", "type": "agent", "goal": gA, "context": "x"},
                 {"id": "B", "type": "agent", "after": ["A"], "goal": gB,
                  "context": "x"}]
        run, _home = make_run("r3a", nodes)
        cf = cred("r3a", s, [A, B])
        admit(run, request(s, [A, B], "r3ak"), cf)
        # an external actor (NOT the verifier) replays the whole shape:
        sh([BD, "update", A, "--claim"], s)
        sh([BD, "comments", "add", A,
            "EVIDENCE attempt=1 artifacts=x.artifact summary=hand"], s)
        sh([BD, "close", A, "--reason",
            f"workflow admitted close {A} attempt=1 verifier=verifier-r "
            f"run=r3a artifacts=x.artifact"], s)
        self.assertEqual(show(s, A)["status"], "closed")
        out = rb.authorize_close(str(run), A, "1",
                                 {"verdict": "accept",
                                  "artifacts": ["x.artifact"]})
        self.assertNotEqual(out["state"], "closed_verified",
                            f"forged reason credited: {out}")
        self.assertEqual(rb.ledger_read(str(run), A)["state"],
                         "external_closed_unknown")
        with self.assertRaises(rb.BindingRefusal):
            rb.before_launch(str(run), nodes[1], "skey", [])

    def test_r3b_honest_external_close_blocks_successor(self):
        s = make_store("r3b")
        A, B = create(s, "A"), create(s, "B")
        sh([BD, "dep", A, "--blocks", B], s)
        gA, _ = goal("r3b", A, "r3b-A.artifact")
        gB, _ = goal("r3b", B, "r3b-B.artifact")
        nodes = [{"id": "A", "type": "agent", "goal": gA, "context": "x"},
                 {"id": "B", "type": "agent", "after": ["A"], "goal": gB,
                  "context": "x"}]
        run, _home = make_run("r3b", nodes)
        cf = cred("r3b", s, [A, B])
        admit(run, request(s, [A, B], "r3bk"), cf)
        sh([BD, "update", A, "--claim"], s)
        sh([BD, "close", A, "--reason", "done by human"], s)
        out = rb.authorize_close(str(run), A, "1",
                                 {"verdict": "accept",
                                  "artifacts": ["x.artifact"]})
        self.assertEqual(out["state"], "external_closed_unknown")
        with self.assertRaises(rb.BindingRefusal):
            rb.before_launch(str(run), nodes[1], "skey", [])

    def test_r3c_honest_close_reason_carries_runner_nonce(self):
        s = make_store("r3c")
        A = create(s, "A")
        gA, art = goal("r3c", A, "r3c-A.artifact")
        nodes = [{"id": "A", "type": "agent", "goal": gA, "context": "x"}]
        run, _home = make_run("r3c", nodes)
        cf = cred("r3c", s, [A])
        admit(run, request(s, [A], "r3ck"), cf)
        action, _ = rb.before_launch(str(run), nodes[0], "sk", [])
        self.assertEqual(action, "spawn")
        # the worker's effects, exactly as fake-hermes would write them
        with open(art, "w") as f:
            f.write("artifact\n")
        ev_mod.WorkerSurface(str(s), actor="worker-r", bd_bin=BD) \
            .record_evidence(A, attempt="1",
                             artifacts=[os.path.basename(art)],
                             summary="direct")
        demote = rb.after_launch(str(run), nodes[0], {
            "status": "done",
            "output": {"bead": A, "attempt": "1",
                       "artifact": os.path.basename(art),
                       "worker": "worker-r"}})
        self.assertIsNone(demote)
        row = show(s, A)
        self.assertEqual(row["status"], "closed")
        self.assertEqual(row["assignee"], "verifier-r")
        nonce = None
        idir = run / "beads" / "intents"
        for fn in sorted(os.listdir(idir)):
            rec = json.loads((idir / fn).read_text())
            if rec.get("operation") == "launch" and rec.get("bead") == A:
                nonce = rec.get("nonce")
        self.assertTrue(nonce, "no runner-held nonce in the launch intent")
        self.assertIn(f"nonce={nonce}", row["close_reason"])

    # ---------- R4: durable launch intent BEFORE the claim ----------
    def test_r4_launch_intent_fsynced_before_claim(self):
        import claims as claims_mod
        s = make_store("r4")
        A = create(s, "A")
        gA, _ = goal("r4", A, "r4-A.artifact")
        nodes = [{"id": "A", "type": "agent", "goal": gA, "context": "x"}]
        run, _home = make_run("r4", nodes)
        cf = cred("r4", s, [A])
        admit(run, request(s, [A], "r4k"), cf)
        seen = {}
        orig = claims_mod.claim

        def spy(workspace, iid, **kw):
            idir = run / "beads" / "intents"
            seen["listing"] = sorted(os.listdir(idir)) \
                if idir.is_dir() else None
            r = orig(workspace, iid, **kw)
            raise SystemExit("simulated runner death right after claim")

        claims_mod.claim = spy
        try:
            with self.assertRaises(SystemExit):
                rb.before_launch(str(run), nodes[0], "sk", [])
        finally:
            claims_mod.claim = orig
        self.assertIsNotNone(seen["listing"],
                             "intents dir ABSENT when the claim landed")
        hits = [f for f in seen["listing"] if f.startswith("A.a")]
        self.assertTrue(hits, f"no launch intent at claim time: {seen}")
        rec = json.loads((run / "beads" / "intents" / hits[0]).read_text())
        self.assertEqual(rec["state"], "intent")
        self.assertEqual(rec["bead"], A)
        self.assertTrue(rec.get("nonce"), "launch intent carries no nonce")
        row = show(s, A)                       # claim effect landed w/o intent
        self.assertEqual(row["status"], "in_progress")
        self.assertEqual(row["assignee"], "worker-r")

    # ---------- pre-existing gates, kept ----------
    def test_stop_race(self):
        """Stock stop race (existing test, kept): stop.request mid-flight
        stops the wave and B is never dispatched (hooks inert, no grant)."""
        s = make_store("stoprace")
        A, B = create(s, "A"), create(s, "B")
        sh([BD, "dep", A, "--blocks", B], s)
        gA, _ = goal("stoprace", A, "stoprace-A.artifact", prefix="SLEEP 3 ")
        gB, _ = goal("stoprace", B, "stoprace-B.artifact")
        nodes = [{"id": "A", "type": "agent", "goal": gA, "context": "x",
                  "max_turns": 5, "timeout": 20},
                 {"id": "B", "type": "agent", "after": ["A"], "goal": gB,
                  "context": "x", "max_turns": 5, "timeout": 20}]
        run, home = make_run("stoprace", nodes)
        import threading

        def drop_stop():
            time.sleep(0.5)
            (run / "stop.request").touch()
        t = threading.Thread(target=drop_stop, daemon=True)
        t.start()
        p = wf_run("stoprace", env_for("stoprace", home, s))
        t.join()
        self.assertIn("WORKFLOW_STOPPED stoprace", p.stdout, p.stdout[-400:])
        self.assertFalse((run / "nodes" / "B.json").exists())

    def test_no_second_scheduler(self):
        import ast
        import inspect
        src = inspect.getsource(rb)
        self.assertNotIn("acquire_lock", src)
        self.assertNotIn("class Scheduler", src)
        self.assertNotIn("class Runner", src)
        # AST audit: neither module may EXECUTE a process (subprocess.Popen
        # / os.system / fork). The string 'Popen(' inside install()'s
        # anchors is a literal, not a call — the AST proves that.
        banned = {"Popen", "popen", "system", "fork", "fork_exec",
                  "spawnl", "spawnv", "execv", "execve", "killpg", "kill"}
        for mod in (rb, runner_hooks):
            tree = ast.parse(inspect.getsource(mod))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    f = node.func
                    name = (f.attr if isinstance(f, ast.Attribute)
                            else getattr(f, "id", ""))
                    self.assertNotIn(name, banned,
                                     f"{mod.__name__} executes "
                                     f"{name} — second scheduler")


if __name__ == "__main__":
    unittest.main(verbosity=2)
