"""Regression (part 2): make_store sweeps ORPHANED stores
(older than the stale threshold) and nothing else — fresh foreign stores
(concurrent runs) are never touched."""
import json
import os
import subprocess
import sys
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, os.path.join(HERE, "fixtures", "e2e-runtime"))

import bootstrap  # noqa: E402


class OrphanSweep(unittest.TestCase):
    def test_make_store_sweeps_only_stale_known_prefix_dirs(self):
        root = bootstrap.FIXTURE_ROOT
        os.makedirs(root, exist_ok=True)
        # an orphaned store from a crashed run (aged + known prefix)
        orphan = bootstrap.make_store(prefix="mnt")
        bootstrap.RUN_STORES.discard(orphan)  # pretend process is gone
        old = time.time() - bootstrap.STALE_SECONDS - 120
        os.utime(orphan, (old, old))
        for child in os.scandir(orphan):  # age the whole store, not just root
            try:
                os.utime(child.path, (old, old))
            except OSError:
                pass
        # fresh stores: foreign (unknown prefix) + recent known prefix
        fresh_foreign = bootstrap.make_store(prefix="fresher")
        bootstrap.RUN_STORES.discard(fresh_foreign)
        recent = bootstrap.make_store(prefix="mnt")
        bootstrap.RUN_STORES.discard(recent)
        fresh_foreign_mtime = os.stat(fresh_foreign).st_mtime

        new_store = bootstrap.make_store(prefix="sweepcheck")

        self.assertFalse(os.path.exists(orphan),
                         "stale orphaned store must be swept")
        self.assertTrue(os.path.isdir(fresh_foreign),
                        "fresh foreign store must survive")
        self.assertEqual(os.stat(fresh_foreign).st_mtime, fresh_foreign_mtime,
                         "make_store's own git/bd steps must not run inside "
                         "a foreign store (cwd contamination)")
        self.assertTrue(os.path.isdir(recent), "recent store must survive")
        self.assertTrue(os.path.isdir(new_store))

        for d in (orphan, fresh_foreign, recent, new_store):
            bootstrap.RUN_STORES.add(d)
        bootstrap.cleanup_run_stores()


if __name__ == "__main__":
    unittest.main(verbosity=2)
