#!/usr/bin/env python3
"""Two-tier install contract (docs/install.md).

Tier 1 (happy path): stock `hermes plugins enable hermes-beads` plus an
operator-configured bd (PATH or HERMES_BEADS_BD_BIN) — the six agent tools
mount, and the desktop pane is simply ABSENT: nothing in the shipped docs,
manifest, or tool output suggests the pane arrived on its own. Tier 2 (full
features) is documented-only: the pane needs the host-injected reads/provider/
telemetry/botView contract (desktop/workbench.mjs:45-58) and the optional,
version-pinned Hermes Desktop patch that exposes the @hermes/plugin-sdk entry
point; that entry point (desktop/plugin.js) is a stated follow-up slice.

This suite gates both claims with real probes, no mocks:
  1. scripts/verify-tier1.sh must pass end-to-end on vanilla hermes — enable
     into a throwaway HERMES_HOME created under $TMPDIR (never the repo),
     plugin + all six tools mount, zero false pane-tell hits;
  2. the scratch home must actually sit under the system temp dir (the spec's
     "throwaway HOME via tempfile under TMPDIR" is machine-checked, not prose);
  3. README.md + plugin.yaml carry zero 'loaded … automatically' hits;
  4. docs/install.md declares the tiers and pins the Hermes Desktop release
     the patch was written against, with update/reset restore notes;
  5. controls prove the two detectors are LIVE (a dead detector buys false
     confidence — the 0.1.0 wrapper-bug lesson).

Run: python3 tests/test_install_tiers.py   (CI gate 2's per-file loop picks
this up automatically; needs `hermes` on PATH and a bd via BEADS_LAB_BD/PATH.)
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
LANE = os.path.dirname(HERE)
SCRIPT = os.path.join(LANE, "scripts", "verify-tier1.sh")
TOOLS = ("beads_smoke", "beads_frontier", "beads_show", "beads_claim",
         "beads_update", "beads_comment")

# The false tell: shipped prose claiming the desktop pane arrives on its own.
FALSE_TELL = re.compile(r"loaded\s+automatically|automatically\s+loaded",
                        re.IGNORECASE)
SCAN_FILES = ("README.md", "plugin.yaml", "docs/install.md")


def false_tell_hits(texts):
    """texts: {name: content}. Returns ['name:line: snippet', ...]."""
    hits = []
    for name, text in texts.items():
        for m in FALSE_TELL.finditer(text):
            line = text.count("\n", 0, m.start()) + 1
            hits.append(f"{name}:{line}: {m.group(0)!r}")
    return hits


class Tier1Verify(unittest.TestCase):
    """scripts/verify-tier1.sh runs once; all its assertions live here."""

    @classmethod
    def setUpClass(cls):
        if not os.path.isfile(SCRIPT):
            raise AssertionError(f"missing {SCRIPT} — tier 1 is unverified")
        cls.script_rc, cls.script_out = cls.run_script()

    @staticmethod
    def run_script():
        p = subprocess.run(["bash", SCRIPT], capture_output=True, text=True,
                           cwd=HERE, env=dict(os.environ), timeout=900)
        return p.returncode, p.stdout + p.stderr

    def receipt(self):
        for line in self.script_out.splitlines():
            if line.startswith("TIER1_RECEIPT "):
                return json.loads(line[len("TIER1_RECEIPT "):])
        self.fail("verify-tier1.sh exited %d without a receipt:\n%s"
                  % (self.script_rc, self.script_out[-3000:]))

    def test_verify_script_green(self):
        self.assertEqual(self.script_rc, 0, self.script_out[-3000:])

    def test_receipt_shows_plugin_and_all_six_tools_mounted(self):
        r = self.receipt()
        self.assertIn("hermes-beads", r["plugins"], r)
        for t in TOOLS:
            self.assertIn(t, r["tools"], f"tool {t} did not mount: {r}")
        self.assertEqual(r["false_tell_hits"], 0, r)

    def test_scratch_home_is_tempdir_not_repo(self):
        r = self.receipt()
        tmp = os.path.realpath(tempfile.gettempdir())
        home = os.path.realpath(r["home"])
        self.assertTrue(home.startswith(tmp + os.sep),
                        f"scratch home {home} escapes {tmp}")
        self.assertNotEqual(home, os.path.realpath(LANE))


class DocHonesty(unittest.TestCase):
    def read(self, rel):
        with open(os.path.join(LANE, rel), encoding="utf-8") as fh:
            return fh.read()

    def test_shipped_docs_carry_no_false_pane_tell(self):
        texts = {f: self.read(f) for f in SCAN_FILES
                 if os.path.isfile(os.path.join(LANE, f))}
        self.assertEqual(false_tell_hits(texts), [],
                         "false pane tell in shipped docs")

    def test_false_tell_detector_is_live_control(self):
        planted = {"probe.md": "Boot the pane? It loaded automatically.\n"
                              "The pane was automatically loaded at startup."}
        self.assertEqual(len(false_tell_hits(planted)), 2,
                         "false-tell detector is dead")

    def test_readme_declares_two_tiers_and_links_install_doc(self):
        r = self.read("README.md")
        self.assertIn("docs/install.md", r)
        self.assertTrue(re.search(r"(?i)tier\s*1", r))
        self.assertTrue(re.search(r"(?i)tier\s*2", r))

    def test_install_doc_declares_tiers_pin_and_restore_notes(self):
        path = os.path.join(LANE, "docs", "install.md")
        self.assertTrue(os.path.isfile(path),
                        "docs/install.md missing — tier 2 is undocumented")
        with open(path, encoding="utf-8") as fh:
            d = fh.read()
        for must in ("Tier 1", "Tier 2", "Hermes Desktop", "desktop/plugin.js",
                     "botView", "telemetry", "HERMES_BEADS_BD_BIN"):
            self.assertIn(must, d, f"docs/install.md lacks {must!r}")
        self.assertTrue(
            re.search(r"(?i)hermes\s+update", d),
            "docs/install.md lacks update/reset restore notes")
        # The tier-2 pin is a concrete version (X.Y or X.Y.Z), not prose.
        self.assertTrue(
            re.search(r"(?i)hermes\s+desktop\s*[^\n]{0,40}?\d+\.\d+", d),
            "docs/install.md does not pin the Hermes Desktop release")


if __name__ == "__main__":
    unittest.main(verbosity=2)
