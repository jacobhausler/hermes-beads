#!/usr/bin/env python3
"""Packaging qualification (): exact candidate archive, stock admission
validator, stock SDK load + uninstall in a disposable HERMES_HOME, JS syntax.

Nothing is installed or published. The candidate archive, extracted tree and
throwaway homes live under tests/.package-runtime/ (gitignored).

Run: python3 tests/test_packaging.py
"""
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
LANE = os.path.dirname(HERE)
RUNTIME = os.path.join(HERE, ".package-runtime")
os.makedirs(RUNTIME, exist_ok=True)
HERMES_ROOT = os.environ.get("HERMES_ROOT", "/opt/hermes")
VENV_PY = os.environ.get("HERMES_VENV_PY",
                         os.path.join(HERMES_ROOT, ".venv", "bin", "python"))
NODE = os.environ.get("NODE_BIN", "node")
NAME = "hermes-beads"

# Shipped = runtime modules + manifest + desktop source + docs. Tests, fixtures,
# lab scripts and dev scaffolding stay out of the distributable.
SHIP_TOP = ("plugin.yaml", "__init__.py", "README.md")
SHIP_DIRS = ("beads", "desktop")  # README.md is the shipped doc
NEVER_SHIP = re.compile(r"(^|/)(tests|fixtures|scripts|__pycache__|\.beads|"
                        r"\.git|node_modules|dist)(/|$)|\.pyc$")
# Private/lab strings that must never appear in the portable package.
PRIVATE = [re.compile(p) for p in (
    r"/home/hermes", r"beads-lab", r"lanes/", r"reports/", r"planning/",
    r"lab-owner", r"rhuidean", r"callindor", r"haus-")]


def shipped_files():
    out = []
    for name in sorted(os.listdir(LANE)):
        path = os.path.join(LANE, name)
        if os.path.isfile(path) and (name in SHIP_TOP or name.endswith(".py")):
            out.append(name)
    for d in SHIP_DIRS:
        for path in sorted(glob.glob(os.path.join(LANE, d, "**", "*"),
                                     recursive=True)):
            if os.path.isfile(path):
                out.append(os.path.relpath(path, LANE))
    return [f for f in out if not NEVER_SHIP.search(f)]


def build_archive(dest_dir):
    arc = os.path.join(dest_dir, f"{NAME}.tar.gz")
    with tarfile.open(arc, "w:gz") as tar:
        for rel in shipped_files():
            tar.add(os.path.join(LANE, rel), arcname=f"{NAME}/{rel}")
    return arc


def run_hermes(home, *args, timeout=180):
    env = dict(os.environ, HERMES_HOME=home)
    return subprocess.run([VENV_PY, "-m", "hermes_cli.main", *args],
                          capture_output=True, text=True, env=env,
                          timeout=timeout)


class Package(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.work = tempfile.mkdtemp(dir=RUNTIME, prefix="pkg-")
        cls.archive = build_archive(cls.work)
        cls.extract = os.path.join(cls.work, "extract")
        with tarfile.open(cls.archive) as tar:
            tar.extractall(cls.extract, filter="data")
        cls.tree = os.path.join(cls.extract, NAME)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.work, ignore_errors=True)

    def test_archive_contents_exact(self):
        with tarfile.open(self.archive) as tar:
            names = [m.name for m in tar.getmembers() if m.isfile()]
        rel = sorted(n.split("/", 1)[1] for n in names)
        self.assertEqual(rel, sorted(shipped_files()))
        for must in ("plugin.yaml", "__init__.py", "beads/__init__.py",
                     "beads/native.py", "beads/read_model.py",
                     "beads/claims.py", "beads/write_protocol.py",
                     "desktop/model.mjs", "desktop/tree.mjs"):
            self.assertIn(must, rel)
        for n in rel:
            self.assertIsNone(NEVER_SHIP.search(n), f"dev file shipped: {n}")

    def test_no_private_or_lab_strings(self):
        hits = []
        for root, _, files in os.walk(self.tree):
            for f in files:
                path = os.path.join(root, f)
                text = open(path, encoding="utf-8", errors="replace").read()
                for rx in PRIVATE:
                    for m in rx.finditer(text):
                        line = text.count("\n", 0, m.start()) + 1
                        hits.append(f"{os.path.relpath(path, self.tree)}:{line}: {rx.pattern}")
        self.assertEqual(hits, [], "private/lab strings in package:\n" + "\n".join(hits))

    def test_control_private_scan_is_live(self):
        probe = os.path.join(self.work, "leak.txt")
        with open(probe, "w") as fh:
            fh.write("see /home/hermes/.hermes/work/beads-lab/reports/x\n")
        text = open(probe).read()
        self.assertTrue(any(rx.search(text) for rx in PRIVATE))

    def test_stock_admission_validator_passes(self):
        home = tempfile.mkdtemp(dir=RUNTIME, prefix="val-")
        try:
            p = run_hermes(home, "plugins", "validate", self.tree, "--json")
            report = json.loads(p.stdout[p.stdout.index("{"):])
            failed = [c for c in report["checks"] if not c["ok"]]
            self.assertEqual(p.returncode, 0, p.stdout[-2000:] + p.stderr[-2000:])
            self.assertEqual(failed, [])
            self.assertEqual(report.get("warnings", []), [],
                             "packaged tree must scan clean (hostile test payloads stay out)")
        finally:
            shutil.rmtree(home, ignore_errors=True)

    def test_stock_load_then_uninstall_isolated(self):
        home = tempfile.mkdtemp(dir=RUNTIME, prefix="home-")
        try:
            plugins = os.path.join(home, "plugins")
            os.makedirs(plugins)
            shutil.copytree(self.tree, os.path.join(plugins, NAME))
            with open(os.path.join(home, "config.yaml"), "w") as fh:
                fh.write(f"plugins:\n  enabled:\n    - {NAME}\n")
            probe = (
                "import json\n"
                "from hermes_cli.plugins import discover_plugins, get_plugin_manager\n"
                "discover_plugins(force=True)\n"
                "m = get_plugin_manager()\n"
                "names = sorted(getattr(m, '_plugins', {}) or {})\n"
                "tools = sorted(getattr(m, '_plugin_tool_names', set()) or [])\n"
                "print(json.dumps({'plugins': names, 'tools': tools}))\n")
            env = dict(os.environ, HERMES_HOME=home)

            def discover():
                p = subprocess.run([VENV_PY, "-c", probe], capture_output=True,
                                   text=True, env=env, timeout=120)
                self.assertEqual(p.returncode, 0, p.stderr[-2000:])
                return json.loads(p.stdout.strip().splitlines()[-1])

            before = sorted(os.listdir(home))
            loaded = discover()
            self.assertIn(NAME, loaded["plugins"], loaded)
            self.assertIn("beads_smoke", loaded["tools"], loaded)
            # uninstall = remove the plugin dir; nothing else in the home changes
            shutil.rmtree(os.path.join(plugins, NAME))
            gone = discover()
            self.assertNotIn(NAME, gone["plugins"])
            self.assertNotIn("beads_smoke", gone["tools"])
            self.assertEqual(sorted(os.listdir(plugins)), [])
            self.assertTrue(set(before) <= set(os.listdir(home)))
        finally:
            shutil.rmtree(home, ignore_errors=True)

    def test_desktop_sources_parse(self):
        files = sorted(glob.glob(os.path.join(self.tree, "desktop", "*.mjs")))
        self.assertTrue(files)
        for f in files:
            with self.subTest(os.path.basename(f)):
                p = subprocess.run([NODE, "--check", f], capture_output=True,
                                   text=True)
                self.assertEqual(p.returncode, 0, p.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
