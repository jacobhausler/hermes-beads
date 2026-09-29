#!/usr/bin/env python3
"""Negative control suite (hbl-pnu.4.2): refusal honesty + no-lost-update discipline.

Every case asserts (a) the exact process exit code, (b) the exact --json
envelope shape, and (c) that the native business fields of the bead are
UNCHANGED on refusal (canonical-field read-back comparison per the owner
contract — volatile bookkeeping fields updated_at/lease_expires_at/
heartbeat_at are excluded; byte-identity of the raw `bd show --json` stdout
is additionally asserted where the refusal writes nothing at all).

Receipt semantics replicate (do not import) the fixture patterns of
tests/test_native.py and tests/test_write_protocol.py: the ACTUAL pinned bd
(1.3.0, f45b249ce) runs against fresh disposable stores under
tests/fixtures/negative-runtime/, each with its own `git init` so the
embedded-dolt home never falls through to the lab repo's databases. The
planning store is NEVER touched here. Unique actor per run/attempt.

Threat-model discipline (CONTRACTS-v3): the worker-closure refusal and the
authorized-close preconditions are TRUSTED-AGENT POLICY at the plugin
surface — cooperative, bypassable via the native CLI, and tested as such.
They are NOT hostile-isolation/ACL claims; test_policy_is_policy below proves
the native escape hatch stays open. This suite qualifies NO concurrent-writer
behaviour and NO CAS (no --if-revision exists); the declared-topology gate
test states that explicitly.

Run: python3 tests/test_negative.py   (failures exit nonzero)
"""
import ast
import json
import os
import subprocess
import sys
import tempfile
import unittest
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import native            # noqa: E402
import claims            # noqa: E402
import write_protocol    # noqa: E402
import evidence          # noqa: E402

BD_BIN = os.environ.get("BEADS_LAB_BD",
                        "/home/hermes/.hermes/work/beads-lab/bin/bd")
FIXTURE_ROOT = os.path.join(HERE, "fixtures", "negative-runtime")
DOCS = os.path.join(os.path.dirname(HERE), "docs", "negative-qualification.md")

VOLATILE = {"updated_at", "lease_expires_at", "heartbeat_at"}

READONLY_MSG = "Error: operation '{op}' is not allowed in read-only mode"


def make_store(prefix="neg"):
    d = tempfile.mkdtemp(dir=FIXTURE_ROOT)
    subprocess.run(["git", "init", "-q", "."], cwd=d, check=True,
                   capture_output=True)
    p = subprocess.run([BD_BIN, "init", "--prefix", prefix], cwd=d,
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    return d


def actor(prefix="w"):
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def raw(store, args, readonly=False):
    argv = [BD_BIN]
    if readonly:
        argv.append("--readonly")
    argv += args
    return subprocess.run(argv, cwd=store, capture_output=True, text=True)


def create(store, title, description="d"):
    p = subprocess.run([BD_BIN, "-C", store, "create", title,
                        "--description", description, "--json"],
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    rows = json.loads(p.stdout)
    return (rows[0] if isinstance(rows, list) else rows)["id"]


def show_raw(store, iid):
    """Raw `bd show --json` stdout bytes (byte-identity comparisons)."""
    p = subprocess.run([BD_BIN, "-C", store, "--readonly", "show", iid,
                        "--json"], capture_output=True)
    assert p.returncode == 0, p.stderr
    return p.stdout


def show_dict(store, iid):
    return json.loads(show_raw(store, iid))[0]


def business(row):
    """Canonical business fields only: volatile bookkeeping excluded
    (owner contract — unchanged business fields, not byte identity)."""
    return {k: v for k, v in row.items() if k not in VOLATILE}


class ArgvAudit:
    """Capture the ACTUAL argv lists native.run_bd hands to subprocess.run.

    Swaps the `subprocess` attribute INSIDE the native module only (not the
    global module) and delegates to the real subprocess.run, so the audit
    sees exactly what would execute — never a mock short-circuit.
    """

    def __init__(self):
        self.argvs = []
        self._real_run = subprocess.run
        self._saved = None

        outer = self

        class _Shim:
            CalledProcessError = subprocess.CalledProcessError
            TimeoutExpired = subprocess.TimeoutExpired

            @staticmethod
            def run(*a, **k):
                argv = a[0] if a else k.get("args")
                outer.argvs.append(list(argv))
                return outer._real_run(*a, **k)

        self._shim = _Shim

    def __enter__(self):
        self._saved = native.subprocess
        native.subprocess = self._shim
        return self

    def __exit__(self, *exc):
        native.subprocess = self._saved
        return False


def envelope_of(exc):
    """Parse the JSON failure envelope an exception carried (bd prints prose
    then the JSON; find the first '{')."""
    for blob in (getattr(exc, "stdout", ""), getattr(exc, "stderr", "")):
        idx = (blob or "").find("{")
        if idx != -1:
            return json.loads(blob[idx:])
    return None


class ClaimConflictNamesHolder(unittest.TestCase):
    """Interop Q3/Q7 receipt shape: refused claim = exit 1 + failed[] naming
    the holder; ownership never silently moves; business fields unchanged."""

    def setUp(self):
        self.store = make_store()
        self.iid = create(self.store, "contended")
        self.owner = actor("owner")
        self.thief = actor("thief")
        claims.claim(self.store, self.iid, actor=self.owner, bd_bin=BD_BIN)
        self.before = show_dict(self.store, self.iid)

    def test_native_refusal_exit1_failed_names_holder(self):
        with self.assertRaises(native.BdCommandError) as cm:
            native.run_bd(["update", self.iid, "--claim", "--json"],
                          workspace=self.store, bd_bin=BD_BIN,
                          actor=self.thief)
        err = cm.exception
        self.assertEqual(err.exit_code, 1)
        env = envelope_of(err)
        self.assertIsNotNone(env, "failed[] envelope required")
        self.assertEqual(env["error"], "1 of 1 issues failed to update")
        (entry,) = env["failed"]
        self.assertEqual(entry["id"], self.iid)
        self.assertEqual(
            entry["error"],
            f"updating issue: issue already claimed by {self.owner}")
        back = show_dict(self.store, self.iid)
        self.assertEqual(business(back), business(self.before),
                         "refusal must leave business fields unchanged")
        self.assertEqual(back["assignee"], self.owner)

    def test_claims_layer_discloses_holder_without_steal(self):
        with self.assertRaises(claims.ClaimConflictError) as cm:
            claims.claim(self.store, self.iid, actor=self.thief,
                         bd_bin=BD_BIN)
        self.assertEqual(cm.exception.holder, self.owner)
        back = show_dict(self.store, self.iid)
        self.assertEqual(back["assignee"], self.owner)
        self.assertEqual(business(back), business(self.before))


class GuardMismatchWritesNothing(unittest.TestCase):
    """Interop R1/U4 shape: stale guard = exit 13 + guard_mismatch:true,
    byte-identical bead, and exactly ONE update argv — never retried."""

    def setUp(self):
        self.store = make_store()
        self.iid = create(self.store, "guarded")
        self.before_raw = show_raw(self.store, self.iid)
        self.before = show_dict(self.store, self.iid)

    def test_raw_guard_mismatch_exit13_envelope(self):
        with self.assertRaises(native.GuardMismatchError) as cm:
            native.run_bd(["update", self.iid, "--if-status", "in_progress",
                           "--priority", "1", "--json"],
                          workspace=self.store, bd_bin=BD_BIN,
                          actor=actor())
        err = cm.exception
        self.assertEqual(err.exit_code, 13)
        env = envelope_of(err)
        (entry,) = env["failed"]
        self.assertTrue(entry["guard_mismatch"], "guard_mismatch:true required")
        self.assertEqual(
            entry["error"],
            'updating issue: status mismatch: '
            f'{self.iid} has status "open", expected "in_progress"')
        self.assertEqual(show_raw(self.store, self.iid), self.before_raw)

    def test_protocol_stale_single_argv_business_fields_unchanged(self):
        with ArgvAudit() as audit:
            with self.assertRaises(write_protocol.WriteStaleError) as cm:
                write_protocol.update_fields(
                    self.store, self.iid, actor=actor(), bd_bin=BD_BIN,
                    if_assignee="ghost-actor", if_status="open",
                    fields={"priority": "1"})
        err = cm.exception
        self.assertEqual(err.exit_code, 13)
        self.assertTrue(err.guard_mismatch)
        self.assertEqual(err.fresh["status"], "open")
        updates = [a for a in audit.argvs if "update" in a]
        self.assertEqual(len(updates), 1,
                         f"guard mismatch must never retry: {updates}")
        self.assertIn("--if-assignee", updates[0])
        self.assertIn("ghost-actor", updates[0])
        self.assertEqual(show_raw(self.store, self.iid), self.before_raw)
        self.assertEqual(business(show_dict(self.store, self.iid)),
                         business(self.before))
        self.assertEqual(show_dict(self.store, self.iid)["priority"], 2)


class ReadonlyBlocksWrites(unittest.TestCase):
    """Interop T3 shape: --readonly refuses writes with the exact error
    string, exit 1, and leaves the store untouched."""

    def setUp(self):
        self.store = make_store()
        self.iid = create(self.store, "seeded")
        self.before_raw = show_raw(self.store, self.iid)

    def test_raw_readonly_refuses_exact_strings(self):
        for op, args in (("create", ["create", "sneaky"]),
                         ("comment", ["comment", self.iid, "blocked"]),
                         ("comment add",
                          ["comments", "add", self.iid, "blocked"])):
            p = raw(self.store, ["--actor", actor()] + args, readonly=True)
            self.assertEqual(p.returncode, 1, f"{op}: {p.stderr}")
            self.assertEqual(
                p.stderr.strip(), READONLY_MSG.format(op=op),
                f"exact refusal string for {op!r}")
        self.assertEqual(show_raw(self.store, self.iid), self.before_raw)

    def test_native_boundary_readonly_argv_and_exit1(self):
        with ArgvAudit() as audit:
            with self.assertRaises(native.BdCommandError) as cm:
                native.run_bd(["comments", "add", self.iid, "sneaky"],
                              workspace=self.store, bd_bin=BD_BIN,
                              readonly=True, actor=actor(),
                              expect_json=False)
        self.assertEqual(cm.exception.exit_code, 1)
        (argv,) = audit.argvs
        self.assertEqual(argv[0], BD_BIN)
        self.assertEqual(argv[1], "--readonly",
                         "--readonly must be a global token before the verb")
        self.assertEqual(show_raw(self.store, self.iid), self.before_raw)

    def test_readonly_reads_still_work(self):
        # honest distinction: readonly blocks WRITES, reads stay green
        rows = native.ready_frontier(self.store, bd_bin=BD_BIN)
        self.assertEqual([r["id"] for r in rows], [self.iid])


class CircuitBreakerNotEmpty(unittest.TestCase):
    """exit-2 --max-rows surfaces as a named breaker error, never an empty
    selection dressed up as 'no results'."""

    def setUp(self):
        self.store = make_store()
        create(self.store, "one")
        create(self.store, "two")

    def test_max_rows_exit2_raises_breaker(self):
        with self.assertRaises(native.CircuitBreakerError) as cm:
            native.run_bd(["list", "--json", "--max-rows", "1"],
                          workspace=self.store, bd_bin=BD_BIN, readonly=True)
        self.assertEqual(cm.exception.exit_code, 2)
        # and the boundary never degrades it to []:
        try:
            native.run_bd(["list", "--json", "--max-rows", "1"],
                          workspace=self.store, bd_bin=BD_BIN, readonly=True)
            self.fail("exit 2 must raise, not return")
        except native.CircuitBreakerError as exc:
            self.assertNotEqual(exc.exit_code, 0)


class EmptyVsBackendFailure(unittest.TestCase):
    """Backend failure != empty selection, and label-scoped empty is
    distinguished from store-empty by a separate listing check."""

    def setUp(self):
        self.store = make_store()
        self.iid = create(self.store, "solo")

    def test_honest_empty_when_everything_claimed(self):
        claims.claim(self.store, self.iid, actor=actor("holder"),
                     bd_bin=BD_BIN)
        p = raw(self.store, ["ready", "--json", "--max-rows", "1000"])
        self.assertEqual(p.returncode, 0)
        self.assertEqual(p.stdout.strip(), "[]")
        self.assertEqual(native.ready_frontier(self.store, bd_bin=BD_BIN), [])

    def test_label_empty_vs_backend_empty_via_listing(self):
        # ready --label <none-match> is honestly [] ...
        rows = native.ready_frontier(self.store, bd_bin=BD_BIN,
                                     label="no-such-label")
        self.assertEqual(rows, [])
        # ... but a separate scoped listing proves the STORE is not empty:
        # the label-empty result is filter-scoped, not backend-empty.
        listing = raw(self.store, ["--readonly", "list", "--json", "-n", "10"])
        self.assertEqual(listing.returncode, 0)
        self.assertEqual([r["id"] for r in json.loads(listing.stdout)],
                         [self.iid])

    def test_backend_unavailable_is_named_error_not_empty(self):
        with self.assertRaises(native.BdNotFoundError) as cm:
            native.ready_frontier(self.store, bd_bin="/nonexistent/bd-bin")
        self.assertIn("/nonexistent/bd-bin", str(cm.exception))
        empty_dir = tempfile.mkdtemp(dir=FIXTURE_ROOT)
        with self.assertRaises(native.WorkspaceError):
            native.ready_frontier(empty_dir, bd_bin=BD_BIN)


class WorkerClosureImpossibleByArgv(unittest.TestCase):
    """close-on-cancel / close-on-worker is impossible BY AUDIT: the worker
    surface refuses closure and the actual constructed argv contains no
    close/update write — only the append-only REQUEST-CLOSURE comment."""

    def setUp(self):
        self.store = make_store()
        self.iid = create(self.store, "worker bead")
        self.worker = actor("worker")
        claims.claim(self.store, self.iid, actor=self.worker, bd_bin=BD_BIN)
        self.before = show_dict(self.store, self.iid)

    def test_request_closure_raises_and_argv_has_no_close(self):
        surf = evidence.WorkerSurface(self.store, actor=self.worker,
                                      bd_bin=BD_BIN)
        with ArgvAudit() as audit:
            with self.assertRaises(evidence.WorkerClosureRefusedError):
                surf.request_closure(self.iid, detail="cancel requested")
        for argv in audit.argvs:
            self.assertNotIn("close", argv,
                             f"worker surface constructed close argv: {argv}")
            self.assertNotIn("--force", argv)
        # the refusal is honest and durable: a REQUEST-CLOSURE comment landed
        comments = json.loads(raw(self.store, ["--readonly", "comments",
                                               self.iid, "--json"]).stdout)
        self.assertTrue(any(c["author"] == self.worker
                            and c["text"].startswith(evidence.REQUEST_PREFIX)
                            for c in comments), comments)
        back = show_dict(self.store, self.iid)
        self.assertEqual(back["status"], "in_progress")
        self.assertEqual(business(back)["status"], business(self.before)["status"])

    def test_worker_surface_has_no_close_or_reopen_method(self):
        surf = evidence.WorkerSurface(self.store, actor=self.worker,
                                      bd_bin=BD_BIN)
        for name in ("close", "reopen", "authorized_close",
                     "authorized_reopen", "force_close"):
            self.assertFalse(hasattr(surf, name),
                             f"worker surface must not expose {name}")

    def test_authorized_close_preconditions_make_zero_subprocess_calls(self):
        # every refused precondition stops BEFORE any bd subprocess runs:
        # no silent partial write, no probe side effect.
        kw = dict(bd_bin=BD_BIN, actor="parent-x", evidence_actor=self.worker,
                  attempt="a1", artifacts=["rep.md"])
        cases = [
            (dict(kw, authorization="", reason="r"), "authorization"),
            (dict(kw, authorization="auth", reason="  "), "reason"),
            (dict(kw, authorization="auth", reason="done",
                 artifacts=["rep.md"]), "cite"),
            (dict(kw, authorization="auth",
                  reason="verified rep.md",
                  artifacts=["missing-report.md"]), "evidence"),
        ]
        for kwargs, _label in cases:
            with ArgvAudit() as audit:
                with self.assertRaises(evidence.ClosureRefusedError):
                    evidence.authorized_close(self.store, self.iid, **kwargs)
            self.assertEqual(audit.argvs, [],
                             "precondition refusals must touch nothing")
        back = show_dict(self.store, self.iid)
        self.assertEqual(business(back), business(self.before))


class AstForceAudit(unittest.TestCase):
    """grep-audit acceptance: zero --force occurrences in plugin argv call
    sites, and close/reopen argv built ONLY in the authorized surface."""

    MODULES = ["native.py", "claims.py", "write_protocol.py", "evidence.py",
               "read_model.py", "correlation.py", "interop.py"]
    BANNED = ("--force", "--if-revision", "--if-content", "--cas")
    AUTHZ_FUNCS = {"authorized_close", "authorized_reopen"}

    def _call_sites(self):
        found = []  # (module, lineno, source-segment, resolvable tokens)
        for mod in self.MODULES:
            path = os.path.join(os.path.dirname(HERE), mod)
            with open(path) as fh:
                src = fh.read()
            tree = ast.parse(src)
            for node in ast.walk(tree):
                if (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and node.func.attr == "run_bd" and node.args):
                    seg = ast.get_source_segment(src, node.args[0]) or ""
                    toks = []
                    if isinstance(node.args[0], ast.List):
                        toks = [e.value for e in node.args[0].elts
                                if isinstance(e, ast.Constant)]
                    # enclosing function name, if any
                    found.append((mod, node.lineno, seg, toks, node))
        return found

    def test_zero_force_or_fake_cas_tokens_in_argv_call_sites(self):
        sites = self._call_sites()
        self.assertGreaterEqual(len(sites), 8,
                                "audit vacuous — too few run_bd sites found")
        for mod, lineno, seg, toks, _ in sites:
            for banned in self.BANNED:
                self.assertNotIn(
                    banned, seg,
                    f"{banned} appears in run_bd argv source at "
                    f"{mod}:{lineno}: {seg!r}")
                self.assertNotIn(banned, toks)

    def test_close_argv_only_in_authorized_surface(self):
        total_close = 0
        for mod in self.MODULES:
            path = os.path.join(os.path.dirname(HERE), mod)
            with open(path) as fh:
                src = fh.read()
            tree = ast.parse(src)

            def enclosing(node):
                best = None
                for fn in ast.walk(tree):
                    if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        if any(sub is node for sub in ast.walk(fn)):
                            if best is None or (
                                    fn.lineno <= node.lineno
                                    and fn.lineno >= best.lineno):
                                best = fn
                return best.name if best else "<module>"

            for node in ast.walk(tree):
                if (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and node.func.attr == "run_bd" and node.args
                        and isinstance(node.args[0], ast.List)):
                    toks = [e.value for e in node.args[0].elts
                            if isinstance(e, ast.Constant)]
                    if "close" in toks or "reopen" in toks:
                        total_close += 1
                        self.assertEqual(
                            mod, "evidence.py",
                            f"close/reopen argv outside evidence.py at "
                            f"{mod}:{node.lineno}")
                        self.assertIn(
                            enclosing(node), self.AUTHZ_FUNCS,
                            f"close/reopen argv in non-authorized function "
                            f"at {mod}:{node.lineno}")
        self.assertGreaterEqual(total_close, 2,
                                "audit vacuous — no authorized close/reopen "
                                "argv sites found")


class PolicyIsPolicyNotIsolation(unittest.TestCase):
    """Trusted-agent POLICY vs hostile isolation (CONTRACTS-v3 boundary):
    the worker-closure refusal is plugin-surface cooperation, and the native
    CLI escape hatch provably stays open. No sandbox, no ACL claim."""

    def test_native_close_escape_hatch_stays_open(self):
        store = make_store()
        iid = create(store, "escape hatch")
        holder = actor("holder")
        claims.claim(store, iid, actor=holder, bd_bin=BD_BIN)
        surf = evidence.WorkerSurface(store, actor=holder, bd_bin=BD_BIN)
        with self.assertRaises(evidence.WorkerClosureRefusedError):
            surf.request_closure(iid)
        # policy refused; native remains fully usable — this is what proves
        # the refusal is policy, NOT an isolation boundary.
        p = raw(store, ["--actor", holder, "close", iid,
                        "--reason", "native escape hatch", "--json"])
        self.assertEqual(p.returncode, 0, p.stderr)
        back = show_dict(store, iid)
        self.assertEqual(back["status"], "closed")
        self.assertEqual(back["close_reason"], "native escape hatch")

    def test_readonly_is_policy_convenience_not_isolation(self):
        # --readonly is a CLI-global convenience flag: the SAME binary run
        # WITHOUT it mutates freely. Never advertise it as hostile containment.
        store = make_store()
        iid = create(store, "policy bead")
        blocked = raw(store, ["--readonly", "comment", iid, "nope"])
        self.assertEqual(blocked.returncode, 1)
        allowed = raw(store, ["--actor", actor("op"), "comment", iid, "yes"])
        self.assertEqual(allowed.returncode, 0, allowed.stderr)


class DeclaredTopologyGate(unittest.TestCase):
    """Declared-topology precondition before any future parallel enablement:
    one effecting writer per disposable store per test; unique actor per
    run/attempt; concurrent multi-writer and CAS are explicitly UNQUALIFIED.
    This test fails if the qualification doc stops saying so."""

    def test_doc_declares_topology_and_unqualified_claims(self):
        self.assertTrue(os.path.isfile(DOCS),
                        f"missing {DOCS}")
        with open(DOCS) as fh:
            doc = fh.read()
        for phrase in ("declared topology", "single effecting writer",
                       "concurrent multi-writer: UNQUALIFIED",
                       "CAS: UNQUALIFIED",
                       "trusted-agent policy",
                       "not a security boundary"):
            self.assertIn(phrase, doc,
                          f"negative-qualification.md must state {phrase!r}")

    def test_suite_fixture_helper_uses_unique_actors(self):
        seen = {actor("u") for _ in range(50)}
        self.assertEqual(len(seen), 50, "actor() must be unique per use")


if __name__ == "__main__":
    os.makedirs(FIXTURE_ROOT, exist_ok=True)
    unittest.main(verbosity=2)
