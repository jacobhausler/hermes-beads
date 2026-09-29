"""Disposable-store bootstrap for tests/test_bot_handoff.py (hbl-pnu.3.5).

Reuses the accepted e2e bootstrap's path-independent helpers (real pinned bd
v1.3.0, fixed argv) but owns its fixture root: stores land under
tests/fixtures/handoff-runtime (gitignored), unique per run, each with its
own git init — the recorded embedded-dolt fall-through trap. Same laws: no
mocks, no SQL, no --force, no shadow store, no Beads
memory/mail/formulas; the planning store is never touched.
"""
import os
import subprocess
import sys
import tempfile

_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_ROOT), "e2e-runtime"))

import bootstrap as _bootstrap  # noqa: E402  (accepted; reuse, do not fork)

BD_BIN = _bootstrap.BD_BIN
FIXTURE_ROOT = os.path.join(_ROOT, "handoff-runtime")


def make_store(prefix="handoff"):
    """Fresh disposable store under OUR fixture root, own git repo."""
    os.makedirs(FIXTURE_ROOT, exist_ok=True)
    d = tempfile.mkdtemp(dir=FIXTURE_ROOT, prefix=f"{prefix}-")
    subprocess.run(["git", "init", "-q", "."], cwd=d, check=True,
                   capture_output=True)
    p = subprocess.run([BD_BIN, "init", "--prefix", prefix], cwd=d,
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    return d


actor = _bootstrap.actor
seed_bead = _bootstrap.seed_bead
raw_bd = _bootstrap.raw_bd
show_dict = _bootstrap.show_dict
