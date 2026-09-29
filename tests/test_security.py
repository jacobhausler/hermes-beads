#!/usr/bin/env python3
"""Security qualification (hbl-pnu.4.4): hostile content + argv/path injection.

Two threat classes, each with a live negative control proving the assertion
would actually fail if the defence broke:

  1. Hostile bead content — HTML <script>/onerror, markdown javascript:
     links, ANSI escapes, RTL overrides, 64KB titles, shell metacharacters —
     round-trips through read_model as VERBATIM inert text (field-preservation
     law) and renders inert through the real desktop components: the render
     probe asserts no dangerouslySetInnerHTML anywhere and the payload present
     as an escaped text child. The control mode of the probe renders the same
     payload through dangerouslySetInnerHTML and must be caught.

  2. argv/path injection — an ID-looking token (--db=..., ../../x, ;rm, a
     newline) is refused by native.run_bd's fixed-argv shape gate BEFORE any
     spawn; a no-spawn spy proves refusal precedes subprocess.run. Legitimate
     values that merely contain those substrings (comment text "; rm -rf /;",
     a description holding ../../etc/passwd) pass — the gate compares whole
     tokens, never text bodies.

Real pinned bd (1.3.0, f45b249ce), disposable stores under
tests/security-runtime/ (gitignored), each with its own git root. The
planning store is never touched.

Run: python3 tests/test_security.py   (failures exit nonzero)
"""
import inspect
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
LANE = os.path.dirname(HERE)
sys.path.insert(0, LANE)

import native            # noqa: E402
import read_model        # noqa: E402
import claims            # noqa: E402
import write_protocol    # noqa: E402
import evidence          # noqa: E402

BD_BIN = os.environ.get("BEADS_LAB_BD",
                        "/home/hermes/.hermes/work/beads-lab/bin/bd")
NODE = os.environ.get("NODE_BIN", "node")
FIXTURE_ROOT = os.path.join(HERE, "security-runtime")

VOLATILE = {"created_at", "updated_at", "closed_at", "started_at",
            "dependency_count", "dependent_count", "comment_count",
            "id", "issue_id", "revision", "lease_expires_at",
            "heartbeat_at", "closed_by", "actor"}

# Every payload is stored VERBATIM-safe by bd (whole-token argv), so the
# read-back comparison is exact equality — no normalisation anywhere.
HOSTILE = {
    "html_script": '<script>alert("xss")</script>',
    "html_onerror": '<img src=x onerror=alert(1)>',
    "md_javascript": "[click me](javascript:alert(document.cookie))",
    "ansi": "\x1b[2J\x1b]0;owned\x07 \x1b[31mRED\x1b[0m",
    "rtl": "pay \u202e top \u202d boss \u202e now",
    "ltr_control_mix": "abc\u200e\u200fdef\u202aghi\u202b",
    "big_title": "T" * 65536,
    "shell_metachars": '; rm -rf / ; $(whoami) `id` | cat && echo pwned',
    "newline_text": "line1\nline2\rline3",
    "flag_like_text": "--db=/etc/x ../../x ;rm",   # as TEXT body, not an id
    "bell_backspace": "a\x07b\x08c\x0bd\x0ce",
}

HOSTILE_IDS = ("--db=/etc/x", "../../x", ";rm", "a\nb", "--readonly",
               "--actor=evil", "-n", "-", "--", "x\n", "a\r\nb")


def make_store(prefix="sec"):
    os.makedirs(FIXTURE_ROOT, exist_ok=True)
    d = tempfile.mkdtemp(dir=FIXTURE_ROOT, prefix=prefix + "-")
    subprocess.run(["git", "init", "-q", "."], cwd=d, check=True,
                   capture_output=True)
    p = subprocess.run([BD_BIN, "init", "--prefix", prefix], cwd=d,
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    return d


def actor(prefix="sec"):
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def raw_create(store, title, description, a):
    p = subprocess.run([BD_BIN, "-C", store, "--actor", a, "create", title,
                        "--description", description, "--json"],
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    rows = json.loads(p.stdout)
    return (rows[0] if isinstance(rows, list) else rows)["id"]


def canonical(row):
    return {k: v for k, v in row.items() if k not in VOLATILE}


# ---- 1. hostile content: verbatim through the read model -------------------

class HostileContentReadModel(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store = make_store("hostile")
        cls.a = actor("writer")
        cls.ids = {}
        for name, payload in HOSTILE.items():
            cls.ids[name] = raw_create(cls.store, payload, payload, cls.a)

    def test_show_roundtrips_verbatim(self):
        for name, payload in HOSTILE.items():
            with self.subTest(name):
                row = read_model.show(self.store, self.ids[name],
                                       bd_bin=BD_BIN)
                self.assertIsNotNone(row, name)
                self.assertEqual(row.get("title"), payload,
                                 f"{name}: title not verbatim")
                self.assertEqual(row.get("description"), payload,
                                 f"{name}: description not verbatim")

    def test_list_rows_survive_verbatim(self):
        """Field-preservation law: list rows carry hostile content exactly
        as bd emitted it — no whitelist/coercion eats or rewrites it."""
        rows = read_model.list_issues(self.store, include_closed=True,
                                       bd_bin=BD_BIN)
        by_id = {r["id"]: r for r in rows}
        for name, iid in self.ids.items():
            with self.subTest(name):
                self.assertIn(iid, by_id)
                self.assertEqual(by_id[iid]["title"], HOSTILE[name])

    def test_control_would_catch_truncation(self):
        """Negative control: a truncated/normalised value IS caught by the
        same comparison shape used above."""
        row = {"id": "x", "title": HOSTILE["big_title"][:100],
               "description": "ok"}
        self.assertNotEqual(row["title"], HOSTILE["big_title"])


# ---- 1b. hostile content: inert through the REAL desktop render ------------

class HostileContentRender(unittest.TestCase):
    def test_render_is_inert_text(self):
        probe = os.path.join(HERE, "security_render_probe.mjs")
        payloads = {k: v + f" MARKER_{k}" for k, v in HOSTILE.items()}
        proc = subprocess.run(
            [NODE, probe, "--real", json.dumps(payloads)],
            capture_output=True, text=True, timeout=120, cwd=LANE)
        self.assertEqual(proc.returncode, 0,
                         f"render probe failed: {proc.stdout} {proc.stderr}")

    def test_control_catches_active_markup(self):
        """Negative control: the same probe run against an intentionally
        unsafe renderer (dangerouslySetInnerHTML) MUST fail — otherwise the
        inert assertion above proves nothing."""
        probe = os.path.join(HERE, "security_render_probe.mjs")
        payloads = {"evil": '<img src=x onerror=alert(1)> MARKER_evil'}
        proc = subprocess.run(
            [NODE, probe, "--control", json.dumps(payloads)],
            capture_output=True, text=True, timeout=120, cwd=LANE)
        self.assertNotEqual(proc.returncode, 0,
                            "control renderer was NOT caught")
        self.assertIn("ACTIVE-MARKUP", proc.stderr + proc.stdout)


# ---- 2. argv/path injection at the native boundary -------------------------

class _SpawnSpy:
    """Records every subprocess.run that reaches the OS layer inside native."""

    def __init__(self):
        self.calls = []
        self._real = subprocess.run

    def __enter__(self):
        spy = self

        def fake(cmd, *a, **kw):
            spy.calls.append(list(cmd))
            return spy._real(cmd, *a, **kw)
        self._saved = native.subprocess.run
        native.subprocess.run = fake
        return self

    def __exit__(self, *exc):
        native.subprocess.run = self._saved
        return False


class ArgvInjection(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store = make_store("argv")
        cls.a = actor("argv")
        cls.iid = raw_create(cls.store, "injection control", "d", cls.a)

    def test_hostile_id_tokens_refused_before_spawn(self):
        for tok in HOSTILE_IDS:
            with self.subTest(tok):
                with _SpawnSpy() as spy:
                    with self.assertRaises(ValueError):
                        native.run_bd(["show", tok, "--json"],
                                      workspace=self.store, bd_bin=BD_BIN)
                    self.assertEqual(spy.calls, [],
                                     f"spawned with hostile token {tok!r}")

    def test_malformed_prefixes_refused(self):
        for bad in ("show --json", ["show", 5, "--json"], None,
                    ("show", "--json"), ["--json"]):
            with self.subTest(repr(bad)):
                with self.assertRaises(ValueError):
                    native.run_bd(bad, workspace=self.store, bd_bin=BD_BIN)

    def test_actor_tokens_refused(self):
        """--actor/--readonly must not be forgeable through actor= either."""
        for bad in ("--db=/etc/x", "--", "evil\nname", "-n"):
            with self.subTest(bad):
                with _SpawnSpy() as spy:
                    with self.assertRaises(ValueError):
                        native.run_bd(["show", self.iid, "--json"],
                                      workspace=self.store, bd_bin=BD_BIN,
                                      actor=bad)
                    self.assertEqual(spy.calls, [])

    def test_gateway_flag_never_spoofable(self):
        """A prefix token that duplicates the run_bd-controlled flags is
        refused; the legit single --actor/--readonly stay in their slots."""
        with _SpawnSpy() as spy:
            with self.assertRaises(ValueError):
                native.run_bd(["--actor", "evil", "show", self.iid, "--json"],
                              workspace=self.store, bd_bin=BD_BIN)
            with self.assertRaises(ValueError):
                native.run_bd(["--readonly", "show", self.iid, "--json"],
                              workspace=self.store, bd_bin=BD_BIN)
            self.assertEqual(spy.calls, [])
        # legitimate path spawns exactly once with the flags only in slots
        with _SpawnSpy() as spy:
            native.run_bd(["show", self.iid, "--json"], workspace=self.store,
                          bd_bin=BD_BIN, readonly=True, actor=self.a)
            argv = spy.calls[0]
            self.assertEqual(argv[0], BD_BIN)
            self.assertEqual(argv[1:5], ["--readonly", "--actor", self.a])
            self.assertEqual(argv.count("--actor"), 1)
            self.assertEqual(argv.count("--readonly"), 1)

    def test_legit_values_merely_containing_metachars_pass(self):
        """The gate compares WHOLE tokens: comment text that starts with a
        non-dash token and contains ;rm/../../x/newline flows to bd fine."""
        text = ("injection-text; rm -rf / ; ../../etc/passwd \n"
                "--db=/etc/x stays inert inside a text body")
        rows = write_protocol.append_comment(self.store, self.iid,
                                             actor=self.a, bd_bin=BD_BIN,
                                             text=text)
        self.assertTrue(any(text == c.get("text") for c in rows),
                        "text body did not reach bd verbatim")

    def test_claims_actor_gate_refused_without_spawn(self):
        with _SpawnSpy() as spy:
            with self.assertRaises(ValueError):
                claims.claim(self.store, self.iid, actor="--db=/etc/x",
                             bd_bin=BD_BIN)
            for call in spy.calls:
                self.assertNotIn("--db=/etc/x", call)

    def test_plugin_paths_spawn_only_fixed_argv(self):
        """Full legit loop through claims/write_protocol/evidence surfaces:
        every spawned argv[0] is the pinned binary, no token was ever built
        by concatenation of a value into a flag, and every --actor value is
        the exact actor we passed."""
        a = actor("legit")
        iid = raw_create(self.store, "legit loop", "d", a)
        with _SpawnSpy() as spy:
            claims.claim(self.store, iid, actor=a, bd_bin=BD_BIN)
            claims.heartbeat(self.store, iid, actor=a, bd_bin=BD_BIN)
            write_protocol.update_fields(
                self.store, iid, actor=a, bd_bin=BD_BIN, if_assignee=a,
                if_status="in_progress", fields={"notes": "sec note"})
            ws = evidence.WorkerSurface(self.store, actor=a, bd_bin=BD_BIN)
            ws.record_evidence(iid, attempt="a-sec",
                               artifacts=["tests/test_security.py"])
        self.assertTrue(spy.calls, "no spawn recorded — spy broken")
        for argv in spy.calls:
            self.assertEqual(argv[0], BD_BIN)
            self.assertTrue(all(isinstance(t, str) for t in argv))
            if "--actor" in argv:
                i = argv.index("--actor")
                self.assertEqual(argv[i + 1], a)
                self.assertEqual(argv.count("--actor"), 1)

    # ---- shape-gate canaries ---------------------------------------------

    def test_run_bd_wires_the_shape_gate(self):
        """Canary: the gate is WIRED into run_bd, not dead code — if the
        call disappears the suite goes red."""
        src = inspect.getsource(native.run_bd)
        self.assertIn("_shape_ok(", src)
        self.assertTrue(callable(getattr(native, "_shape_ok", None)))

    def test_legit_shape_corpus_covers_shipped_call_sites(self):
        """Canary for the shape table: every legitimate fixed-argv shape the
        plugin builds must be accepted by the pure gate. If run_bd grows a
        new call-site shape, add it here AND to _FIXED_ARGV_SHAPES together
        (this test failing = the table and the call sites drifted)."""
        ok = native._shape_ok
        V, ID = "@value", "@id"
        corpus = [
            ["ready", "--json", "--exclude-type=epic", "-n", V, "--max-rows", V],
            ["ready", "--json", "--exclude-type=epic", "-n", V, "--max-rows", V,
             "--label", V],
            ["show", ID, "--json"],
            ["list", "--json", "-n", V, "--max-rows", V],
            ["list", "--json", "-n", V, "--max-rows", V, "--status", V],
            ["list", "--json", "-n", V, "--max-rows", V, "--label", V],
            ["list", "--json", "-n", V, "--max-rows", V, "--parent", V],
            ["list", "--json", "-n", V, "--max-rows", V, "--all"],
            ["list", "--json", "--parent", V, "--status", "all",
             "-n", V, "--max-rows", V],
            ["query", f"parent={V}", "--json", "-n", V],
            ["blocked", "--json"],
            ["blocked", "--json", "--label", V],
            ["blocked", "--json", "--parent", V],
            ["comments", ID, "--json"],
            ["comments", "add", ID, V],
            ["history", ID, "--json", "--limit", V],
            ["info", "--json"],
            ["update", ID, "--claim", "--json"],
            ["update", ID, "--if-assignee", V, "--if-status", V, "--json"],
            ["update", ID, "--if-assignee", V, "--if-status", V, "--json",
             "--priority", V],
            ["update", ID, "--if-assignee", V, "--if-status", V, "--json",
             "--notes", V],
            ["update", ID, "--if-assignee", V, "--if-status", V, "--json",
             "--due", V],
            ["update", ID, "--if-assignee", V, "--if-status", V, "--json",
             "--estimate", V],
            ["update", ID, "--if-assignee", V, "--if-status", V, "--json",
             "--external-ref", V],
            ["update", ID, "--if-assignee", V, "--if-status", V, "--json",
             "--defer", V],
            ["update", ID, "--if-assignee", V, "--if-status", V, "--json",
             "--title", V],
            ["update", ID, "--if-assignee", V, "--if-status", V, "--json",
             "--description", V],
            ["update", ID, "--if-assignee", V, "--if-status", V, "--json",
             "--notes", V, "--due", V, "--estimate", V],
            ["heartbeat", ID, "--json"],
            ["unclaim", ID, "--if-assignee", V, "--json"],
            ["close", ID, "--reason", V, "--json"],
            ["reopen", ID, "--reason", V, "--json"],
            ["epic", "status", ID, "--json"],
            ["epic", "close-eligible", "--dry-run", "--json"],
            ["list", "--json", "--metadata-field", V],
            ["list", "--json", "--metadata-field", V, "--all"],
        ]
        for shape in corpus:
            with self.subTest(shape[0] + " " + shape[1]):
                self.assertTrue(ok(shape), f"legit shape refused: {shape}")
        # and the same corpus with ONE hostile token must be refused
        for shape in corpus:
            with self.subTest("hostile " + shape[0]):
                poisoned = list(shape)
                poisoned_tok = False
                for i, t in enumerate(poisoned):
                    if t in (V, ID):
                        poisoned[i] = ";rm"
                        poisoned_tok = True
                        break
                if not poisoned_tok:
                    poisoned.append(";rm")  # shape without value slots:
                    # an extra token is equally off-table
                self.assertFalse(ok(poisoned), f"hostile accepted: {poisoned}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
