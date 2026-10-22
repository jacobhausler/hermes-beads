#!/usr/bin/env python3
"""Timing-manifest generator, OBSERVE mode (NEXTGEN-CI.md (c) manifest split,
S3a + S3 addendum; (e) P1) — hermes-beads flavor.

Parses THIS repo's own CI log (`gh run view --log`), canonical leg only
(default `floor` — TIERS precedent), reading what the gate actually prints:

  * Gate 1 (`python3 tests/test_standalone.py`) and Gate 2 (the rc-propagating
    per-file loop with `== tests/x.py` headers): unittest completion lines —
    same-line `NAME (mod.Class) ... ok`, bare-pending + docstring form, and
    the standalone suite's interleaved-stderr form (pending line, noise, bare
    `ok`). Per-method wall = completion-delta on the leg's log clock; the
    first completion of a section anchors at the section's own start
    (`##[group]Run` header / command echo / `==` header). A case that runs in
    BOTH Gate 1 and the Gate 2 loop (test_standalone.py) keeps its LATEST
    (Gate 2) wall — the loop is the suite; Gate 1 is the parity smoke.
  * Gate 3 (`node --test`, non-git cwd): spec-reporter lines with EXPLICIT
    durations — `NAME (Nms)` under ✔/✖ plus the skipped ` # comment` form;
    file attribution via a name->file map built from the mjs sources (all
    declarations here are `test("...")` literals).

Validation (fail-closed): Gate-2 completion events must equal Σ `Ran N
test(s)`; node rows must equal `ℹ tests N`. Rows are EXACTLY {case, wall_s,
timeout_s}; timeout = ceil(wall × 3).

Generator law (S3 addendum — kills timeout self-inflation): the generator may
only DECREASE a weight; raising one requires --cite-run <Tier-2 run id> whose
uploaded ledger shows the new p95 (the citation lands in the commit message,
never the row). New cases may be added; drops are reported (deletion law:
burn token in the PR body).

Usage:
  python3 scripts/timing_observe.py [--run-id N] [--log FILE] [--leg floor]
      [--repo owner/repo] [--workflow ci]
      [--existing tests/timing.json] [--out tests/timing.json] [--cite-run ID]
"""
import argparse
import datetime
import json
import math
import re
import subprocess
import sys
from pathlib import Path

def _derive_repo():
    """owner/repo of the `origin` remote — the generator must carry NO
    hardcoded repo handle: the publish-scrub forbids shipping one, and a
    derived default survives forks and renames (fail-closed if absent)."""
    import subprocess
    try:
        url = subprocess.run(['git', 'remote', 'get-url', 'origin'],
                             capture_output=True, text=True, check=True,
                             cwd=Path(__file__).resolve().parent.parent
                             ).stdout.strip()
    except Exception:
        return None
    m = re.search(r'github\.com[:/](?P<r>[\w.-]+/[\w.-]+?)(?:\.git)?/?$', url)
    return m.group('r') if m else None


_REPO_DEFAULT = None
PREFIX_RE = re.compile(r'^(?P<leg>leg/(?P<legname>[\w-]+))\t(?P<step>[^\t]*)\t'
                       r'(?P<ts>\ufeff?\d{4}-\d{2}-\d{2}T[\d:.]+)Z (?P<msg>.*)$')
# gh --log may carry ANSI codes as real escapes (`\x1b[36;1m`) OR as caret
# text (`^[[36;1m` — ESC rendered as the two characters ^ + [), so the caret
# form carries TWO opening brackets.
ANSI = re.compile(r'(?:\x1b\[|\^\[\[)[0-9;]*m')
TESTLINE = re.compile(r'^(?P<m>test_\w+) \((?P<mod>[\w.]+?)(?:\.(?P=m))?\)(?P<rest>.*)$')
VERDICT_TAIL = re.compile(r'\s*\.\.\.\s*(?:/home/\S.*:\d+: \S.*\n?)?(?P<v>ok|FAIL|ERROR|skipped\b.*)$')
VERDICT_BARE = re.compile(r'^(?P<v>ok|FAIL|ERROR|skipped(?: .*)?)$')
RAN_RE = re.compile(r'^Ran (\d+) tests?')
NODE_MS = re.compile(r'^(?P<mark>[✔✖⚠﹣\-])?\s*(?P<name>.+?) \((?P<ms>[\d.]+)ms\)(?: #.*)?$')
NODE_INFO = re.compile(r'^ℹ (?P<k>tests|fail) (?P<n>\d+)')
TEST_DECL = re.compile(r'test\(\s*"([^"]+)"')


def ts_s(t):
    return datetime.datetime.strptime(t.lstrip('\ufeff')[:26], '%Y-%m-%dT%H:%M:%S.%f').timestamp()


def sh(args):
    p = subprocess.run(args, capture_output=True, text=True)
    if p.returncode != 0:
        sys.exit(f'timing_observe: command failed {args}: {p.stderr.strip()[:400]}')
    return p.stdout


def resolve_latest_green(repo, workflow):
    rows = json.loads(sh(['gh', 'run', 'list', '-R', repo, '-b', 'main',
                          '--workflow', workflow, '--status', 'success',
                          '--event', 'push', '-L', '1',
                          '--json', 'databaseId,conclusion']))
    if rows[0].get('conclusion') != 'success':
        sys.exit('timing_observe: resolved run not success — refusing')
    return int(rows[0]['databaseId'])


def leg_lines(text, leg):
    out = []
    for line in text.splitlines():
        m = PREFIX_RE.match(line)
        if m and m.group('legname') == leg:
            out.append((ts_s(m.group('ts')), ANSI.sub('', m.group('msg'))))
    return out


def build_name_map(root):
    names = {}
    for f in sorted((root / 'tests').glob('test_*.mjs')):
        for m in TEST_DECL.finditer(f.read_text(errors='replace')):
            if m.group(1) in names and names[m.group(1)] != f.as_posix():
                print(f'timing_observe: NOTE node test name in two files: {m.group(1)!r}')
            names.setdefault(m.group(1), f.as_posix())
    return names


def parse(text, leg, root):
    lines = leg_lines(text, leg)
    if not lines:
        sys.exit(f'timing_observe: no lines for leg/{leg} — wrong log or leg?')
    walls, node_total = {}, None
    ran_g1 = py_g1 = ran_g2 = py_g2 = 0
    cur_file, pending = None, None
    prev = None
    section = 'pre'
    name_map = build_name_map(root)
    unmapped = []
    for ts, msg in lines:
        stripped = msg.strip()
        # section transitions: GH step group headers, else raw echoes/`==`
        if stripped.startswith('##[group]Run '):
            cmd = stripped[len('##[group]Run '):].strip()
            if cmd == 'python3 tests/test_standalone.py':
                section, cur_file, prev = 'gate1', 'tests/test_standalone.py', ts
                continue
            if 'node --test' in cmd:
                section, prev = 'gate3', ts
                continue
            if 'plugins validate' in cmd:
                section = 'gate4'
                continue
        if section == 'pre':
            if stripped == 'python3 tests/test_standalone.py':
                section, cur_file, prev = 'gate1', 'tests/test_standalone.py', ts
            continue
        # Gate 3 runs inside a multi-line step whose `Run` header echoes only
        # the first line (`set -euo pipefail`) — transition on the command
        # echo itself instead.
        if section in ('gate1', 'gate2') and stripped.startswith('node --test tests/'):
            section, prev = 'gate3', ts
            continue
        if stripped.startswith('== tests/'):
            if pending:
                print('timing_observe: NOTE pending unittest case lost at file '
                      f'boundary: {pending} (in {cur_file})')
                pending = None
            if section == 'pre':  # repo without a Gate-1 standalone step
                section, cur_file, prev = 'gate2', stripped[3:].strip(), ts
                continue
            section, cur_file = 'gate2', stripped[3:].strip()
            prev = ts
            continue
        if section == 'gate4':
            continue
        m = NODE_INFO.match(stripped)
        if section == 'gate3' and m:
            if m.group('k') == 'tests':
                node_total = int(m.group('n'))
            continue
        if section == 'gate3':
            nm = NODE_MS.match(stripped)
            if nm and nm.group('mark') in ('✔', '✖', '⚠', '﹣', '-', None) \
                    and (nm.group('name') or '').strip():
                name = nm.group('name')
                f = name_map.get(name)
                if f is None:
                    f = 'tests/?'
                    unmapped.append(name)
                walls[f'{f}::{name}'] = round(float(nm.group('ms')) / 1000.0, 4)
            continue
        # python sections (gate1/gate2): per-section Ran lines cross-check
        # each section's own completion count — never a pooled sum.
        r = RAN_RE.match(stripped)
        if r:
            if section == 'gate2':
                ran_g2 += int(r.group(1))
            elif section == 'gate1':
                ran_g1 += int(r.group(1))
            continue
        m = TESTLINE.match(stripped)
        if m:
            rest = m.group('rest')
            vt = VERDICT_TAIL.search(rest)
            if vt and rest.strip().startswith('...'):
                walls[f'{cur_file}::{m.group("m")}'] = round(ts - prev, 1)
                if section == 'gate2':
                    py_g2 += 1
                else:
                    py_g1 += 1
                prev, pending = ts, None
            else:
                pending = m.group('m')
            continue
        if pending:
            vb = VERDICT_BARE.match(stripped)
            vt = VERDICT_TAIL.search(stripped)
            if vb or vt:
                walls[f'{cur_file}::{pending}'] = round(ts - prev, 1)
                if section == 'gate2':
                    py_g2 += 1
                else:
                    py_g1 += 1
                prev, pending = ts, None
                continue
    return (walls, (ran_g1, py_g1, ran_g2, py_g2), node_total, unmapped)


def apply_law(parsed, existing, cite_run):
    old = {}
    if existing.exists():
        old = {r['case']: r for r in json.loads(existing.read_text())}
    report, out, clamped = [], [], []
    for case, wall in sorted(parsed.items()):
        tmo = math.ceil(wall * 3)
        if case in old:
            ow, ot = float(old[case]['wall_s']), int(old[case]['timeout_s'])
            if wall > ow and not cite_run:
                out.append({'case': case, 'wall_s': ow, 'timeout_s': ot})
                clamped.append(f'{case}: parsed {wall} > committed {ow}')
                continue
            if wall > ow:
                report.append(f'RAISE cited run={cite_run}: {case} {ow} -> {wall}')
        out.append({'case': case, 'wall_s': round(wall, 4), 'timeout_s': tmo})
    for case in sorted(set(old) - set(parsed)):
        report.append(f'DROP (not in this run — deletion law for burn rows): {case}')
    if clamped:
        report.append(f'RAISE-CLAMPED {len(clamped)} row(s) (decrease-only; raise '
                      'needs --cite-run <Tier-2 run id>, citation in the commit '
                      'message):')
        report.extend('  ' + c for c in clamped)
    return out, report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--run-id', type=int)
    ap.add_argument('--log')
    ap.add_argument('--leg', default='floor')
    ap.add_argument('--repo', default=_derive_repo(),
                    help='owner/repo (default: git origin remote)')
    ap.add_argument('--workflow', default='ci')
    ap.add_argument('--existing', default='tests/timing.json')
    ap.add_argument('--out', default='tests/timing.json')
    ap.add_argument('--cite-run')
    a = ap.parse_args()

    run_id = a.run_id
    if a.log:
        text = Path(a.log).read_text()
    else:
        if run_id is None:
            run_id = resolve_latest_green(a.repo, a.workflow)
        text = sh(['gh', 'run', 'view', '-R', a.repo, '--log', str(run_id)])
    if run_id is None:
        run_id = -1

    walls, (ran_g1, py_g1, ran_g2, py_g2), node_total, unmapped = parse(text, a.leg, Path('.'))
    py_rows = {k: v for k, v in walls.items() if '.mjs::' not in k}
    node_rows = [k for k in walls if '.mjs::' in k]
    if ran_g2 and py_g2 != ran_g2:
        sys.exit(f'timing_observe: Gate-2 completion events {py_g2} != sum(Ran) '
                 f'{ran_g2} — refusing (fail-closed)')
    if ran_g1 and py_g1 != ran_g1:
        sys.exit(f'timing_observe: Gate-1 completion events {py_g1} != sum(Ran) '
                 f'{ran_g1} — refusing (fail-closed)')
    if node_total is not None and len(node_rows) != node_total:
        sys.exit(f'timing_observe: node coverage {len(node_rows)} != '
                 f'ℹ tests {node_total} — refusing (fail-closed)')
    if unmapped:
        print(f'timing_observe: WARNING {len(unmapped)} node test names unmapped '
              f'from sources (rows carry tests/?): {unmapped[:5]}')

    rows, report = apply_law(walls, Path(a.existing), a.cite_run)
    Path(a.out).write_text(json.dumps(rows, indent=2) + '\n')
    total = sum(r['wall_s'] for r in rows)
    print(f'timing_observe: run={run_id} leg={a.leg} rows={len(rows)} '
          f'(py {len(py_rows)} unique; gate2 {py_g2}/{ran_g2} events/Ran, gate1 '
          f'{py_g1}/{ran_g1}; node {len(node_rows)}/{node_total}) '
          f'sum_wall={total:.1f}s -> {a.out}')
    cap = 150
    print(f'timing_observe: at lane_cap_s={cap} the contract would compute '
          f'shards=ceil({total:.1f}/{cap})={math.ceil(total / cap)} (observe only, '
          'no repack; py legs re-pack, node leg runs once by D11)')
    for line in report:
        print('timing_observe:', line)
    if run_id > 0:
        print('timing_observe: cite in commit/PR body: '
              f'https://github.com/{a.repo}/actions/runs/{run_id}')


if __name__ == '__main__':
    main()
