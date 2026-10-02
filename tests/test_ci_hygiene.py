#!/usr/bin/env python3
"""CI hygiene gates (estate bead est-atx, issue #2 follow-ups).

Two committed-artifact assertions (cf. committed-artifact gates: ci.yml IS
the artifact under test here — we assert on the committed text, not a copy):

  test_a: .github/workflows/ci.yml must be git-TRACKED and must not be
    matched by any ignore rule. The .gitignore once shipped a
    '.github/workflows/' line (staged out of main so the owner could push it
    with a workflow-scoped token); after the merge that line would silently
    swallow any FUTURE workflow file. git check-ignore skips tracked paths
    unless --no-index is given, so we pass --no-index: the check goes RED the
    moment the ignore line returns, tracked file or not.

  test_b: the gate-3 npm install step lists ONLY exact semver pins — no ^ ~
    ranges, no bare majors — matching the file's own triple-pin doctrine
    (bd asset sha256 + checksums + version stamp, HERMES_PIN commit).

Run: python3 tests/test_ci_hygiene.py
"""
import os
import re
import subprocess
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
LANE = os.path.dirname(HERE)
CI_YML = os.path.join(".github", "workflows", "ci.yml")


def git(*args):
    return subprocess.run(
        ["git", *args], cwd=LANE, capture_output=True, text=True
    )


class CiWorkflowHygiene(unittest.TestCase):
    def test_a_ci_workflow_is_tracked_and_not_ignored(self):
        # Tracked: exits 0 only while the file is in the index.
        tracked = git("ls-files", "--error-unmatch", CI_YML)
        self.assertEqual(
            tracked.returncode, 0,
            f"{CI_YML} must stay git-tracked: {tracked.stderr.strip()}",
        )
        # Not ignored: --no-index so the answer reflects the ignore RULES even
        # while the path is tracked (default check-ignore behaviour hides
        # tracked paths and would mask a regression of the ignore line).
        ignored = git("check-ignore", "--no-index", "-v", CI_YML)
        self.assertEqual(
            ignored.returncode, 1,
            f"{CI_YML} must not match any gitignore rule; "
            f"check-ignore --no-index said: {ignored.stdout.strip()}",
        )

    def test_b_gate3_npm_install_uses_exact_pins(self):
        path = os.path.join(LANE, CI_YML)
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        install_lines = [
            line.strip()
            for line in text.splitlines()
            if re.search(r"\bnpm install\b", line)
        ]
        self.assertTrue(install_lines, "no npm install step found in ci.yml")
        exact = re.compile(r"^(@[A-Za-z0-9._-]+/)?[A-Za-z0-9._-]+@"
                           r"\d+\.\d+\.\d+$")
        for line in install_lines:
            cmd = line.split("npm install", 1)[1]
            for token in cmd.split():
                if token.startswith("-"):
                    continue  # flags (--no-audit etc.) are not package specs
                self.assertTrue(
                    exact.match(token),
                    f"floating version spec {token!r} in npm install step; "
                    f"the file's triple-pin doctrine demands exact semver "
                    f"(no ^ ~ ranges, no bare majors): {line!r}",
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
