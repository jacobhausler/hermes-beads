"""Disposable-store bootstrap for tests/test_e2e.py (hbl-pnu.4.1).

Every helper here drives the ACTUAL pinned bd binary (v1.3.0, f45b249ce) with
fixed argv — no mocks, no SQL, no shadow store, no --force, no Beads
memory/mail/formulas. Each run owns a uniquely-named store under this
directory (gitignored via the worktree's .git/info/exclude; the committed
tree carries only this bootstrap), and each fixture store gets its own
`git init` so the embedded-dolt home never falls through to the lab repo's
databases — the recorded trap.

Seeding (bd create / bd dep add) is the seeder's act on a disposable store.
The planning store is read-only to this lane and is NEVER touched: these
helpers build only their own throwaway worlds. Closure inside the loop under
test goes through evidence.authorized_close (fixture closure is explicitly
permitted); the native raw-close used by the negative fixtures is a deliberate
native-behavior probe, never the plugin's closure surface.
"""
import json
import os
import shutil
import subprocess
import tempfile
import uuid

BD_BIN = os.environ.get("BEADS_LAB_BD",
                        "/home/hermes/.hermes/work/beads-lab/bin/bd")
FIXTURE_ROOT = os.path.dirname(os.path.abspath(__file__))

# hbl-pnu.4.7: the FIXTURE_ROOT is SHARED by every suite that seeds through
# this bootstrap (e2e, scenarios, mounted smoke). A runner must delete ONLY
# the stores its own process created — sweeping the root kills concurrent
# suites' live stores mid-run (the recorded -C flake).
RUN_STORES = set()


def cleanup_run_stores():
    """Remove exactly the stores THIS process created; never touch a
    foreign dir in the shared fixture root."""
    for d in list(RUN_STORES):
        shutil.rmtree(d, ignore_errors=True)
        RUN_STORES.discard(d)


def make_store(prefix="e2e"):
    """Fresh disposable store: unique dir under this fixture root, own
    git repo, own bd store (durable-in-run, deleted by the runner via
    cleanup_run_stores())."""
    os.makedirs(FIXTURE_ROOT, exist_ok=True)
    d = tempfile.mkdtemp(dir=FIXTURE_ROOT, prefix=f"{prefix}-")
    RUN_STORES.add(d)
    subprocess.run(["git", "init", "-q", "."], cwd=d, check=True,
                   capture_output=True)
    p = subprocess.run([BD_BIN, "init", "--prefix", prefix], cwd=d,
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    return d


def actor(prefix):
    """Unique actor per run/attempt (lab convention: same-actor idempotence
    is not two-worker exclusion)."""
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _one(stdout):
    rows = json.loads(stdout)
    return (rows[0] if isinstance(rows, list) else rows)


def seed_bead(store, title, *, labels=("impl",), description=None,
              actor_name="lab-seeder", deps=()):
    """Create one bead with the given labels; optional blocking prereqs
    (`bd dep add <new> <prereq>` — prereq blocks the new bead, type=blocks).
    Returns the exact created ID (native identity, never re-minted)."""
    argv = [BD_BIN, "-C", store, "--actor", actor_name, "create", title,
            "--json"]
    if description is not None:
        argv += ["--description", description]
    if labels:
        argv += ["--labels", ",".join(labels)]
    p = subprocess.run(argv, capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    iid = _one(p.stdout)["id"]
    for prereq in deps:
        p = subprocess.run([BD_BIN, "-C", store, "--actor", actor_name,
                            "dep", "add", iid, prereq, "--json"],
                           capture_output=True, text=True)
        assert p.returncode == 0, p.stderr
    return iid


def raw_bd(store, *argv, actor_name="probe", readonly=False, check=False):
    """Raw fixed-argv native probe (read-backs and the negative-control
    native close). Returns the CompletedProcess — nonzero is a RESULT."""
    full = [BD_BIN, "-C", store]
    if readonly:
        full.append("--readonly")
    full += ["--actor", actor_name] + list(argv)
    p = subprocess.run(full, capture_output=True, text=True, check=check)
    return p


def show_dict(store, iid):
    p = raw_bd(store, "show", iid, "--json", readonly=True)
    assert p.returncode == 0, p.stderr
    rows = json.loads(p.stdout)
    return (rows[0] if rows else None)


def comments_list(store, iid):
    p = raw_bd(store, "comments", iid, "--json", readonly=True)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


def raw_ready(store, *, label=None, exclude_epics=True):
    """`bd ready --json` (optionally --exclude-type=epic / --label), parsed
    to ID list."""
    argv = ["ready", "--json", "-n", "100"]
    if exclude_epics:
        argv += ["--exclude-type=epic"]
    if label:
        argv += ["--label", label]
    p = raw_bd(store, *argv, readonly=True)
    assert p.returncode == 0, p.stderr
    return [r["id"] for r in json.loads(p.stdout)]
