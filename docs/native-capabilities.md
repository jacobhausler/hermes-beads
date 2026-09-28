# Native capability evidence — local N0 qualification

Observed bd 1.3.0 (f45b249ce), not a minimum-version certification.
The parent reran 13 tests successfully. Below are actual argv/output receipts from isolated stores, each with its own git root. Raw journal: `../../reports/recovery-native-probes.json`.

Limits: actor/status guards are not content CAS; claim success is not eligibility; no multiwriter or unattended execution qualification. `ready` has a count limit, not a proven max-rows guard; missing rows remain unknown. Exit-0 failure-envelope rejection uses an injected process result, not a native successful failure response. Plugin validator passed all checks locally; published Hermes floor remains unqualified.

## 0

```text
cwd: /home/hermes/.hermes/work/beads-lab/build/tests/.fixtures/tmpht0eva1l
argv: /home/hermes/.hermes/work/beads-lab/bin/bd init --prefix tst
exit: 0
stdout:
  Repository ID: 28119db7
  Clone ID: 0f77af50fea94529
  Hooks installed to: .beads/hooks/
  ✓ Created AGENTS.md with agent instructions
Installing Claude hooks for this project...
✓ Registered SessionStart hook
Installing Claude Code integration...
✓ Created new CLAUDE.md with beads integration

✓ Claude Code integration installed
  File: /home/hermes/.hermes/work/beads-lab/build/tests/.fixtures/tmpht0eva1l/CLAUDE.md
No additional configuration needed!

✓ Claude Code integration installed
  Settings: /home/hermes/.hermes/work/beads-lab/build/tests/.fixtures/tmpht0eva1l/.claude/settings.json

Restart Claude Code for changes to take effect.
Installing Beads agent skill...
✓ Beads agent skill installed
  Skill: /home/hermes/.hermes/work/beads-lab/build/tests/.fixtures/tmpht0eva1l/.agents/skills/beads/SKILL.md
Installing Codex native hooks for this project...
✓ Codex native hooks installed
Installing Codex instructions for this project...
✓ Codex instructions installed
  File: /home/hermes/.hermes/work/beads-lab/build/tests/.fixtures/tmpht0eva1l/AGENTS.md
Restart Codex if it is already running.
Installing Beads agent skill...
✓ Beads agent skill installed
  Skill: .agents/skills/beads/SKILL.md
✓ Cursor integration installed (rules + skill + hooks)

✓ bd initialized successfully!

  Backend: dolt
  Mode: embedded
  Database: tst
  Issue prefix: tst
  Issues will be named: tst-<hash> (e.g., tst-a3f2dd)

Run bd quickstart to get started.


stderr:
Warning: failed to commit beads files: exit status 128

⚠ No Dolt remote configured
  Issues are stored in local Dolt. .beads/issues.jsonl is an export,
  not cross-machine sync or the source of truth.
  To enable durable sync, add a git origin and then run:
    bd dolt push

```

## update --claim0

```text
cwd: /home/hermes/.hermes/work/beads-lab/build/tests/.fixtures/tmp8ddmfk82
argv: /home/hermes/.hermes/work/beads-lab/bin/bd --actor owner-60585d1c update tst-db1 --claim --json
exit: 0
stdout:
[
  {
    "id": "tst-db1",
    "title": "contended",
    "status": "in_progress",
    "priority": 2,
    "issue_type": "task",
    "assignee": "owner-60585d1c",
    "created_at": "2026-09-28T01:38:07Z",
    "created_by": "unknown",
    "updated_at": "2026-09-28T01:38:08Z",
    "started_at": "2026-09-28T01:38:08Z",
    "lease_expires_at": "2026-09-28T01:43:08Z",
    "heartbeat_at": "2026-09-28T01:38:08Z"
  }
]

stderr:

```

## --readonly show0

```text
cwd: /home/hermes/.hermes/work/beads-lab/build/tests/.fixtures/tmp8ddmfk82
argv: /home/hermes/.hermes/work/beads-lab/bin/bd --readonly show tst-db1 --json
exit: 0
stdout:
[
  {
    "id": "tst-db1",
    "title": "contended",
    "status": "in_progress",
    "priority": 2,
    "issue_type": "task",
    "assignee": "owner-60585d1c",
    "created_at": "2026-09-28T01:38:07Z",
    "created_by": "unknown",
    "updated_at": "2026-09-28T01:38:08Z",
    "started_at": "2026-09-28T01:38:08Z",
    "lease_expires_at": "2026-09-28T01:43:08Z",
    "heartbeat_at": "2026-09-28T01:38:08Z",
    "dependent_count": 0,
    "dependency_count": 0,
    "comment_count": 0,
    "revision": "2259116202305996098"
  }
]

stderr:

```

## update --claim1

```text
cwd: /home/hermes/.hermes/work/beads-lab/build/tests/.fixtures/tmp8ddmfk82
argv: /home/hermes/.hermes/work/beads-lab/bin/bd --actor thief-4821a8fc update tst-db1 --claim --json
exit: 1
stdout:

stderr:
Error updating tst-db1: issue already claimed by owner-60585d1c
{"error":"1 of 1 issues failed to update","failed":[{"id":"tst-db1","error":"updating issue: issue already claimed by owner-60585d1c"}],"schema_version":1}

```

## heartbeat0

```text
cwd: /home/hermes/.hermes/work/beads-lab/build/tests/.fixtures/tmp6t28qaiw
argv: /home/hermes/.hermes/work/beads-lab/bin/bd --actor lab-native-test-69f155b8 heartbeat tst-3ax --json
exit: 0
stdout:
{
  "id": "tst-3ax",
  "owner": "lab-native-test-69f155b8",
  "schema_version": 1,
  "status": "heartbeat"
}

stderr:

```

## update --if-assignee13

```text
cwd: /home/hermes/.hermes/work/beads-lab/build/tests/.fixtures/tmporw1pktn
argv: /home/hermes/.hermes/work/beads-lab/bin/bd --actor tester update tst-d3v --if-assignee ghost-actor --append-notes 'should not land' --json
exit: 13
stdout:

stderr:
Error updating tst-d3v: assignee mismatch: tst-d3v is held by "", expected "ghost-actor"
{"error":"1 of 1 issues failed to update","failed":[{"id":"tst-d3v","error":"updating issue: assignee mismatch: tst-d3v is held by \"\", expected \"ghost-actor\"","guard_mismatch":true}],"schema_version":1}

```

## --readonly --max-rows2

```text
cwd: /home/hermes/.hermes/work/beads-lab/build/tests/.fixtures/tmpsqly5a_c
argv: /home/hermes/.hermes/work/beads-lab/bin/bd --readonly list --json --max-rows 1
exit: 2
stdout:

stderr:
Error: too many rows: 2 found, --max-rows=1 exceeded.
       Refine the query (add filters, set --limit), or raise the cap with
       --max-rows N or BEADS_MAX_ROWS=N.

```

## --readonly1

```text
cwd: /home/hermes/.hermes/work/beads-lab/build/tests/.fixtures/tmpeltpwop8
argv: /home/hermes/.hermes/work/beads-lab/bin/bd --readonly create 'sneaky write'
exit: 1
stdout:

stderr:
Error: operation 'create' is not allowed in read-only mode

```

## --readonly ready --max-rows0

```text
cwd: /home/hermes/.hermes/work/beads-lab/build/tests/.fixtures/tmpz8kn62u9
argv: /home/hermes/.hermes/work/beads-lab/bin/bd --readonly ready --json --exclude-type=epic -n 100 --label no-such-label --max-rows 1000
exit: 0
stdout:
[]

stderr:

```

## --readonly info0

```text
cwd: /home/hermes/.hermes/work/beads-lab/build/tests/.fixtures/tmpienokw5g
argv: /home/hermes/.hermes/work/beads-lab/bin/bd --readonly info --json
exit: 0
stdout:
{
  "config": {
    "auto_compact_enabled": "false",
    "compact_batch_size": "50",
    "compact_parallel_workers": "5",
    "compact_tier1_days": "30",
    "compact_tier1_dep_levels": "2",
    "compact_tier2_commits": "100",
    "compact_tier2_days": "90",
    "compact_tier2_dep_levels": "5",
    "compaction_enabled": "false",
    "issue_prefix": "tst"
  },
  "database_path": "/home/hermes/.hermes/work/beads-lab/build/tests/.fixtures/tmpienokw5g/.beads/embeddeddolt",
  "issue_count": 0,
  "mode": "direct",
  "schema_version": 1
}

stderr:

```

## version0

```text
cwd: /home/hermes/.hermes/work/beads-lab/build/tests/.fixtures/tmp50cnyve5
argv: /home/hermes/.hermes/work/beads-lab/bin/bd version
exit: 0
stdout:
bd version 1.3.0 (f45b249ce: HEAD@f45b249ce6b4)

stderr:

```
