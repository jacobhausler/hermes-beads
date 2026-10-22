#!/usr/bin/env python3
"""P1 observe-mode contract preflight (NEXTGENCI (d) S7; (e) P1 "preflight leg
live"). OBSERVE: runs the CONTRACT's own preflight script — fetched at the
pinned tag, never a copy — against this checkout, reports the verdict, and
NEVER reds: the job exists to be read, and the existing required gates are
untouched. No statuses are written, the caller workflow is never invoked
(that flip ships at P2), so zero behavior change.

What it does:
  1. reads the `# ci-contract: estate-ci@vN` pin line from this repo's
     observe workflow and the ref of the declared (observe-skipped at P1)
     caller job; they must agree;
  2. resolves the tag via the refs API (gh api; git ls-remote fallback — a
     remote pin must be a tag);
  3. downloads .github/workflows/estate-ci.yml AT THAT TAG and extracts the
     preflight step's embedded script verbatim (stdlib text only);
  4. executes it with the parameter block passed in via IN_* env, repo root
     as cwd (it scans .github/workflows/*, tests/timing.json, the pin, the
     knobs — exactly as the contract's preflight job would);
  5. prints `PREFLIGHT OBSERVE rc=...` + the script's own named markers
     (PREFLIGHT OK / PREFLIGHT FAIL <reason>) into stdout and the step
     summary; exits 0 whatever the preflight says (observe law).

Stdlib only. Usage (from the repo root, IN_* env set by the job):
  python3 scripts/ci_preflight_observe.py
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

CONTRACT_REPO = 'jacobhausler/estate-ci'
CONTRACT_FILE = '.github/workflows/estate-ci.yml'
SELF_YML = Path('.github') / 'workflows' / 'estate-ci-observe.yml'
PIN_RE = re.compile(r'^[ \t]*#[ \t]*ci-contract:[ \t]*estate-ci@(v\d+(?:\.\d+){0,2})[ \t]*$', re.M)
USES_RE = re.compile(r'uses:\s*jacobhausler/estate-ci/\.github/workflows/estate-ci\.yml@(\S+)')
IN_VARS = ('IN_LEGS', 'IN_COMPONENTS', 'IN_SHARDS', 'IN_LANE_CAP_S',
           'IN_BUDGET', 'IN_PATHS', 'IN_ARTIFACT_GATE')


def report(lines):
    for line in lines:
        print(line, flush=True)
    summary = os.environ.get('GITHUB_STEP_SUMMARY')
    if summary:
        with open(summary, 'a') as fh:
            fh.write('\n'.join(lines) + '\n')


def resolve_tag(tag):
    """Tag must exist as a git tag on the contract repo (the same law the
    contract's preflight enforces on remote uses:)."""
    try:
        p = subprocess.run(['gh', 'api', f'repos/{CONTRACT_REPO}/git/ref/tags/{tag}'],
                           capture_output=True, text=True)
        if p.returncode == 0:
            obj = json.loads(p.stdout)
            return f"tag-object {obj.get('object', {}).get('sha', '?')}"
    except FileNotFoundError:
        pass
    p = subprocess.run(['git', 'ls-remote', f'https://github.com/{CONTRACT_REPO}',
                        f'refs/tags/{tag}'], capture_output=True, text=True)
    if p.returncode == 0 and p.stdout.strip():
        return 'ls-remote ok'
    return None


def extract_preflight_script(yml_text):
    """Stdlib extraction of the preflight render step's `run: |` block, then
    the <<'PY' heredoc inside it. Verbatim contract code, no re-typing."""
    lines = yml_text.splitlines()
    try:
        start = next(i for i, l in enumerate(lines) if l.strip() == 'id: render')
    except StopIteration:
        raise RuntimeError('id: render step not found in contract yml')
    run_indent = run_at = None
    for i in range(start, min(start + 15, len(lines))):
        stripped = lines[i].lstrip(' ')
        if stripped.startswith('run: |'):
            run_indent, run_at = len(lines[i]) - len(stripped), i
            break
    if run_at is None or run_indent is None:
        raise RuntimeError('run: | block not found after id: render')
    block = []
    for i in range(run_at + 1, len(lines)):
        line = lines[i]
        if line.strip() and (len(line) - len(line.lstrip(' '))) <= run_indent:
            break
        block.append(line)
    open_at = next((i for i, l in enumerate(block) if "<<'PY'" in l), None)
    if open_at is None:
        raise RuntimeError("heredoc <<'PY' opener not found")
    body, indent = [], None
    for line in block[open_at + 1:]:
        if line.strip() == 'PY':
            break
        cur = len(line) - len(line.lstrip(' ')) if line.strip() else None
        if line.strip() and indent is None:
            indent = cur
        body.append(line[indent:] if (indent is not None and line.strip()) else '')
    if not body or indent is None:
        raise RuntimeError('heredoc body empty')
    return '\n'.join(body) + '\n'


def main():
    lines = []
    try:
        if not SELF_YML.is_file():
            raise RuntimeError(f'{SELF_YML} not found from cwd')
        yml = SELF_YML.read_text()
        pin = PIN_RE.search(yml)
        if not pin:
            raise RuntimeError('no `# ci-contract: estate-ci@vN` pin line in observe yml')
        pin = pin.group(1)
        uses = USES_RE.search(yml)
        if not uses:
            raise RuntimeError('no declared contract caller (uses: ...@ref) in observe yml')
        if uses.group(1) != pin:
            raise RuntimeError(f'pin line {pin} disagrees with uses ref {uses.group(1)}')
        ref_state = resolve_tag(pin)
        if ref_state is None:
            raise RuntimeError(f'ref {pin} does not resolve to a tag on {CONTRACT_REPO}')
        url = f'https://raw.githubusercontent.com/{CONTRACT_REPO}/{pin}/{CONTRACT_FILE}'
        with urllib.request.urlopen(url, timeout=30) as resp:
            contract_yml = resp.read().decode('utf-8')
        script = extract_preflight_script(contract_yml)
        missing = [v for v in IN_VARS if v not in os.environ]
        if missing:
            raise RuntimeError(f'missing IN_* parameter env: {missing}')
        with tempfile.TemporaryDirectory() as td:
            script_path = Path(td) / 'estate_ci_preflight.py'
            script_path.write_text(script)
            env = dict(os.environ, GITHUB_OUTPUT=str(Path(td) / 'github_output'))
            proc = subprocess.run([sys.executable, str(script_path)],
                                  env=env, capture_output=True, text=True)
        rc = proc.returncode
        out = (proc.stdout + proc.stderr)
        markers = [l.strip() for l in out.splitlines()
                   if 'PREFLIGHT OK' in l or 'PREFLIGHT FAIL' in l]
        lines.append(f'PREFLIGHT OBSERVE pin={pin} ref={ref_state} rc={rc} '
                     f'markers={" | ".join(markers) if markers else "(none printed)"}')
        if rc != 0:
            # honest observation, not a red: the named cause (and the P2 to-do
            # it implies) belongs in the summary, not on the merge path.
            lines.append('::warning::P1 observe: contract preflight is RED for the '
                         'reason named above; it blocks nothing at P1 (not required; '
                         'legacy gates untouched)')
        tail = '\n'.join(out.splitlines()[-25:])
        lines.append('<details><summary>contract preflight output (verbatim tail)</summary>\n\n'
                     '```\n' + tail + '\n```\n\n</details>')
    except Exception as exc:  # observe law: report, never red
        lines.append(f'PREFLIGHT OBSERVE ERROR {type(exc).__name__}: {exc}')
        lines.append('::warning::P1 observe: preflight-observe could not complete; '
                     'blocks nothing (not required)')
    report(lines)
    sys.exit(0)


if __name__ == '__main__':
    main()
