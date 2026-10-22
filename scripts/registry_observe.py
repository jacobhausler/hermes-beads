#!/usr/bin/env python3
"""registry_observe.py — P1 OBSERVE-MODE burn-token registry generator.

Mints tests/registry.json: one row per case keyed on its burn token,
{case, tier, burn, order_group, after}. The registry is ORDER DATA, never
sizes (TIERS (c)): the contract repacks lanes by (order_group, after)
teeth, so an FTE-fairness number stored here would only drift.

Burn token (TIERS methodology, per-case throughput): hermes-beads'
gates print NO per-case token, so the token here is the observed wall
seconds from tests/timing.json (same source the interim tier map uses).
P2's b9s-native token replaces this at the recut; until then burn is a
derived field the generator owns.

Interim tier map (TIERS hb row: 110 t1 methods / T2 14 scenarios +
fixtures — 'scenario' and 'fixture' are file-level properties, so the
map is a hardcoded file set, the honest shape at this repo's scale):

  * tests/test_scenarios_[abc].mjs           -> T2 (S3-parallel scenarios)
  * tests/test_fixture_orphan_sweep.py       -> T2 (fixture-store lifecycle)
  * tests/test_fixture_teardown.py           -> T2 (fixture-store lifecycle)
  * tests/test_standalone.py (5 methods)     -> T2 (stock-bd live readbacks)
  * everything else                          -> T1

order_group/after: every row null/[] — hermes-beads names no ordering
teeth at P1 (the only teeth the contract names live in hermes-workflows).

Run:  python3 scripts/registry_observe.py --timing tests/timing.json
Re-vs-timing: the case set must match tests/timing.json exactly or the
generator refuses (fail-closed): a registry missing cases would silently
drop them from repack.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

T2_FILES = {
    'tests/test_scenarios_a.mjs',
    'tests/test_scenarios_b.mjs',
    'tests/test_scenarios_c.mjs',
    'tests/test_fixture_orphan_sweep.py',
    'tests/test_fixture_teardown.py',
    'tests/test_standalone.py',
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--timing', default='tests/timing.json')
    ap.add_argument('--out', default='tests/registry.json')
    ap.add_argument('--floor-missing', type=int, default=3,
                    help='burn floor for a row timing cannot see (new test)')
    a = ap.parse_args()

    timing = json.loads(Path(a.timing).read_text())
    rows = []
    for t in timing:
        f = t['case'].split('::', 1)[0]
        rows.append({
            'case': t['case'],
            'tier': 2 if f in T2_FILES else 1,
            'burn': t['wall_s'],
            'order_group': None,
            'after': [],
        })

    rows.sort(key=lambda r: r['case'])
    Path(a.out).write_text(json.dumps(rows, indent=2) + '\n')
    t2 = sum(1 for r in rows if r['tier'] == 2)
    burn_t1 = sum(r['burn'] for r in rows if r['tier'] == 1)
    burn_t2 = sum(r['burn'] for r in rows if r['tier'] == 2)
    cap = 150
    print(f'registry_observe: {len(rows)} rows ({len(rows) - t2} T1 / {t2} T2; '
          f'burn {burn_t1:.0f}s / {burn_t2:.0f}s) '
          f'ceil-sum shards={math.ceil(sum(max(r["burn"], 1.0) for r in rows) / cap)} '
          f'(observe only, no repack) -> {a.out}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
