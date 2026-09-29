"""hbl-pnu.3.3 targeted runner hooks — injected into the ISOLATED Workflow
checkout only (tests/fixtures/runner-runtime/workflow-source/), never the
installed plugin (a test hashes the installed tree before/after).

Stock runner stays the ONE scheduler: these hooks run inside its existing
spawn lock and replay-skip law; they never Popen, never flock the runner
lock, never schedule. Everything degrades inert: if runner_binding is not
importable, or the run has no admitted grant (beads/grant.json), the runner
keeps byte-identical stock behavior (native plugin remains usable without
the Workflow/runner stack — spec 'Workflow missing leaves native plugin
usable').

Injection points in wf.py (exact anchors, fail-closed if absent):
  run_child: under meta['_procs_lock'], before the spawn-check/Popen
    section, this hook's gate() decides: refusal -> typed beads_hold
    failure record (never dispatched), completion -> replay-free finish of
    an observed prior effect, spawn -> stock path; after the spawn settles,
    gate_after() demotes done->failed unless the verifier authorized a
    causal closure readback.
"""
import time


def _mod():
    try:
        import runner_binding
        return runner_binding
    except ImportError:
        return None


def active_child(b, run, node, byid, index=None):
    """Adoption-first recovery BEFORE any admission decision: a verified
    live child (running record + efp match + pid alive + skey-in-cmdline)
    is never killed or re-spawned by the binding."""
    try:
        from wfcommon import active_child as ac
    except ImportError:
        return None
    try:
        return ac(run, node, byid, index)
    except Exception:
        return None


def gate(b, run, meta, node, index, skey):
    """Pre-Popen admission decision. Returns None (stock spawn), a failure
    dict (hold — runner commits it, zero spawns), or a completion dict
    (replay-free finish of an observed prior effect)."""
    try:
        action, payload = b.before_launch(run, node, skey, [])
    except Exception as exc:                      # hold closed, reason kept
        return {"status": "failed",
                "error": f"beads hold: {exc}",
                "error_class": "beads_hold", "ms": 0, "spawn": 0,
                "attempts": 0}
    if action == "spawn":
        return None
    if action == "complete":
        return {"status": "done", "output": payload, "ms": 0,
                "recovered": True, "spawn": 0, "attempts": 0}
    return {"status": "failed", "error": f"beads hold: unknown action "
            f"{action!r}", "error_class": "beads_hold", "ms": 0,
            "spawn": 0, "attempts": 0}


def gate_after(b, run, node, r):
    """Commit gate: verifier re-check + causal authorized closure. Returns
    the (possibly demoted) node result the runner must commit."""
    try:
        demotion = b.after_launch(run, node, r)
    except Exception as exc:
        return {"status": "failed",
                "error": f"beads closure gate crashed: {exc}",
                "error_class": "beads_closure_unverified",
                "ms": (r or {}).get("ms", 0)}
    return demotion if demotion is not None else r


def install(wf_path, lane_root):
    """Patch the isolated checkout's wf.py at the exact anchors. Raises if
    any anchor moved (fail-closed — never patch blind)."""
    with open(wf_path, encoding="utf-8") as f:
        src = f.read()
    if "hbl-pnu.3.3 beads runner hooks" in src:
        return "already-patched"

    a1 = ("            proc = subprocess.Popen(cmd, stdout=logf, "
          "stderr=subprocess.STDOUT,")
    a2 = ("            save_node(run, node, byid, r)\n"
          "            if r[\"status\"] in (\"done\", \"partial\"):")
    a3 = "def run_agent_node(run, meta, byid, node, outputs, steering):"
    for a in (a1, a2, a3):
        if src.count(a) != 1:
            raise RuntimeError(
                f"runner anchor not unique/found — refusing to patch: "
                f"{a[:60]!r} (count={src.count(a)})")

    prelude = f'''

# ---- hbl-pnu.3.3 beads runner hooks (injected; isolated checkout only) ----
import sys as _bsys
if {str(lane_root)!r} not in _bsys.path:
    _bsys.path.insert(0, {str(lane_root)!r})
import runner_binding as _b
import runner_hooks as _bh


def _beads_granted(run):
    try:
        return _b.granted(run)
    except Exception:
        return False


def _beads_child(meta, node, byid, index):
    """Stock solo spawn path with the binding gate under the same spawn
    lock; returns a result dict or None to fall through to stock code.
    R1: a VERIFIED live orphan (wfcommon.active_child: running record +
    efp match + pid alive + skey-in-cmdline) is ADOPTED here via the
    runner's own _adopt_child — the solo path must NEVER fall through to a
    second Popen on top of live work. (Stock wf.py only calls active_child
    in the fanout branch; without this the binding would double-spawn.)"""
    run = meta["_run"]
    if not _beads_granted(run):
        return None
    child = _bh.active_child(_b, run, node, byid, index)
    if child is not None:
        # adopt OUTSIDE the procs lock — _adopt_child takes it itself
        memo_key = str(node["id"]) + ":" + str(index) + ":" + str(child["pid"])
        memo = (meta.get("_adopt_result") or {{}}).get(memo_key)
        if memo is not None:
            return {{**memo, **_profile_evidence(node)}}   # already harvested
        return {{**_adopt_child(meta, node, byid, index, child,
                                node.get("schema")),
                **_profile_evidence(node)}}
    with meta["_procs_lock"]:
        if meta["_stop"].is_set():
            return {{"status": "failed", "error": "cancelled before spawn",
                     "error_class": "cancelled", "ms": 0}}
        solo = next((k for k in meta["_procs"] if k.startswith(
            str(node["id"]) + ":")), None)
        if solo is not None:
            return None                      # live child already registered
        skey = meta.get("_beads_skey") or "beads:" + str(node["id"])
        decision = _bh.gate(_b, run, meta, node, index, skey)
        if decision is not None:
            return decision
        meta["_beads_skey"] = skey           # skey fixed for this spawn
    return None


def _beads_settle(run, node, r):
    if not _beads_granted(run):
        return r
    return _bh.gate_after(_b, run, node, r)

'''

    # prelude after the last top-level import block: insert before the first
    # def that follows (run_agent_node anchor a3's preceding helper boundary)
    src = src.replace(a3, prelude + "\n" + a3, 1)

    # 1) pre-Popen gate inside the spawn lock (before stock check→Popen).
    #    R2: the admission credential NEVER rides into a child env — the
    #    child has no admission authority; the host holds the secret alone.
    src = src.replace(
        "        proc = None\n        with meta[\"_procs_lock\"]:",
        "        proc = None\n"
        "        env.pop(\"BEADS_ADMISSION_CREDENTIAL_FILE\", None)\n"
        "        if not node.get(\"fanout\"):\n"
        "            _bd = _beads_child(meta, node, byid, index)\n"
        "            if _bd is not None:\n"
        "                try: logf.close()\n"
        "                except Exception: pass\n"
        "                return _bd\n"
        "        with meta[\"_procs_lock\"]:", 1)

    # 2) commit gate at the solo save_node site
    src = src.replace(a2, (
        "            r = _beads_settle(run, node, r)\n"
        "            save_node(run, node, byid, r)\n"
        "            if r[\"status\"] in (\"done\", \"partial\"):"), 1)

    # 3) reconcile at startup: runner-only-loss / restart no-reminder wake
    #    (bootstrap: the caller re-invokes `wf.py run` — acknowledged local
    #    bootstrap gap; the reconcile itself is the binding's observation law)
    src = src.replace(
        "    threading.Thread(target=_stop_watcher, daemon=True).start()",
        "    threading.Thread(target=_stop_watcher, daemon=True).start()\n"
        "    if _beads_granted(run):\n"
        "        try:\n"
        "            _b.reconcile_run(run)\n"
        "        except Exception as _e:\n"
        "            log(run, \"beads.reconcile.error\",\n"
        "                error=f\"{type(_e).__name__}: {_e}\")", 1)

    with open(wf_path, "w", encoding="utf-8") as f:
        f.write(src)
    return "patched"
