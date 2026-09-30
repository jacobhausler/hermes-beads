// tests/test_b9s_drift.mjs — council S3 drift guard (2026-09-30): the pinned
// b9s binary must still read our store class. RED = drift ALARM for the owner,
// NOT a release block (docs/hbi-boundary.md rule 2). The suite skips cleanly
// when the binary or fixture is absent, so an external project can never
// become a silent hard dependency.
//
// Read-only mandate: GETs only (health, snapshot, issue). No POST /api/write.
// Synchronous curl subprocesses only: undici keep-alive sockets from fetch()
// hang node --test's process isolation (proven 2026-09-30), and b9s needs lab
// bin/bd on PATH + the SEC-003 TMPDIR, same as every other bd invocation here.
// Run: node --test tests/test_b9s_drift.mjs
import test from "node:test";
import assert from "node:assert/strict";
import { spawn, spawnSync, execFileSync } from "node:child_process";
import { existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const LAB = path.resolve(here, "..", "..");            // beads-lab/
const B9S = process.env.B9S_BIN || path.join(LAB, "fixtures", "b9s-trial", "b9s");
const STORE = path.join(LAB, "fixtures", "b9s-trial", "planning-copy");
const EXPECTED_ISSUES = 53; // lab planning store as seeded 2026-09-29
const PORT = 18799; // drift-guard port; distinct from manual trial servers
const BASE = `http://127.0.0.1:${PORT}`;

function curlJson(url) {
  try {
    const out = execFileSync("curl", ["-s", "--fail", "--max-time", "5", url],
      { encoding: "utf8" });
    return JSON.parse(out);
  } catch { return null; }
}

test("b9s pinned binary still reads the embedded-dolt store class", (t) => {
  if (!existsSync(B9S) || !existsSync(STORE)) {
    t.skip(`b9s (${B9S}) or store copy (${STORE}) absent — advisory suite`);
    return;
  }
  const env = {
    ...process.env,
    PATH: path.join(LAB, "bin") + path.delimiter + process.env.PATH,
    TMPDIR: process.env.TMPDIR || path.join(LAB, "fixtures"),
  };
  const proc = spawn(B9S, ["web", "-no-token", "-listen", `127.0.0.1:${PORT}`],
    { cwd: STORE, env, stdio: "ignore", detached: true });
  proc.unref();
  t.after(() => { try { process.kill(-proc.pid, "SIGKILL"); } catch {} });

  let health = null;
  for (let i = 0; i < 60 && !health; i++) {
    spawnSync("sleep", ["0.25"]);
    health = curlJson(`${BASE}/api/health`);
  }
  assert.ok(health, "b9s web did not become healthy in 15s — DRIFT: upstream broke startup or flags changed");
  assert.equal(health.ok, true, `health not ok: ${health.error}`);
  assert.equal(health.kind, "dolt_embedded", `store kind drifted: ${health.kind}`);
  assert.equal(health.bd_found, true, "b9s cannot find bd — its write path would be dead");
  assert.equal(health.issues, EXPECTED_ISSUES, `issue count drifted on the pinned fixture (${health.issues})`);

  const snap = curlJson(`${BASE}/api/snapshot`);
  assert.ok(snap, "snapshot endpoint failed — DRIFT in their read API");
  const rows = snap.issues ?? snap.rows ?? (Array.isArray(snap) ? snap : null);
  assert.ok(Array.isArray(rows) && rows.length > 0, `snapshot shape changed: ${Object.keys(snap)}`);
  const first = rows[0];
  assert.ok(first && (first.id ?? first.ID), "snapshot rows lost their id field — DRIFT");
  const one = curlJson(`${BASE}/api/issue?id=${encodeURIComponent(String(first.id ?? first.ID))}`);
  assert.ok(one && (one.issue ?? one.id ?? one.ID), "issue detail endpoint shape changed — DRIFT");
});
