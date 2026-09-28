#!/usr/bin/env python3
"""Evidence notes + authorization-aware closure separation (hbl-pnu.1.4).

Verdict under test — the plugin surface separates WHO may close from WHO
records evidence. Worker-surface calls attempting closure are refused (the
worker appends REQUEST-CLOSURE evidence instead, and no close argv is ever
built from the worker path — proven by an AST audit). Authorized closure
requires explicit caller authorization + a non-empty artifact-citing reason
+ a real evidence comment matching the exact store/bead/attempt scope, and
is only believed after read-back of closed_at, close_reason and the still-
present evidence comment. Native `bd close` accepts an empty reason (probed
bd 1.3.0, f45b249ce) — the non-empty reason is enforced HERE; native close
is never patched. Epic closure is INSPECTED via `bd epic status` +
`bd epic close-eligible --dry-run` only; a sweep verb is never built.
No scheduler, no role-ACL claims: surface separation, not hostile
containment.

Real native evidence, no mocks: each case runs the ACTUAL pinned bd against
a fresh disposable fixture store under tests/fixtures/evidence-runtime
(uniquely-named, gitignored; each fixture owns its `git init` so the
embedded-dolt home never falls through to the lab repo — the recorded
trap). The planning store is NEVER touched here.

Run: python3 tests/test_evidence.py
"""
import ast
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import evidence            # noqa: E402  (the module under test)
import read_model          # noqa: E402
import write_protocol      # noqa: E402

BD_BIN = os.environ.get("BEADS_LAB_BD",
                        "/home/hermes/.hermes/work/beads-lab/bin/bd")
FIXTURE_ROOT = os.path.join(HERE, "fixtures", "evidence-runtime")


def make_store():
    # unique name per fixture: tests/fixtures/evidence-runtime/<case>-<uuid>
    d = tempfile.mkdtemp(dir=FIXTURE_ROOT, prefix="ev-")
    subprocess.run(["git", "init", "-q", "."], cwd=d, check=True,
                   capture_output=True)
    p = subprocess.run([BD_BIN, "init", "--prefix", "ev"], cwd=d,
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    return d


def actor(prefix="w"):
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def create(store, title, description="d", actor_name="seeder", parent=None):
    argv = [BD_BIN, "-C", store, "--actor", actor_name, "create", title,
            "--description", description, "--json"]
    if parent:
        argv += ["--parent", parent]
    p = subprocess.run(argv, capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    rows = json.loads(p.stdout)
    return (rows[0] if isinstance(rows, list) else rows)["id"]


def show_raw(store, iid):
    p = subprocess.run([BD_BIN, "-C", store, "--readonly", "show", iid,
                        "--json"], capture_output=True)
    assert p.returncode == 0, p.stderr
    return p.stdout


def show_dict(store, iid):
    return json.loads(show_raw(store, iid))[0]


def comments_raw(store, iid):
    p = subprocess.run([BD_BIN, "-C", store, "--readonly", "comments", iid,
                        "--json"], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


def seed_worker(store, prefix="worker"):
    """Bead claimed by a worker actor who has appended acceptance evidence."""
    iid = create(store, "worker bead")
    w = actor(prefix)
    import claims
    claims.claim(store, iid, actor=w, bd_bin=BD_BIN)
    wsurf = evidence.WorkerSurface(store, actor=w, bd_bin=BD_BIN)
    wsurf.record_evidence(iid, attempt="a1",
                          artifacts=["tests/test_evidence.py",
                                     "reports/completion-evidence.json"])
    return iid, w, wsurf


class EnvelopeRoundTrip(unittest.TestCase):
    def test_multiline_summary_preserves_exact_fields(self):
        text = evidence.evidence_comment_text(
            "a1+final", ["reports/result.json"],
            summary="first line\nsecond line attempt=a1 artifacts=other.json")
        self.assertEqual(evidence._parse_envelope_fields(text),
                         ("a1+final", ["reports/result.json"]))


class WorkerSurfaceSeparation(unittest.TestCase):
    def setUp(self):
        self.store = make_store()

    def test_worker_records_append_only_evidence(self):
        iid, w, _ = seed_worker(self.store)
        cs = comments_raw(self.store, iid)
        self.assertEqual(len(cs), 1)
        self.assertEqual(cs[0]["author"], w)
        self.assertIn("attempt=a1", cs[0]["text"])
        # append-only: a second evidence comment coexists, nothing edited
        w2 = evidence.WorkerSurface(self.store, actor=w, bd_bin=BD_BIN)
        w2.record_evidence(iid, attempt="a1", artifacts=["extra.log"])
        cs = comments_raw(self.store, iid)
        self.assertEqual(len(cs), 2)
        self.assertIn("extra.log", cs[1]["text"])

    def test_worker_close_attempt_is_refused_and_recorded(self):
        iid, w, wsurf = seed_worker(self.store)
        before = show_raw(self.store, iid)
        with self.assertRaises(evidence.WorkerClosureRefusedError):
            wsurf.request_closure(iid, detail="tests green")
        # nothing closed; a REQUEST-CLOSURE comment was appended instead
        self.assertEqual(show_dict(self.store, iid)["status"], "in_progress")
        texts = [c["text"] for c in comments_raw(self.store, iid)]
        self.assertTrue(any(t.startswith("REQUEST-CLOSURE") for t in texts))

    def test_worker_surface_has_no_close_or_reopen_method(self):
        for banned in ("close", "authorized_close", "reopen",
                       "authorized_reopen"):
            self.assertFalse(hasattr(evidence.WorkerSurface, banned),
                             f"worker surface exposes {banned}")


class AuthorizedClosure(unittest.TestCase):
    def setUp(self):
        self.store = make_store()
        self.iid, self.w, _ = seed_worker(self.store)
        self.parent = actor("parent")

    def _close(self, **kw):
        kw.setdefault("authorization", "parent-verified: wave1 review")
        kw.setdefault("reason",
                      "Verified: tests/test_evidence.py + "
                      "reports/completion-evidence.json green")
        return evidence.authorized_close(
            self.store, self.iid, actor=self.parent, bd_bin=BD_BIN,
            evidence_actor=self.w, attempt="a1",
            artifacts=["tests/test_evidence.py",
                       "reports/completion-evidence.json"], **kw)

    def test_authorized_close_with_evidence_readback_verifies(self):
        rec = self._close()
        self.assertTrue(rec["readback_verified"])
        row = show_dict(self.store, self.iid)
        self.assertEqual(row["status"], "closed")
        self.assertTrue(row["closed_at"], "closed_at required")
        self.assertEqual(row["close_reason"],
                         "Verified: tests/test_evidence.py + "
                         "reports/completion-evidence.json green")
        # evidence comment STILL present via the comments API
        texts = [c["text"] for c in comments_raw(self.store, self.iid)]
        self.assertTrue(any(t.startswith("EVIDENCE") and "attempt=a1" in t
                            for t in texts))

    def test_close_without_matching_evidence_refused_untouched(self):
        before = show_raw(self.store, self.iid)
        with self.assertRaises(evidence.ClosureRefusedError):
            evidence.authorized_close(
                self.store, self.iid, actor=self.parent, bd_bin=BD_BIN,
                authorization="parent says so", reason="done: report.md",
                evidence_actor="ghost-actor", attempt="a1",
                artifacts=["report.md"])
        self.assertEqual(show_raw(self.store, self.iid), before)
        self.assertEqual(show_dict(self.store, self.iid)["status"],
                         "in_progress")

    def test_wrong_attempt_scope_refused(self):
        before = show_raw(self.store, self.iid)
        with self.assertRaises(evidence.ClosureRefusedError):
            evidence.authorized_close(
                self.store, self.iid, actor=self.parent, bd_bin=BD_BIN,
                authorization="parent ok", reason="done: tests/test_evidence.py",
                evidence_actor=self.w, attempt="OTHER-attempt",
                artifacts=["tests/test_evidence.py"])
        self.assertEqual(show_raw(self.store, self.iid), before)

    def test_attempt_prefix_collision_refused(self):
        """REVIEW-A: evidence for attempt 'a1-final' must NOT satisfy a
        close demanding 'a1' — 'attempt=a1' is a substring of
        'attempt=a1-final'; the attempt token must match at its boundary."""
        store = make_store()
        iid = create(store, "collision bead")
        w = actor("worker")
        import claims
        claims.claim(store, iid, actor=w, bd_bin=BD_BIN)
        wsurf = evidence.WorkerSurface(store, actor=w, bd_bin=BD_BIN)
        wsurf.record_evidence(iid, attempt="a1-final",
                              artifacts=["tests/test_evidence.py",
                                         "reports/completion-evidence.json"])
        before = show_raw(store, iid)
        with self.assertRaises(evidence.ClosureRefusedError):
            evidence.authorized_close(
                store, iid, actor=actor("parent"), bd_bin=BD_BIN,
                authorization="parent ok",
                reason="done: tests/test_evidence.py "
                       "reports/completion-evidence.json",
                evidence_actor=w, attempt="a1",
                artifacts=["tests/test_evidence.py",
                           "reports/completion-evidence.json"])
        self.assertEqual(show_raw(store, iid), before)
        self.assertEqual(show_dict(store, iid)["status"], "in_progress")

    def test_vacuous_artifact_citation_refused(self):
        """REVIEW-B: artifacts=['e'] + reason='e' must be refused — an
        artifact must be an artifact-shaped token (path/URL/ID), and it must
        appear in BOTH the reason and the evidence comment."""
        # single-char artifact rejected before any store access
        before = show_raw(self.store, self.iid)
        with self.assertRaises(evidence.ClosureRefusedError):
            evidence.authorized_close(
                self.store, self.iid, actor=self.parent, bd_bin=BD_BIN,
                authorization="parent ok", reason="e",
                evidence_actor=self.w, attempt="a1", artifacts=["e"])
        self.assertEqual(show_raw(self.store, self.iid), before)
        # artifact-shaped but absent from the reason -> refused
        with self.assertRaises(evidence.ClosureRefusedError):
            evidence.authorized_close(
                self.store, self.iid, actor=self.parent, bd_bin=BD_BIN,
                authorization="parent ok", reason="done",
                evidence_actor=self.w, attempt="a1",
                artifacts=["tests/test_evidence.py"])
        self.assertEqual(show_raw(self.store, self.iid), before)
        # artifact-shaped but absent from the evidence comment -> refused
        with self.assertRaises(evidence.ClosureRefusedError):
            evidence.authorized_close(
                self.store, self.iid, actor=self.parent, bd_bin=BD_BIN,
                authorization="parent ok", reason="done: phantom/report.log",
                evidence_actor=self.w, attempt="a1",
                artifacts=["phantom/report.log"])
        self.assertEqual(show_raw(self.store, self.iid), before)

    def test_wrong_store_scope_refused(self):
        other = make_store()
        iid2 = create(other, "unrelated bead")
        with self.assertRaises(evidence.ClosureRefusedError):
            evidence.authorized_close(
                other, iid2, actor=self.parent, bd_bin=BD_BIN,
                authorization="parent ok",
                reason="done: tests/test_evidence.py reports/completion-evidence.json",
                evidence_actor=self.w, attempt="a1",
                artifacts=["tests/test_evidence.py",
                           "reports/completion-evidence.json"])

    def test_missing_authorization_refused(self):
        with self.assertRaises(evidence.ClosureRefusedError):
            self._close(authorization="")

    def test_empty_reason_refused_even_though_native_allows_it(self):
        # pinned fact: native `bd close` accepts an empty reason; the plugin
        # surface must still refuse.
        p = subprocess.run([BD_BIN, "-C", self.store, "--actor", "native-probe",
                            "create", "native empty-reason probe", "--json"],
                           capture_output=True, text=True)
        native_iid = json.loads(p.stdout)["id"] if isinstance(
            json.loads(p.stdout), dict) else json.loads(p.stdout)[0]["id"]
        p = subprocess.run([BD_BIN, "-C", self.store, "--actor", "native-probe",
                            "close", native_iid, "--json"],
                           capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, "native allows empty reason")
        with self.assertRaises(evidence.ClosureRefusedError):
            self._close(reason="   ")


class ExactEnvelopeMatching(unittest.TestCase):
    """Parent ruling: match the machine envelope FIELDS exactly — anchored
    header `EVIDENCE attempt=<a> artifacts=<;sep> [summary=...]` with exact
    attempt equality and exact semicolon-delimited artifact membership —
    never a regex/substring approximation over the whole comment text."""

    def setUp(self):
        self.store = make_store()
        self.parent = actor("parent")

    def _seed(self, attempt, artifacts, summary=""):
        iid = create(self.store, "envelope bead")
        w = actor("worker")
        import claims
        claims.claim(self.store, iid, actor=w, bd_bin=BD_BIN)
        evidence.WorkerSurface(self.store, actor=w, bd_bin=BD_BIN) \
            .record_evidence(iid, attempt=attempt, artifacts=artifacts,
                             summary=summary)
        return iid, w

    def _close(self, iid, w, *, attempt, artifacts, reason):
        return evidence.authorized_close(
            self.store, iid, actor=self.parent, bd_bin=BD_BIN,
            authorization="parent ok", reason=reason,
            evidence_actor=w, attempt=attempt, artifacts=artifacts)

    def _assert_refused_untouched(self, iid, before, **kw):
        with self.assertRaises(evidence.ClosureRefusedError):
            self._close(iid, **kw)
        self.assertEqual(show_raw(self.store, iid), before)
        self.assertEqual(show_dict(self.store, iid)["status"], "in_progress")

    def test_punctuation_flanked_attempt_collision_refused(self):
        """Parent-found hole: 'a1+final' — '+' is outside the separator
        class, so attempt=a1 boundary-matched inside attempt=a1+final.
        Exact attempt-field equality closes this whole punctuation class."""
        iid, w = self._seed("a1+final", ["tests/test_evidence.py"])
        before = show_raw(self.store, iid)
        self._assert_refused_untouched(
            iid, before, w=w, attempt="a1",
            artifacts=["tests/test_evidence.py"],
            reason="done: tests/test_evidence.py")

    def test_artifact_suffix_collision_refused(self):
        """Parent-found hole: artifacts=['test_evidence.py'] matched as a
        substring of the recorded 'tests/test_evidence.py'. Artifact
        entries must match a ';' field entry exactly."""
        iid, w = self._seed("a1", ["tests/test_evidence.py"])
        before = show_raw(self.store, iid)
        self._assert_refused_untouched(
            iid, before, w=w, attempt="a1",
            artifacts=["test_evidence.py"],
            reason="done: test_evidence.py")

    def test_summary_tokens_are_not_fields_refused(self):
        """A path or attempt token that appears only in the opaque summary
        tail is not field evidence and must not satisfy a close."""
        iid, w = self._seed("a1", ["notes/x.md"],
                            summary="see tests/test_evidence.py for proof")
        before = show_raw(self.store, iid)
        self._assert_refused_untouched(
            iid, before, w=w, attempt="a1",
            artifacts=["tests/test_evidence.py"],
            reason="done: tests/test_evidence.py")

    def test_exact_fields_still_close(self):
        """Positive control: exact attempt + exact artifact entries close
        even when the summary flanks lookalike tokens."""
        iid, w = self._seed(
            "a1", ["tests/test_evidence.py"],
            summary="decoy attempt=a1+final test_evidence.py noise")
        rec = self._close(iid, w, attempt="a1",
                          artifacts=["tests/test_evidence.py"],
                          reason="Verified: tests/test_evidence.py")
        self.assertTrue(rec["readback_verified"])
        self.assertEqual(show_dict(self.store, iid)["status"], "closed")


class AuthorizedReopen(unittest.TestCase):
    def test_authorized_reopen_readback_and_refusal(self):
        store = make_store()
        iid = create(store, "to reopen")
        a = actor("closer")
        subprocess.run([BD_BIN, "-C", store, "--actor", a, "close", iid,
                        "--reason", "done: artifact.log", "--json"],
                       capture_output=True, text=True, check=True)
        rec = evidence.authorized_reopen(store, iid, actor=a, bd_bin=BD_BIN,
                                         authorization="parent ok",
                                         reason="reopen: evidence was stale")
        self.assertTrue(rec["readback_verified"])
        self.assertEqual(show_dict(store, iid)["status"], "open")
        before = show_raw(store, iid)
        with self.assertRaises(evidence.ClosureRefusedError):
            evidence.authorized_reopen(store, iid, actor=a, bd_bin=BD_BIN,
                                       authorization="", reason="whatever")
        self.assertEqual(show_raw(store, iid), before)


class EpicInspectionNoSweep(unittest.TestCase):
    def test_epic_eligibility_read_only_inspection(self):
        store = make_store()
        epic = create(store, "epic", actor_name="planner")
        # real -t epic (create above is a task); make a real epic instead
        p = subprocess.run([BD_BIN, "-C", store, "--actor", "planner",
                            "create", "real epic", "-t", "epic", "--json"],
                           capture_output=True, text=True)
        d = json.loads(p.stdout)
        epic = (d[0] if isinstance(d, list) else d)["id"]
        kid = create(store, "child", parent=epic)
        rec = evidence.inspect_epic_closure_eligibility(store, epic,
                                                        bd_bin=BD_BIN)
        self.assertFalse(rec["sweep_issued"])
        self.assertEqual(rec["status"][0]["epic"]["id"], epic)
        self.assertFalse(rec["status"][0]["eligible_for_close"])
        subprocess.run([BD_BIN, "-C", store, "--actor", "planner", "close",
                        kid, "--reason", "done: child artifact", "--json"],
                       capture_output=True, text=True)
        rec = evidence.inspect_epic_closure_eligibility(store, epic,
                                                        bd_bin=BD_BIN)
        self.assertTrue(rec["status"][0]["eligible_for_close"])
        self.assertTrue(rec["eligible_preview"])


class ReadbackAmbiguity(unittest.TestCase):
    def test_readback_failure_is_ambiguity_not_success(self):
        """A close whose read-back loses close_reason surfaces as
        ClosureAmbiguityError — exit 0 alone is never success."""
        store = make_store()
        iid, w, _ = seed_worker(store)
        real_show = read_model.show
        def hijack(ws, idv, **kw):
            row = dict(real_show(ws, idv, **kw))
            row["close_reason"] = ""       # simulate lost reason
            return row
        read_model.show = hijack
        try:
            with self.assertRaises(evidence.ClosureAmbiguityError):
                evidence.authorized_close(
                    store, iid, actor=actor("parent"), bd_bin=BD_BIN,
                    authorization="parent ok",
                    reason="Verified: tests/test_evidence.py",
                    evidence_actor=w, attempt="a1",
                    artifacts=["tests/test_evidence.py"])
        finally:
            read_model.show = real_show


class CodePathAudit(unittest.TestCase):
    """Static audit of evidence.py: close/reopen argv verbs appear ONLY in
    the authorized_* functions; the epic sweep verb is never built anywhere."""

    AUTHORIZED = ("authorized_close", "authorized_reopen")

    def _function_calls(self, tree):
        found = []  # (function_name, [literal argv tokens])
        for fn in (n for n in ast.walk(tree)
                   if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))):
            for node in ast.walk(fn):
                if (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and node.func.attr == "run_bd"
                        and node.args
                        and isinstance(node.args[0], ast.List)):
                    toks = [e.value for e in node.args[0].elts
                            if isinstance(e, ast.Constant)]
                    found.append((fn.name, toks, node.lineno))
        return found

    def test_close_reopen_argv_only_in_authorized_functions(self):
        with open(os.path.join(os.path.dirname(HERE), "evidence.py")) as f:
            src = f.read()
        tree = ast.parse(src)
        calls = self._function_calls(tree)
        self.assertTrue(calls, "no literal run_bd argv found — audit broken")
        for fname, toks, lineno in calls:
            if not toks:
                continue
            if toks[0] in ("close", "reopen"):
                self.assertIn(fname, self.AUTHORIZED,
                              f"{toks[0]} argv built in {fname}() line {lineno}")
        # and both authorized verbs really build their verb
        verbs = {f: t[0] for f, t, _ in calls if t}
        self.assertEqual(verbs.get("authorized_close"), "close")
        self.assertEqual(verbs.get("authorized_reopen"), "reopen")

    def test_epic_sweep_verb_never_built(self):
        """close-eligible may appear ONLY paired with --dry-run; a bare
        sweep argv is never constructed; --force never appears."""
        with open(os.path.join(os.path.dirname(HERE), "evidence.py")) as f:
            src = f.read()
        tree = ast.parse(src)
        for fname, toks, lineno in self._function_calls(tree):
            if "close-eligible" in toks:
                self.assertIn("--dry-run", toks,
                              f"non-dry-run sweep argv at {fname} line {lineno}")
            for banned in ("--force", "done"):
                self.assertNotIn(banned, toks,
                                 f"{banned} built at {fname} line {lineno}")
        # worker classes never build any mutation verb beyond append-only
        # comments (via write_protocol) — no direct close/update argv in
        # WorkerSurface methods:
        for fname, toks, lineno in self._function_calls(tree):
            if fname in ("record_evidence", "request_closure"):
                self.fail(f"worker method {fname} builds run_bd argv at "
                          f"line {lineno}")


if __name__ == "__main__":
    os.makedirs(FIXTURE_ROOT, exist_ok=True)
    try:
        unittest.main(verbosity=2)
    finally:
        for name in os.listdir(FIXTURE_ROOT):
            shutil.rmtree(os.path.join(FIXTURE_ROOT, name), ignore_errors=True)
