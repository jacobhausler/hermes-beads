"""hbl-pnu.3.5 Work half: the ONE authenticated replayable Work door.

A Work click on a selected bead becomes exactly ONE admission: the door
signs host-side (only the holder of the 0600 credential file's secret can
mint the HMAC — the click itself can fabricate nothing) and calls
runner_binding.admit_work, which IS the one authenticated replayable door:
a byte-equal replay of the same principal+request_key returns the stored
receipt and never re-admits. The stock patched Workflow runner stays the
ONE scheduler (the door launches it and never Popen's anything else, never
flocks the runner lock); execution runs under the binding's claim/verifier/
closure law, so closure happens ONLY via the runner's verifier — worker
exit is evidence, never acceptance.

Truthful run state (the vocabulary contains no "delivered"):
    admitted          receipt stored, worker not yet observed
    running           worker spawn observed (runner progress)
    succeeded         runner terminal + ledger closed_verified
    failed            runner terminal, ledger not closed_verified
    uncertain         deadline passed / runner died before any event
    cancel_requested  stop requested while the runner is still alive
    cancelled         only after the runner process confirms terminal

Qualification is checked BEFORE any claim (bot_handoff.run_work calls
precheck_error first): without a bound door, without the host credential
file, or without the isolated patched Workflow checkout, Work is visibly
unavailable with a typed reason — never a fake success, and the bead is
never claimed.
"""
import hashlib
import json
import os
import subprocess
import threading
import time

import runner_binding as rb

STATE_VOCAB = ("admitted", "running", "succeeded", "failed", "uncertain",
               "cancel_requested", "cancelled")
TERMINAL = ("succeeded", "failed", "cancelled", "uncertain")
WALL_GRACE = 60.0


class WorkDoorError(Exception):
    """Typed door-level failure with its reason preserved verbatim."""


def _jload(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _pid_alive(pid):
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    try:  # a zombie (awaited death) is NOT alive
        with open(f"/proc/{pid}/stat", encoding="utf-8") as f:
            state = f.read().rsplit(") ", 1)[1].split()[0]
        return state != "Z"
    except OSError:
        return True


class WorkDoor:
    """Callable door bound via bot_handoff.bind_runner_door.

    __call__(payload) — payload from run_work: {workspace, bead, actor,
    row, reasons, surface, request_key}. Returns the admission receipt with
    handed_off:true, delivery:false, no_dispatch:true — handed_off is never
    delivered.
    """

    def __init__(self, *, store, bd_bin="bd", run_base, workflow_src,
                 hermes_bin, authority_domain="lab",
                 profile="beads-work", verifier="verifier-r",
                 worker_default="worker-r", goal_prefix="",
                 node_timeout=600, node_max_turns=8, wall_deadline_s=None,
                 first_message_s=0):
        self.store = os.path.abspath(str(store))
        self.bd_bin = str(bd_bin)
        self.run_base = os.path.abspath(str(run_base))
        self.workflow_src = os.path.abspath(str(workflow_src))
        self.hermes_bin = str(hermes_bin)
        self.authority_domain = authority_domain
        self.profile = profile
        self.verifier = verifier
        self.worker_default = worker_default
        self.goal_prefix = goal_prefix
        self.node_timeout = int(node_timeout)
        self.node_max_turns = int(node_max_turns)
        self.wall_deadline_s = (
            float(wall_deadline_s) if wall_deadline_s is not None
            else self.node_timeout + WALL_GRACE)
        self.first_message_s = first_message_s
        self._lock = threading.Lock()
        self._procs = {}

    # ---------- qualification (typed unavailability, pre-claim) ------------
    def precheck_error(self):
        """None when qualified; a typed reason string otherwise. Checked
        BEFORE any claim so an unqualified Work never touches the store."""
        cred, problem = rb._read_host_credential(rb.credential_path())
        if problem:
            return (f"host credential unusable at {rb.credential_path()!r}: "
                    f"{problem} — runner qualification absent")
        if not (os.path.isfile(os.path.join(self.workflow_src, "wf.py"))
                and os.path.isfile(
                    os.path.join(self.workflow_src, "wfcommon.py"))):
            return (f"isolated patched Workflow checkout absent under "
                    f"{self.workflow_src!r}: runner qualification absent")
        if not os.path.isfile(self.hermes_bin):
            return (f"worker binary absent at {self.hermes_bin!r}: runner "
                    "qualification absent")
        return None

    def read_credential(self):
        """The host credential at runner_binding.credential_path(), read
        through runner_binding's own fd-bound provenance check (no second,
        weaker reader; no caller-chosen path)."""
        cred, problem = rb._read_host_credential(rb.credential_path())
        if problem:
            raise WorkDoorError(f"host credential unusable: {problem}")
        if not isinstance(cred, dict) or not cred.get("principal"):
            raise WorkDoorError("host credential lacks a principal")
        secret = cred.get("secret") or ""
        if not isinstance(secret, str) or len(secret) < rb.MIN_SECRET_LEN:
            raise WorkDoorError("host credential carries no host-generated "
                                f"secret (>= {rb.MIN_SECRET_LEN} chars)")
        return cred

    # ---------- the door ----------------------------------------------------
    def admission_error(self, bead, request_key, workspace=None):
        """Every refusal the door can decide WITHOUT effects, as a typed
        string (None = admissible). run_work calls this BEFORE claiming, so
        a keyless / out-of-scope / wrong-store click never leaves a claimed
        bead behind."""
        if not request_key or not isinstance(request_key, str):
            return ("work click without an idempotency key is refused — the "
                    "door cannot prove one admission per click")
        err = self.precheck_error()
        if err:
            return f"runner_unqualified: {err}"
        try:
            cred = self.read_credential()
        except WorkDoorError as exc:
            return str(exc)
        if bead not in set(cred.get("approved_beads") or []):
            return (f"bead {bead} outside the credential's approved scope: "
                    "scoped admission refused before effects")
        if cred.get("store") and os.path.realpath(cred["store"]) != \
                os.path.realpath(self.store):
            return ("credential/store mismatch: the door may not retarget "
                    "the canonical store")
        if workspace is not None and os.path.realpath(str(workspace)) != \
                os.path.realpath(self.store):
            return ("workspace is not the door's canonical store: refused "
                    "before effects")
        return None

    def __call__(self, payload):
        bead = payload["bead"]
        actor = payload.get("actor") or self.worker_default
        key = payload.get("request_key")
        with self._lock:
            err = self.admission_error(bead, key)
            if err:
                raise WorkDoorError(err)
            cred = self.read_credential()

            # ONE door call -> ONE admit_work. The click's key is claimed
            # in a durable O_EXCL map BEFORE anything: the winner derives
            # the run dir; a duplicate click reads the SAME run dir and
            # replays through admit_work's own key-index law — one receipt,
            # never a second admission, never a second worker.
            os.makedirs(self.run_base, exist_ok=True)
            key_tag = hashlib.sha256(
                (key + "\0" + self.store).encode()).hexdigest()[:12]
            marker = os.path.join(self.run_base, ".keys", key_tag)
            os.makedirs(os.path.dirname(marker), exist_ok=True)
            run_id = f"work-{bead}-{key_tag}"
            try:
                fd = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                             0o600)
                os.write(fd, run_id.encode())
                os.close(fd)
            except FileExistsError:
                with open(marker, encoding="utf-8") as f:
                    run_id = f.read().strip()
            run_dir = os.path.join(self.run_base, run_id)
            self._build_run(run_dir, run_id, key, bead, actor, cred)

            req = {"authority_domain": self.authority_domain,
                   "profile": self.profile, "store": self.store,
                   "beads": [bead], "worker": actor,
                   "verifier": self.verifier, "request_key": key}
            secret = cred["secret"]
            sig = rb.sign_request(secret, req, cred["principal"])
            try:
                res = rb.admit_work(req, authenticated_context={
                    "principal": cred["principal"], "signature": sig},
                    run_root=run_dir, bd_bin=self.bd_bin)
            except rb.BindingError as exc:
                raise WorkDoorError(f"admission refused: {exc}") from exc

            # launch marker (O_EXCL): the first winner launches the runner;
            # a duplicate NEVER re-launches (no second scheduler, no second
            # dispatch) — it only replays the receipt.
            marker = os.path.join(run_dir, "beads", "LAUNCH")
            os.makedirs(os.path.dirname(marker), exist_ok=True)
            try:
                fd = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                             0o600)
                os.close(fd)
                self._launch(run_dir, run_id)
            except FileExistsError:
                pass

            receipt = dict(res["receipt"])
            receipt.update({"handed_off": True, "delivery": False,
                            "no_dispatch": True,
                            "replayed": bool(res.get("replayed")),
                            "run_id": res["run_id"], "bead": bead})
            return receipt

    def _build_run(self, run_dir, run_id, key, bead, actor, cred):
        """Freeze the plan before the runner starts; idempotent (a replay
        never rewrites the frozen graph)."""
        if os.path.exists(os.path.join(run_dir, "graph.json")):
            return
        for d in ("nodes", "gates", ".home", "artifacts"):
            os.makedirs(os.path.join(run_dir, d), exist_ok=True)
        artifact = os.path.join(run_dir, "artifacts", f"{run_id}.artifact")
        query = (f"{self.goal_prefix}RUNNER-TASK worker BEAD={bead} "
                 f"ATTEMPT=1 ARTIFACT={artifact} WORKER={actor}")
        node = {"id": "work", "type": "agent", "goal": query,
                "context": "work half", "max_turns": self.node_max_turns,
                "timeout": self.node_timeout}
        rb._atomic_write(os.path.join(run_dir, "graph.json"),
                         {"name": run_id, "nodes": [node]})
        rb._atomic_write(os.path.join(run_dir, "run.json"), {
            "hermes_bin": self.hermes_bin, "concurrency": 1,
            "node_timeout": self.node_timeout,
            "first_message_s": self.first_message_s})
        open(os.path.join(run_dir, "fake.log"), "a").close()
        rb._atomic_write(os.path.join(run_dir, "beads",
                                       "door.json"), {
            "bead": bead, "worker": actor, "store": self.store,
            "bd_bin": self.bd_bin, "key_sha256":
                hashlib.sha256(key.encode()).hexdigest(),
            "admitted_at": time.time(), "node_timeout": self.node_timeout,
            "wall_deadline_s": self.wall_deadline_s})

    def _launch(self, run_dir, run_id):
        """Hand the run to the STOCK runner process — the ONE scheduler
        under its own flock. Detached; the door survives. The credential
        env rides to the runner only (the patched spawn path strips it from
        every child — R2: children hold no admission secret)."""
        env = dict(os.environ)
        env["HERMES_HOME"] = os.path.join(run_dir, ".home")
        env["WF_RUNS_ROOT"] = self.run_base
        env.pop(rb.CRED_ENV, None)   # never carried; the runner verifies nothing
        env["FAKE_LOG"] = os.path.join(run_dir, "fake.log")
        env["BEADS_STORE"] = self.store
        env["BEADS_LANE"] = os.path.dirname(os.path.abspath(rb.__file__))
        env["BEADS_BD"] = self.bd_bin
        logf = open(os.path.join(run_dir, "runner.out"), "a")
        proc = subprocess.Popen(
            ["python3", os.path.join(self.workflow_src, "wf.py"), "run",
             run_id],
            cwd=self.run_base, env=env, stdout=logf,
            stderr=subprocess.STDOUT, start_new_session=True,
            stdin=subprocess.DEVNULL)
        try:
            logf.close()          # the child holds its own fd
        except OSError:
            pass
        self._procs[run_dir] = proc
        rb._atomic_write(os.path.join(run_dir, "beads", "wf.json"),
                         {"wf_pid": proc.pid, "launched_at": time.time()})

    # ---------- truthful state: NATIVE OBSERVATION only --------------------
    def _key_tag(self, request_key):
        return hashlib.sha256(
            (request_key + "\0" + self.store).encode()).hexdigest()[:12]

    def run_dir(self, request_key):
        """The durable O_EXCL key map is the ONLY resolution path: a stale
        dir can never shadow this store's run."""
        marker = os.path.join(self.run_base, ".keys",
                              self._key_tag(request_key))
        try:
            with open(marker, encoding="utf-8") as f:
                run_id = f.read().strip()
        except FileNotFoundError:
            return None
        run_dir = os.path.join(self.run_base, run_id)
        return run_dir if os.path.isdir(run_dir) else None

    def spawn_count(self, request_key):
        run_dir = self.run_dir(request_key)
        if not run_dir:
            return 0
        try:
            with open(os.path.join(run_dir, "fake.log"),
                      encoding="utf-8", errors="replace") as f:
                return sum(1 for ln in f if ln.startswith("SPAWN "))
        except FileNotFoundError:
            return 0

    def runner_alive(self, request_key):
        run_dir = self.run_dir(request_key)
        if not run_dir:
            return False
        proc = self._procs.get(run_dir)
        if proc is not None:
            return proc.poll() is None          # reaps our own zombie
        wf = _jload(os.path.join(run_dir, "beads", "wf.json"))
        if not wf:
            return False
        return _pid_alive(wf["wf_pid"])

    def worker_alive(self, request_key):
        """The runner-owned spawn record (nodes/work.json) is the truth
        about the live worker child — the door reads it, never registers."""
        run_dir = self.run_dir(request_key)
        if not run_dir:
            return False
        rec = _jload(os.path.join(run_dir, "nodes", "work.json"))
        if not rec or rec.get("status") != "running" or "pid" not in rec:
            return False
        return _pid_alive(rec["pid"])

    def status(self, request_key):
        run_dir = self.run_dir(request_key)
        if not run_dir:
            return {"state": "unknown", "detail": "no admission for this "
                    "request key"}
        p = rb._run_paths(run_dir)
        door = _jload(os.path.join(p["base"], "door.json"), {})
        bead = door.get("bead")
        base = {"run_dir": run_dir, "spawns": self.spawn_count(request_key),
                "delivery": False, "no_dispatch": True}
        receipt = _jload(p["receipt"])
        if receipt is None:
            return dict(base, state="unknown",
                        reason="admission receipt not on disk")
        led = None
        try:
            led = rb.ledger_read(run_dir, bead)
        except Exception:
            led = None
        base["ledger"] = (led or {}).get("state")
        if os.path.exists(os.path.join(p["base"], "STOP")):
            alive = self.runner_alive(request_key)
            return dict(base,
                        state="cancel_requested" if alive else "cancelled",
                        runner_alive=alive)
        alive = self.runner_alive(request_key)
        if not alive:
            # terminal candidate — classify ONLY from observed artifacts
            events = os.path.exists(os.path.join(run_dir,
                                                 "events.jsonl"))
            if not events:
                return dict(base, state="uncertain", runner_alive=False,
                            reason="runner died before any event")
            node_rec = _jload(os.path.join(run_dir, "nodes", "work.json"))
            if (led or {}).get("state") == "closed_verified" \
                    and (node_rec or {}).get("status") == "done":
                return dict(base, state="succeeded", runner_alive=False)
            return dict(base, state="failed", runner_alive=False,
                        node_status=(node_rec or {}).get("status"))
        # runner alive: wall deadline?
        deadline = (door.get("admitted_at") or 0) + (door.get(
            "wall_deadline_s") or self.wall_deadline_s)
        if time.time() > deadline:
            return dict(base, state="uncertain", runner_alive=True,
                        reason="wall deadline passed; runner not confirmed "
                               "terminal")
        if self.spawn_count(request_key) or self.worker_alive(request_key):
            return dict(base, state="running", runner_alive=True)
        return dict(base, state="admitted", runner_alive=True)

    def wait_terminal(self, request_key, timeout=120.0, every=0.25):
        end = time.time() + timeout
        while True:
            st = self.status(request_key)
            if st["state"] in TERMINAL:
                return st
            if time.time() >= end:
                # our OWN wait deadline: still truth — never "delivered"
                st["state"] = "uncertain"
                st["reason"] = "wait deadline passed without runner " \
                               "terminal confirmation"
                return st
            time.sleep(every)

    # ---------- cancel: signed stop, truthful two-phase ---------------------
    def cancel(self, request_key):
        run_dir = self.run_dir(request_key)
        if not run_dir:
            return {"ok": False, "error": "no admission for this request "
                    "key", "state": "unknown"}
        cred = self.read_credential()
        sig = rb.sign_stop(cred["secret"], run_dir, cred["principal"])
        res = rb.stop_work(run_dir, authenticated_context={
            "principal": cred["principal"], "signature": sig})
        # TRUTHFUL: while the runner is alive the state is cancel_requested;
        # "cancelled" only once it is confirmed terminal. stop_work itself
        # never claims more than stopping_unknown.
        alive = self.runner_alive(request_key)
        return {"ok": True,
                "state": "cancel_requested" if alive else "cancelled",
                "runner_alive": alive, "stop": res.get("state"),
                "delivery": False, "no_dispatch": True}

    def run_state(self, request_key):
        """Read-only truthful run state for the desktop panel (same
        vocabulary; never 'delivered', never 'terminated' before the
        runner confirms)."""
        return self.status(request_key)


def make_work_door(**kw):
    """Factory so bot_handoff callers never import runner_binding directly."""
    return WorkDoor(**kw)
