#!/usr/bin/env python3
"""Host bridge CLI (hbl-pnu.3.7): the Work/Cancel host handlers for the
mounted smoke — and only that. The shipped desktop components stay
presentation-only (zero I/O); ALL door I/O lives here, exactly where the
real app loader would keep it.

Subcommands (all print ONE JSON object on stdout):

  click  --store S --bead B --key K [--goal-prefix P] [--node-timeout N]
               [--wall-deadline S]
      bind the REAL work_door.make_work_door (same args as
      tests/test_work_half.py: run_base/workflow_src under the gitignored
      tests/.work-bridge home, hermes_bin = the committed fake-hermes
      fixture) onto bot_handoff and call bot_handoff.run_work.
  state  --store S --bead B --key K
      bot_handoff.work_run_state — the door's verbatim state vocabulary.
  cancel --store S --bead B --key K
      bot_handoff.work_cancel — truthful two-phase (cancel_requested while
      the runner is alive; cancelled only after confirmed terminal).

Credential law: the admission credential is provisioned with
runner_binding.provision_credential at HERMES_HOME/beads/
admission-credential.json, HERMES_HOME coming from the HOST environment
(the smoke points it at a disposable dir under tests/, gitignored). No
env-credential path is ever consulted: BEADS_ADMISSION_CREDENTIAL_FILE is
removed from the environment on arrival, so the fixed-path law cannot be
sidestepped even by a stray export.

No new door logic lives here: make_work_door / run_work / work_run_state /
work_cancel are reused exactly as tests/test_work_half.py drives them.

Run:  python3 work_bridge.py <verb> --store S --bead B --key K
"""
import argparse
import importlib
import json
import os
import pathlib
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
_TESTS = os.path.join(HERE, "tests")
sys.path.insert(0, _TESTS)

# The host's HERMES_HOME is config, chosen by the caller (the mounted smoke
# points it under tests/.work-bridge). It is never a credential POINTER —
# only the fixed relative path below is consulted.
os.environ.pop("BEADS_ADMISSION_CREDENTIAL_FILE", None)
_CALLER_HERMES_HOME = os.environ.get("HERMES_HOME")

BRIDGE_HOME = pathlib.Path(HERE) / "tests" / ".work-bridge"
RUNS_BASE = BRIDGE_HOME / "runs"

import runner_hooks                # noqa: E402
import bot_handoff                 # noqa: E402
import work_door                   # noqa: E402
import runner_binding as rb        # noqa: E402

# Fixture reuse (BD, FAKE_HERMES, INSTALLED) from the 3.3 suite. That module
# sets HERMES_HOME at import to ITS own host home — the caller's host config
# is restored immediately so credential_path() stays wherever the HOST
# pointed it, never where a fixture import wanted it.
trb = importlib.import_module("test_runner_binding")
os.environ["HERMES_HOME"] = _CALLER_HERMES_HOME or ""


def _emit(obj):
    print(json.dumps(obj, sort_keys=False))


def _fail(verb, exc):
    _emit({"ok": False, "verb": verb, "error": type(exc).__name__,
           "reason": str(exc)})
    sys.exit(2)


def _ensure_checkout(fresh=False):
    """Same law as test_work_half.setUpClass: an isolated PATCHED copy of
    the stock runner under the gitignored bridge home; the installed tree
    is only ever read."""
    wf_src = BRIDGE_HOME / "workflow-source"
    if fresh:
        # fresh isolated patched checkout per click (same law as
        # test_work_half.setUpClass): a stale/mangled copy can never shadow
        # this click. state/cancel must NOT rewrite it while a runner is
        # mid-run, so they only ensure it exists.
        if wf_src.exists():
            shutil.rmtree(wf_src)
    wf_src.mkdir(parents=True, exist_ok=True)
    for f in ("wf.py", "wfcommon.py"):
        dst = wf_src / f
        if not dst.exists():
            shutil.copy2(trb.INSTALLED / f, dst)
    runner_hooks.install(wf_src / "wf.py", HERE)
    RUNS_BASE.mkdir(parents=True, exist_ok=True)
    return wf_src


def _provision(store, bead):
    """Host-side provisioning at the ONE credential path
    (runner_binding.credential_path() under the caller's HERMES_HOME), via
    runner_binding.provision_credential — random secret, approved scope =
    exactly this bead, store-bound. A pre-provisioned credential for the
    same (store, bead) is reused (state/cancel must verify against the
    SAME secret that admitted the run)."""
    path = rb.credential_path()
    if os.path.lexists(path):
        cred, problem = rb._read_host_credential(path)
        if not problem and isinstance(cred, dict) \
                and cred.get("store") == os.path.realpath(str(store)) \
                and bead in (cred.get("approved_beads") or []):
            return cred["principal"]
    return rb.provision_credential("lab-host-bridge", [bead], store,
                                   rotate=True)


def _bind(args, fresh=False):
    wf_src = _ensure_checkout(fresh=fresh)
    _provision(args.store, args.bead)
    door = work_door.make_work_door(
        store=str(args.store), bd_bin=trb.BD,
        run_base=str(RUNS_BASE), workflow_src=str(wf_src),
        hermes_bin=str(trb.FAKE_HERMES),
        node_timeout=args.node_timeout,
        wall_deadline_s=args.wall_deadline,
        goal_prefix=args.goal_prefix)
    bot_handoff.bind_runner_door(door)
    return door


def main(argv):
    ap = argparse.ArgumentParser(prog="work_bridge.py")
    ap.add_argument("verb", choices=("click", "state", "cancel"))
    ap.add_argument("--store", required=True)
    ap.add_argument("--bead", required=True)
    ap.add_argument("--key", required=True)
    ap.add_argument("--goal-prefix", default="")
    ap.add_argument("--node-timeout", type=int, default=60)
    ap.add_argument("--wall-deadline", type=float, default=None)
    ap.add_argument("--actor", default="w-bridge")
    args = ap.parse_args(argv)
    if not _CALLER_HERMES_HOME:
        _emit({"ok": False, "verb": args.verb,
               "error": "host_home_missing",
               "reason": "the host bridge needs the host's HERMES_HOME set "
                         "to its own state dir (never a credential env "
                         "pointer)"})
        sys.exit(2)
    try:
        # A click rebuilds the patched checkout fresh (rmtree+re-copy) so a
        # stale/mangled copy can never shadow this click; state/cancel only
        # ensure it exists — never rewrite wf.py mid-run.
        door = _bind(args, fresh=(args.verb == "click"))
        if args.verb == "click":
            out = bot_handoff.run_work(str(args.store), args.bead,
                                       actor=args.actor, bd_bin=trb.BD,
                                       request_key=args.key)
        elif args.verb == "state":
            out = bot_handoff.work_run_state(args.key)
        else:
            out = bot_handoff.work_cancel(args.key)
        out = dict(out)
        out.setdefault("ok", False)
        out["verb"] = args.verb
        _emit(out)
    except SystemExit:
        raise
    except Exception as exc:
        _fail(args.verb, exc)


if __name__ == "__main__":
    main(sys.argv[1:])
