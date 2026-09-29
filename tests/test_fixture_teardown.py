"""Regression (hbl-pnu.4.7): fixture-runner teardown must delete ONLY the
stores this process created — never foreign dirs in the shared fixture root.

Red state: teardown sweeps all of FIXTURE_ROOT, so a concurrent suite's
in-flight store (mnt-*) and its seeded content vanish.
"""
import os
import shutil
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, os.path.join(HERE, "fixtures", "e2e-runtime"))

import bootstrap  # noqa: E402


class OwnDirCleanupOnly(unittest.TestCase):
    def test_teardown_removes_owned_and_spares_foreign(self):
        root = bootstrap.FIXTURE_ROOT
        os.makedirs(root, exist_ok=True)
        owned = bootstrap.make_store(prefix="ownertest")
        # a foreign in-flight store: seeded content, created by another proc
        foreign = bootstrap.make_store(prefix="foreign")
        self._seed_marker(foreign)
        # simulate a different process: clear the ownership registry
        registry = getattr(bootstrap, "RUN_STORES", None)
        self.assertIsNotNone(registry,
                             "bootstrap must track stores it created (RUN_STORES)")
        registry.clear()
        registry.add(owned)

        bootstrap.cleanup_run_stores()

        self.assertFalse(os.path.exists(owned), "owned store must be removed")
        self.assertTrue(os.path.isdir(foreign),
                        "foreign store was wiped by another run's teardown")
        self.assertTrue(os.listdir(foreign),
                        "foreign store contents were wiped")

    def _seed_marker(self, store):
        p = subprocess.run([bootstrap.BD_BIN, "-C", store, "--actor", "t",
                            "create", "foreign marker", "--json"],
                           capture_output=True, text=True)
        assert p.returncode == 0, p.stderr

    def tearDown(self):
        for name in ("ownertest", "foreign"):
            for d in os.listdir(bootstrap.FIXTURE_ROOT):
                if d.startswith(name + "-"):
                    shutil.rmtree(os.path.join(bootstrap.FIXTURE_ROOT, d),
                                  ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
