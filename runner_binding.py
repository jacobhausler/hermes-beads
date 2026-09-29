"""hbl-pnu.3.3 optional runner binding: admitted Work, verification, recovery.

Policy/IO layer the PATCHED isolated Workflow runner calls at its spawn and
commit seams. It NEVER schedules: no subprocess.Popen, no flock acquisition
on the runner lock, no Scheduler/Runner class (AST-audited in the tests);
the stock runner (wf.py) stays the one scheduler under its single-runner
flock. Without an admitted grant the hooks are inert and the runner keeps
byte-identical stock behavior; without this module at all the native plugin
and the stock runner remain fully usable (the hooks try-import).

Boundaries honored (CONTRACTS-v3):
  C3  exact-ID native admission: scoped `bd ready` parses, claim of the
      EXACT id through claims.py (native CAS where the primitive exists:
      unclaim --if-assignee; close is NOT natively conditional in pinned
      bd 1.3.0 and CAS is never faked), post-claim blocker inspection via
      claims.inspect_after_claim. The C3 posture is a DECLARED cooperating
      single-mutator topology: the bound verifier actor is the only
      admitted close mutator for the run; independent native writers are
      DETECTED by read-window signatures and cause refusal/quarantine,
      never blind retry.
  C4  authenticated admission is verified against a host credential file
      (env-pinned path, mode 0600) — never from the request body or the
      environment alone. The installed Hermes admission door remains
      explicitly UNQUALIFIED (docs/runner-binding.md): a missing credential
      is a typed BLOCK with no_dispatch, never a fallback principal.
  Effects  durable intents precede comment/close; an unsettled intent is
      reconciled by NATIVE OBSERVATION (replay-free), never blind-retried.
      A dead-but-effect-observed attempt completes WITHOUT re-spawn.
  Closure  only via evidence.authorized_close (verifier actor, artifact-
      citing reason, causal attempt token, read-back). Rejected / externally
      closed / ambiguous beads never unlock a successor: worker exit is
      evidence, not acceptance.

Trusted-agent surface policy, not a hostile-shell sandbox.
"""
import fcntl
import hashlib
import hmac
import json
import os
import re
import secrets
import stat
import time

import claims
import evidence
import native
import read_model

BINDING_DIR = "beads"           # run-dir namespace owned by this binding
CRED_ENV = "BEADS_ADMISSION_CREDENTIAL_FILE"  # legacy name; popped from child env, NEVER trusted
CRED_RELPATH = ("beads", "admission-credential.json")
MIN_SECRET_LEN = 32                 # host-generated secret; not restatable
_LEDGER_STATES = ("closed_verified", "rejected", "quarantined",
                  "external_closed_unknown", "blocked", "failed")

RUNNER_TASK = re.compile(r"RUNNER-TASK worker BEAD=(\S+) ATTEMPT=(\S+) "
                        r"ARTIFACT=(\S+) WORKER=(\S+)")


class BindingError(Exception):
    pass


class BindingRefusal(BindingError):
    """Typed hold/refusal: the spawn never happens; reason preserved."""


# ---------- durable json helpers (atomic: tmp + os.replace) ----------

def _atomic_write(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, sort_keys=True, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    # R4: the rename itself must be durable before any native effect rides
    # on this record — fsync the containing directory too.
    try:
        dfd = os.open(os.path.dirname(path), os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    except OSError:
        pass


def _jload(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _run_paths(run):
    run = str(run)
    base = os.path.join(run, BINDING_DIR)
    return {"run": run, "base": base,
            "grant": os.path.join(base, "grant.json"),
            "receipt": os.path.join(base, "receipt.json"),
            "keys": os.path.join(base, "keys"),
            "ledger": os.path.join(base, "ledger"),
            "intents": os.path.join(base, "intents")}


def _canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def _digest(obj):
    return hashlib.sha256(_canonical(obj).encode()).hexdigest()


def node_task(node):
    """Bead binding rides the goal's RUNNER-TASK line (the stock graph
    validator's strict AGENT_KEYS rejects extra node keys, so nothing is
    added to the node dict). Returns (bead, attempt, artifact, worker) or
    None for a non-beads node."""
    m = RUNNER_TASK.search((node or {}).get("goal") or "")
    return m.groups() if m else None


# ---------- authentication (C4 door: host credential file; lab-qualified) ----------

def sign_request(secret, request, principal):
    """HOST-side helper: the only way to mint a valid admission signature is
    to hold the credential file's secret — the request body cannot create it.
    (A real deployment calls this from the host's operator tooling.)"""
    return _sign(secret, {"action": "admit", "principal": principal,
                          "request": request})


def sign_stop(secret, run, principal):
    """HOST-side helper for an authenticated stop."""
    return _sign(secret, {"action": "stop", "principal": principal,
                          "run": str(run)})


def _sign(secret, message):
    return hmac.new(str(secret).encode(), _canonical(message).encode(),
                    hashlib.sha256).hexdigest()


def credential_path():
    """The ONE place an admission credential may live: the host's own Hermes
    state dir, derived from HERMES_HOME. HERMES_HOME is TRUSTED host config:
    a caller that controls the runner's environment or runs as the runner
    uid is inside the trust boundary (it could equally rewrite this file).
    The removed per-call env pointer let an in-boundary-looking request aim
    the door at any self-minted file; this path cannot be chosen per call."""
    home = os.environ.get("HERMES_HOME") or os.path.join(
        os.path.expanduser("~"), ".hermes")
    return os.path.join(home, *CRED_RELPATH)


def provision_credential(principal, approved_beads, store, *, rotate=False):
    """Host-side, once: create the credential with a fresh random secret.
    Returns the secret for the host's signer. Refuses to overwrite unless
    rotate=True (rotation invalidates every outstanding signature)."""
    if not principal or not approved_beads:
        raise BindingRefusal("provisioning needs a principal and a non-empty "
                             "approved_beads ceiling")
    path = credential_path()
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    os.chmod(os.path.dirname(path), 0o700)
    secret = secrets.token_hex(32)
    body = json.dumps({"principal": principal, "secret": secret,
                       "approved_beads": list(approved_beads),
                       "store": os.path.realpath(store)}).encode()
    tmp = path + ".new"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        os.write(fd, body)
        os.fsync(fd)
    finally:
        os.close(fd)
    if os.path.lexists(path) and not rotate:
        os.unlink(tmp)
        raise BindingRefusal(f"credential already provisioned at {path}; "
                             "pass rotate=True to replace it")
    os.replace(tmp, path)
    return secret


def _read_host_credential(path):
    """Owner-only regular file with a single link, not a symlink, in an
    owner-only directory that is itself not a symlink. Checks and read are
    bound to the SAME open fds (openat + O_NOFOLLOW + fstat), so a swap
    between check and read, a symlinked beads dir, or a same-uid hardlink
    of a self-minted file is refused. Returns (cred, problem)."""
    uid = os.geteuid()
    try:
        dfd = os.open(os.path.dirname(path),
                      os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError:
        return None, "no host credential provisioned (or its dir is a symlink)"
    try:
        dst = os.fstat(dfd)
        if dst.st_uid != uid:
            return None, "credential directory is not owned by the runner uid"
        if dst.st_mode & 0o077:
            return None, "credential directory must be owner-only (0700)"
        try:
            fd = os.open(os.path.basename(path), os.O_RDONLY | os.O_NOFOLLOW,
                         dir_fd=dfd)
        except OSError:
            return None, ("no host credential provisioned "
                          "(or it is a symlink — refused)")
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode):
                return None, "credential is not a regular file"
            if st.st_uid != uid:
                return None, "credential is not owned by the runner uid"
            if st.st_mode & 0o077:
                return None, "admission credential file must be mode 0600"
            if st.st_nlink != 1:
                return None, "credential has extra hard links (refused)"
            with os.fdopen(os.dup(fd), "rb") as f:
                raw = f.read(65536)
        finally:
            os.close(fd)
    finally:
        os.close(dfd)
    try:
        return json.loads(raw), None
    except ValueError:
        return None, "credential file is not valid JSON"


def _verify_credential(authenticated_context, message=None):
    """The door: the request must carry an HMAC the request body cannot
    mint, verified against the secret in the host credential at
    credential_path() (fixed location, owner-only, provisioned by
    provision_credential). A file elsewhere is never consulted."""
    if not isinstance(authenticated_context, dict):
        raise BindingRefusal("authenticated_context must be supplied by the "
                             "host, never by the request body")
    principal = authenticated_context.get("principal")
    signature = authenticated_context.get("signature") or ""
    cred_path = credential_path()
    problem = None if principal else "missing principal"
    cred = None
    if not problem:
        cred, problem = _read_host_credential(cred_path)
    if problem:
        raise BindingRefusal(
            f"workflow_admission_unqualified: {problem} ({cred_path}); Work "
            "remains unavailable — caller-claimed identity is never authority")
    if not isinstance(cred, dict) or cred.get("principal") != principal:
        raise BindingRefusal(
            "principal mismatch against the host credential file "
            "(selected row / caller-provided principal are not authority)")
    secret = cred.get("secret") or ""
    if not isinstance(secret, str) or len(secret) < MIN_SECRET_LEN:
        raise BindingRefusal(
            "credential file carries no host-generated secret "
            f"(>= {MIN_SECRET_LEN} chars): a file that only restates the "
            "principal is self-issued, not a credential")
    message = dict(message or {})
    message.setdefault("principal", principal)
    if not signature or not hmac.compare_digest(
            _sign(secret, message), str(signature)):
        raise BindingRefusal(
            "admission signature missing/invalid: the request was not "
            "signed by the holder of the host secret (HMAC over the exact "
            "request + principal)")
    return cred


# ---------- admission: one door, durable grant + exact-replay receipt ------

def admit_work(request, *, authenticated_context, run_root, bd_bin):
    """One fixed-scope authenticated admission. Byte-equal replay of the
    same principal+request_key returns the stored receipt; ANY change in
    principal, store, or exact ordered bead scope rejects. Nothing here
    dispatches — the runner drives execution."""
    cred = _verify_credential(
        authenticated_context,
        {"action": "admit", "request": request})
    for k in ("authority_domain", "profile", "store", "beads", "worker",
              "verifier", "request_key"):
        if k not in (request or {}):
            raise BindingRefusal(f"grant missing field {k}")
    beads = request["beads"]
    if not isinstance(beads, list) or not beads \
            or not all(isinstance(b, str) and b.strip() for b in beads):
        raise BindingRefusal("grant requires a non-empty exact ordered list "
                             "of approved native IDs (no discovery, no sweep)")
    store = os.path.realpath(str(request["store"]))
    if not os.path.isdir(os.path.join(store, ".beads")):
        raise BindingRefusal(f"canonical store unavailable: {store} has no "
                             ".beads/ — refusing before effects")
    if cred.get("store") and cred["store"] != store:
        raise BindingRefusal("credential/store mismatch — the principal may "
                             "not retarget the canonical store")
    if cred.get("authority_domain") and \
            request["authority_domain"] != cred["authority_domain"]:
        raise BindingRefusal("credential/authority-domain mismatch")
    approved = set(cred.get("approved_beads") or [])
    if not approved:
        raise BindingRefusal(
            "credential carries no approved_beads ceiling — admission is "
            "refused (an omitted ceiling would mean no scope ceiling at all; "
            "the approved set is mandatory, never grown from discovery)")
    if not set(beads) <= approved:
        raise BindingRefusal(
            "out-of-grant IDs in request: "
            f"{sorted(set(beads) - approved)} (a fixed approved set never "
            "grows from discovery)")

    grant = {
        "authority_domain": request["authority_domain"],
        "profile": request["profile"],
        "store": store,
        "bd_bin": os.path.realpath(str(bd_bin)),
        "lane": os.path.dirname(os.path.abspath(native.__file__)),
        "beads": list(beads),
        "worker": request["worker"], "verifier": request["verifier"],
        "max_attempts": int(request.get("max_attempts", 1)),
        "grant_revision": int(request.get("grant_revision", 1)),
        "request_key": str(request["request_key"]),
        "principal": authenticated_context["principal"],
        "c3_topology": "cooperating_single_mutator",
        "policy": dict(request.get("policy") or {}),
    }
    digest = _digest(grant)
    key_hash = _digest({"principal": grant["principal"],
                        "request_key": grant["request_key"]})

    paths = _run_paths(run_root)
    os.makedirs(paths["keys"], exist_ok=True)
    lk = os.open(os.path.join(paths["base"], "key-index.lock"),
                 os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(lk, fcntl.LOCK_EX)       # admission key-index lock —
        receipt = _jload(paths["receipt"])   # NOT a scheduler
        if receipt:
            if receipt.get("grant_digest") != digest:
                raise BindingRefusal(
                    "request_key collision with changed principal/store/"
                    "scope/policy: exact-key replay requires a byte-"
                    "equivalent grant")
            return {"ok": True, "replayed": True,
                    "run_id": receipt["run_id"], "receipt": receipt}
        receipt = {"ok": True, "run_id": os.path.basename(paths["run"]),
                   "grant_digest": digest, "key_hash": key_hash,
                   "admitted_at": time.time(), "beads": grant["beads"]}
        _atomic_write(paths["grant"], grant)
        _atomic_write(os.path.join(paths["keys"], key_hash + ".json"),
                      receipt)
        _atomic_write(paths["receipt"], receipt)
        return {"ok": True, "replayed": False,
                "run_id": receipt["run_id"], "receipt": receipt}
    finally:
        fcntl.flock(lk, fcntl.LOCK_UN)
        os.close(lk)


def _grant(run):
    paths = _run_paths(run)
    g = _jload(paths["grant"])
    if not g:
        raise BindingRefusal("no admitted grant for this run — Work requires "
                             "admit_work() first (a stock run never reaches "
                             "this path)")
    return g, paths


def granted(run):
    """Hook-side probe: is this run beads-bound? Stock runs stay inert."""
    return _jload(os.path.join(_run_paths(run)["grant"])) is not None


# ---------- ledger + durable effect intents ----------

def ledger_read(run, bead_id):
    return _jload(os.path.join(_run_paths(run)["ledger"], f"{bead_id}.json"))


def ledger_write(run, bead_id, rec):
    assert rec.get("state") in _LEDGER_STATES, f"unknown ledger state {rec!r}"
    _atomic_write(os.path.join(_run_paths(run)["ledger"],
                               f"{bead_id}.json"), rec)


def _intent_path(run, name, attempt):
    safe = "".join(c if c.isalnum() or c in "-._" else "_" for c in str(name))
    return os.path.join(_run_paths(run)["intents"], f"{safe}.a{attempt}.json")


def _next_attempt(run, node_id):
    n = 0
    while os.path.exists(_intent_path(run, node_id, n + 1)):
        n += 1
    return n + 1


def before_native_effect(run, bead_id, attempt, operation, payload):
    """Durable effect intent BEFORE comment/close. The intent file at state
    'intent' is what reconcile observes after a runner-only death — no
    intent file, no unobserved replay."""
    if operation not in ("comment", "close", "launch"):
        raise BindingRefusal(f"unknown effect operation {operation!r}")
    iid = f"{bead_id}.a{attempt}.{operation}.{int(time.time() * 1000)}"
    _atomic_write(_intent_path(run, f"{bead_id}-{operation}", attempt),
                  {"intent": iid, "bead": bead_id, "attempt": attempt,
                   "operation": operation, "state": "intent",
                   "payload_digest": _digest(payload), "at": time.time()})
    return iid


def _settle_intent(path, state, **extra):
    rec = _jload(path)
    if rec:
        rec.update(state=state, **extra)
        _atomic_write(path, rec)


# ---------- native observation (never trust cached verdicts) ----------

def _observe_effect(store, bd_bin, bead_id, attempt, worker):
    """Native observation whether `attempt` recorded worker evidence."""
    try:
        rows = read_model.comments(store, bead_id, bd_bin=bd_bin) or []
    except native.NativeError:
        return "unknown", []
    hits = [c for c in rows
            if c.get("author") == worker
            and (c.get("text") or "").startswith(f"EVIDENCE attempt={attempt} ")]
    return ("effect" if hits else "none"), hits


def _envelope_artifacts(comment_text):
    try:
        return (comment_text.split("artifacts=")[1]
                .split(" summary=")[0].split(";"))
    except IndexError:
        return []


# ---------- spawn gate (called by the patched runner inside its lock) ------

def before_launch(run, node, skey, argv):
    """Pre-Popen gate. Returns ("spawn", None) to allow, ("complete",
    output) when a prior attempt's real effect is observed (finish WITHOUT
    re-spawn), or raises BindingRefusal to hold the spawn (the runner then
    commits a typed beads_hold failure)."""
    g, paths = _grant(run)
    store, bd_bin = g["store"], g["bd_bin"]
    node_id = node["id"]
    task = node_task(node)
    if os.path.exists(os.path.join(paths["run"], "stop.request")) or \
            os.path.exists(os.path.join(paths["base"], "STOP")):
        raise BindingRefusal("stop latched — no spawn may cross the stop "
                             "boundary (checked under the runner spawn lock)")
    if task is None:
        raise BindingRefusal(f"node {node_id} carries no RUNNER-TASK bead "
                             "binding under an admitted grant (fixed "
                             "approved scope; no discovery)")
    bead_id, goal_attempt, artifact, worker = task
    if bead_id not in g["beads"]:
        raise BindingRefusal(f"out-of-grant bead {bead_id} on node {node_id}")
    if worker != g["worker"]:
        raise BindingRefusal(f"node {node_id} worker token {worker!r} != "
                             f"grant worker {g['worker']!r}")

    led = ledger_read(run, bead_id)
    if led and led.get("state") == "closed_verified":
        raise BindingRefusal(f"{bead_id} already closed_verified this run — "
                             "replay-skip, never re-dispatch")
    if led and led.get("state") in ("quarantined", "external_closed_unknown",
                                    "rejected", "blocked"):
        raise BindingRefusal(
            f"{bead_id} ledger state {led['state']!r} — adjudication "
            "required; blind retry of an observed-uncertain effect is "
            "forbidden")

    # progression gate: successors advance ONLY on this run's verifier-
    # authorized causal closure of each dependency bead.
    graph = _jload(os.path.join(paths["run"], "graph.json"), {}) or {}
    byid = {n["id"]: n for n in graph.get("nodes", [])}
    for dep in node.get("after", []):
        dep_task = node_task(byid.get(dep))
        if dep_task:
            dl = ledger_read(run, dep_task[0])
            if not dl or dl.get("state") != "closed_verified":
                raise BindingRefusal(
                    f"successor gate: dependency {dep} (bead {dep_task[0]}) "
                    "has no verifier-authorized causal closure readback — "
                    "B never advances on worker exit alone")

    attempt = _next_attempt(run, node_id)
    if attempt > g["max_attempts"]:
        raise BindingRefusal(f"node {node_id} attempt {attempt} exceeds "
                             f"grant max_attempts={g['max_attempts']} — "
                             "effect-aware retry budget spent")

    # effect-aware retry: an unsettled prior intent is OBSERVED first —
    # replay-free completion when the effect really landed, effect-safe
    # redispatch when it did not, quarantine when unattributable.
    if attempt > 1:
        prior = _jload(_intent_path(run, node_id, attempt - 1))
        if prior and prior.get("state") == "intent":
            obs, hits = _observe_effect(store, bd_bin, bead_id,
                                        goal_attempt, g["worker"])
            row = read_model.show(store, bead_id, bd_bin=bd_bin) or {}
            if obs == "effect" and row.get("assignee") == g["worker"]:
                _settle_intent(_intent_path(run, node_id, attempt - 1),
                               "settled_reconciled")
                return ("complete",
                        {"bead": bead_id, "attempt": goal_attempt,
                         "artifact": artifact, "worker": worker,
                         "recovered": "observed prior effect",
                         "evidence_comment":
                             (hits[-1].get("text", "") if hits else "")[:400]})
            if obs == "effect":
                ledger_write(run, bead_id,
                             {"state": "quarantined",
                              "attempt": goal_attempt,
                              "reason": "unsettled prior intent resolved to "
                                        "a real native effect this run did "
                                        "not authorize"})
                raise BindingRefusal(f"{bead_id}: prior native effect "
                                     "unattributable — quarantined")
            # obs none/unknown with a dead intent: replay is effect-safe.

    row = read_model.show(store, bead_id, bd_bin=bd_bin) or {}
    if not row.get("assignee"):
        # exact ready parses for the approved ID (advisory eligibility),
        # then the exact-ID claim is the authority step — never claim-next,
        # never a filter standing in for a claim (no claim-to-filter).
        ids = [r.get("id") for r in
               (read_model.ready(store, bd_bin=bd_bin) or [])]
        if bead_id not in ids:
            raise BindingRefusal(
                f"{bead_id} absent from the scoped ready frontier — "
                "refusing before effects")

    # R4: the durable launch intent — carrying the runner-held causal
    # NONCE — is written AND fsynced BEFORE the claim (the first native
    # effect). A death in the claim window therefore leaves an intent that
    # reconcile/before_launch can observe; a claim with no intent can never
    # happen. The nonce is random per launch: only this run knows it, and
    # the verifier-authorized close reason must carry it verbatim (R3).
    nonce = secrets.token_hex(16)
    _atomic_write(_intent_path(run, node_id, attempt),
                  {"intent": f"{node_id}.a{attempt}", "node": node_id,
                   "bead": bead_id, "attempt": attempt,
                   "operation": "launch", "state": "intent", "skey": skey,
                   "nonce": nonce,
                   "argv_digest": _digest(list(argv)), "at": time.time()})
    try:
        claims.claim(store, bead_id, actor=g["worker"], bd_bin=bd_bin)
    except native.NativeError as exc:
        # The claim (the effect) never landed: the intent stays unsettled
        # for reconcile's native observation — never deleted blind.
        raise BindingRefusal(f"exact-ID claim failed for {bead_id}: {exc}")
    return ("spawn", None)


def after_launch(run, node, result):
    """Runner calls this once a spawn settles (inside the spawn lock):
    settle the launch intent, run the independent verifier, and — only on
    accept — the causal authorized closure. Returns a node record dict the
    runner MUST re-commit (demotion), or None to keep its own verdict."""
    g, _ = _grant(run)
    node_id = node["id"]
    task = node_task(node)
    attempt = max(_next_attempt(run, node_id) - 1, 1)
    _settle_intent(_intent_path(run, node_id, attempt),
                   "settled" if result.get("status") in ("done", "partial")
                   else "settled_failed",
                   result_class=result.get("error_class"))
    if task is None:
        return None
    bead_id, goal_attempt, artifact, worker = task

    verdict = _verify(g=g, bead_id=bead_id, goal_attempt=goal_attempt,
                      artifact=artifact, worker=worker, result=result)
    if verdict["verdict"] != "accept":
        ledger_write(run, bead_id, {"state": "failed",
                                    "attempt": goal_attempt,
                                    "problems": verdict["problems"]})
        return {"status": "failed",
                "error": "verifier rejected: " + "; ".join(verdict["problems"]),
                "error_class": "beads_verifier_reject",
                "ms": result.get("ms", 0)}
    out = authorize_close(run, bead_id, goal_attempt,
                          {"verdict": "accept",
                           "artifacts": verdict["artifacts"]})
    if out["state"] != "closed_verified":
        return {"status": "failed",
                "error": f"closure not causally verified: {out['state']}",
                "error_class": "beads_closure_unverified",
                "ms": result.get("ms", 0)}
    return None


def _verify(*, g, bead_id, goal_attempt, artifact, worker, result):
    """INDEPENDENT verifier role: re-reads native truth; never trusts the
    worker's json block, a harvest, or any caller-provided verdict."""
    store, bd_bin = g["store"], g["bd_bin"]
    problems = []
    row = read_model.show(store, bead_id, bd_bin=bd_bin)
    if not row:
        problems.append("bead missing natively")
    elif row.get("assignee") != worker:
        problems.append(f"holder {row.get('assignee')!r} != grant worker")
    obs, hits = _observe_effect(store, bd_bin, bead_id, goal_attempt, worker)
    if obs != "effect":
        problems.append("worker EVIDENCE envelope absent for this attempt")
    artifacts = _envelope_artifacts(hits[-1].get("text", "")) if hits else []
    if not artifacts:
        problems.append("evidence envelope cites no artifacts")
    if artifact and not os.path.exists(artifact):
        problems.append(f"cited artifact file missing on disk: {artifact}")
    out = result or {}
    if out.get("status") not in ("done", "partial"):
        problems.append(f"runner status {out.get('status')!r}")
    child_out = out.get("output")
    if isinstance(child_out, dict) and child_out.get("worker_error"):
        problems.append(f"worker_error: {child_out['worker_error']}")
    if out.get("error_class") == "beads_hold":
        problems.append("spawn was held")
    return {"verdict": "accept" if not problems else "reject",
            "problems": problems, "artifacts": artifacts,
            "bead": bead_id, "attempt": goal_attempt,
            "verifier": g["verifier"]}


def verify_node(run, node, result):
    """Public verifier entry (verifier role): a verdict INPUT to
    authorize_close, never an authority by itself."""
    g, _ = _grant(run)
    task = node_task(node)
    if task is None:
        raise BindingRefusal("verify_node needs a RUNNER-TASK node")
    bead_id, goal_attempt, artifact, worker = task
    return _verify(g=g, bead_id=bead_id, goal_attempt=goal_attempt,
                   artifact=artifact, worker=worker, result=result)


def _launch_nonce(run, bead_id, attempt):
    """The runner-held causal nonce for this bead's launch intent (R3):
    random per launch, recorded BEFORE the claim, known only to this run.
    None if this run never durably launched this bead/attempt — an external
    actor cannot mint it, so a close reason carrying it is proof of runner
    causality, not a guessable string."""
    idir = _run_paths(run)["intents"]
    found = None
    if os.path.isdir(idir):
        for fn in os.listdir(idir):
            rec = _jload(os.path.join(idir, fn)) or {}
            if (rec.get("operation") == "launch"
                    and rec.get("bead") == bead_id
                    and str(rec.get("attempt")) == str(attempt)
                    and rec.get("nonce")):
                if found is None or rec.get("at", 0) > found.get("at", 0):
                    found = rec
    return (found or {}).get("nonce")


def authorize_close(run, bead_id, attempt, verifier_result, *, bd_bin=None):
    """CLOSE gate: a provided verdict is an INPUT, re-checked here (a
    rejected verdict or evidence-less close NEVER advances the successor).
    The only close argv path is evidence.authorized_close — evidenced,
    artifact-citing, causally tokened (runner-held nonce), read-backed."""
    g, _ = _grant(run)
    bd_bin = bd_bin or g["bd_bin"]
    if not isinstance(verifier_result, dict) or \
            verifier_result.get("verdict") != "accept":
        ledger_write(run, bead_id,
                     {"state": "rejected", "attempt": attempt,
                      "problems": (verifier_result or {}).get(
                          "problems", ["no verdict"])})
        return {"closed": False, "state": "rejected"}
    nonce = _launch_nonce(run, bead_id, attempt)
    causal_token = f"attempt={attempt} verifier={g['verifier']}"
    row = read_model.show(g["store"], bead_id, bd_bin=bd_bin)
    if row and row.get("status") == "closed":
        reason = row.get("close_reason") or ""
        # Causality (R3): the exact verifier actor from the grant must be
        # the closer (read-back assignee), AND the reason must carry the
        # runner-held nonce VERBATIM — a replayable substring alone is
        # never credited (P4). No runner launch intent => no causality.
        causal = bool(nonce) and causal_token in reason \
            and f"nonce={nonce}" in reason \
            and (row.get("assignee") or "") == g["verifier"]
        ledger_write(run, bead_id,
                     {"state": "closed_verified" if causal
                      else "external_closed_unknown",
                      "attempt": attempt, "reason": reason,
                      "closed_at": row.get("closed_at")})
        return {"closed": bool(causal),
                "state": "closed_verified" if causal
                else "external_closed_unknown"}
    artifacts = verifier_result.get("artifacts") or []
    if not artifacts:
        ledger_write(run, bead_id, {"state": "rejected", "attempt": attempt,
                                    "problems": ["no artifacts cited"]})
        return {"closed": False, "state": "rejected"}
    if not nonce:
        # No durable launch intent this run wrote for this bead/attempt:
        # nothing this run launched can be causally closed by it.
        ledger_write(run, bead_id,
                     {"state": "rejected", "attempt": attempt,
                      "problems": ["no durable launch intent (nonce) for "
                                   "this bead/attempt — closure would be "
                                   "uncalable"]})
        return {"closed": False, "state": "rejected"}
    reason = (f"workflow admitted close {bead_id} {causal_token} "
              f"nonce={nonce} "
              f"run={os.path.basename(g['store'])} "
              f"artifacts={';'.join(artifacts)}")
    receipt = _jload(_run_paths(run)["receipt"], {}) or {}
    iid = before_native_effect(run, bead_id, attempt, "close",
                               {"reason": reason})
    # C3 read window BEFORE the close attempt: an unexpected holder or a
    # status moved by anyone else means an independent mutator touched the
    # bead — refuse before effects, suspend (never steal, never fake CAS).
    pre = read_model.show(g["store"], bead_id, bd_bin=bd_bin) or {}
    if pre.get("assignee") not in (None, "", g["worker"], g["verifier"]):
        ledger_write(run, bead_id,
                     {"state": "quarantined", "attempt": attempt,
                      "reason": f"independent mutator holds {bead_id} "
                                f"({pre.get('assignee')!r}) at close window"})
        return {"closed": False, "state": "quarantined", "intent": iid}
    try:
        out = evidence.authorized_close(
            g["store"], bead_id, actor=g["verifier"],
            authorization=f"admitted-grant "
                          f"{receipt.get('grant_digest', 'none')}",
            reason=reason, evidence_actor=g["worker"], attempt=attempt,
            artifacts=artifacts, bd_bin=bd_bin)
    except (evidence.ClosureRefusedError, evidence.ClosureAmbiguityError,
            native.NativeError) as exc:
        # Whether the close landed is uncertain: keep the intent unsettled,
        # quarantine (or mark externally-closed if it plainly landed),
        # never blind-retry.
        post = read_model.show(g["store"], bead_id, bd_bin=bd_bin) or {}
        if post.get("status") == "closed":
            ledger_write(run, bead_id,
                         {"state": "external_closed_unknown",
                          "attempt": attempt,
                          "reason": "close landed without full readback "
                                    f"proof: {post.get('close_reason')!r}"})
            state = "external_closed_unknown"
        else:
            ledger_write(run, bead_id,
                         {"state": "quarantined", "attempt": attempt,
                          "reason": f"close uncertain: {exc}"})
            state = "quarantined"
        return {"closed": False, "state": state, "intent": iid,
                "error": str(exc)}
    _settle_intent(_intent_path(run, f"{bead_id}-close", attempt), "settled")
    rec = {"state": "closed_verified", "attempt": attempt,
           "reason": reason,
           "closed_at": (out.get("row") or {}).get("closed_at"),
           "verifier": g["verifier"], "intent": iid,
           "readback_verified": out.get("readback_verified")}
    ledger_write(run, bead_id, rec)
    return {"closed": True, "state": "closed_verified", "detail": out}


# ---------- stop / recovery (host bootstrap is an acknowledged gap) --------

def stop_work(run, *, authenticated_context):
    """Authenticated stop: latch BEFORE any spawn can start. Child death is
    the stock runner's stop watcher; this adds the binding-side latch so a
    re-drive cannot resurrect work past the stop boundary."""
    _verify_credential(authenticated_context,
                       {"action": "stop", "run": str(run)})
    _, paths = _grant(run)
    _atomic_write(os.path.join(paths["base"], "STOP.json"),
                  {"at": time.time(),
                   "principal": authenticated_context["principal"]})
    open(os.path.join(paths["base"], "STOP"), "w").close()
    open(os.path.join(paths["run"], "stop.request"), "w").close()
    return {"ok": True,
            "state": "stopping_unknown until the runner proves terminal "
                     "children/effects"}


def reconcile_run(run):
    """Startup / runner-only-loss reconcile (no tab, no chat, no reminder):
    settle every unsettled NON-launch intent by NATIVE OBSERVATION. An
    observed real effect this run did not authorize quarantines (never
    replays blindly). Unsettled LAUNCH intents are deliberately left for
    before_launch's observation law at the next spawn — replay-free
    completion or effect-safe redispatch inside the grant budget."""
    paths = _run_paths(run)
    g = _jload(paths["grant"])
    if not g:
        return {"reconciled": [], "skipped": "no grant (stock run)"}
    idir = paths["intents"]
    if not os.path.isdir(idir):
        return {"reconciled": [], "skipped": "no intents"}
    events = []
    for fn in sorted(os.listdir(idir)):
        p = os.path.join(idir, fn)
        rec = _jload(p)
        if not rec or rec.get("state") != "intent":
            continue
        if rec.get("operation") == "launch":
            events.append({"intent": rec.get("intent"),
                           "action": "launch intent left for the spawn gate"})
            continue
        bead, attempt = rec.get("bead", ""), str(rec.get("attempt", ""))
        obs, _ = _observe_effect(g["store"], g["bd_bin"], bead, attempt,
                                 g["worker"])
        if obs == "effect" and (ledger_read(run, bead) or {}) \
                .get("state") != "closed_verified":
            ledger_write(run, bead,
                         {"state": "quarantined", "attempt": attempt,
                          "reason": "runner died between intent and "
                                    "settlement on a real effect"})
            events.append({"intent": rec.get("intent"),
                           "action": "quarantined"})
        _settle_intent(p, "settled_reconciled")
        events.append({"intent": rec.get("intent"),
                       "action": "settled by observation"})
    return {"reconciled": events}


def resolve_quarantine(run, bead_id, *, decision, authenticated_context):
    """Adjudication of a quarantined effect — explicit, authenticated,
    never silent."""
    _verify_credential(authenticated_context)
    led = ledger_read(run, bead_id)
    if not led or led.get("state") != "quarantined":
        raise BindingRefusal(f"{bead_id} is not quarantined")
    if decision not in ("accept_effect", "retry"):
        raise BindingRefusal(f"unknown adjudication {decision!r}")
    state = "blocked" if decision == "accept_effect" else "failed"
    ledger_write(run, bead_id,
                 {"state": state, "attempt": led.get("attempt"),
                  "reason": f"adjudicated {decision} by "
                            f"{authenticated_context['principal']}"})
    return {"bead": bead_id, "state": state}
