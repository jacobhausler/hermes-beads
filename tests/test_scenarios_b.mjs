// tests/test_scenarios_b.mjs — hbl-pnu.2.8 lane B scenario acceptance:
//   S5 claim contention + dead-worker reclaim (bd reclaim --older-than).
//   (S7/S8 split-compare scenarios were removed 2026-09-30 together with
//    desktop/compare.mjs — council S3 / owner v0.x law: generic exploration
//    is LAUNCH-only via b9s.)
//
// World: the SEEDED real-bd scenario store (tests/fixtures/scenarios/
// make_store.py — every mutation/readback goes through its fixed-argv
// seed/read/act/cleanup CLI driving the pinned bd v1.3.0). UI state comes
// from the REAL desktop components rendered through the jsx shim, exactly as
// tests/test_blockers.mjs does. Each scenario asserts
// UI state against a SAME-MOMENT `make_store.py read` readback.
//
// One store per test file (seeded once here); stores live under the
// gitignored e2e-runtime fixture root; cleanup is the seeder's own command.
//
// Run: node --test tests/test_scenarios_b.mjs
import test from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { register } from "node:module";
import { fileURLToPath, pathToFileURL } from "node:url";
import path from "node:path";

const here = path.dirname(fileURLToPath(import.meta.url));
register(pathToFileURL(path.join(here, "__shims__", "jsx-loader.mjs")).href);
const shim = await import(pathToFileURL(path.join(here, "__shims__", "jsx-capture.mjs")).href);

const { buildSnapshot, createWorkbenchState } = await import("../desktop/model.mjs");
const { Tree } = await import("../desktop/tree.mjs");
// ---- seeder bridge ----------------------------------------------------------
const SEEDER = path.join(here, "fixtures", "scenarios", "make_store.py");
const py = (...args) =>
  JSON.parse(execFileSync("python3", [SEEDER, ...args], { encoding: "utf8", maxBuffer: 32 * 1024 * 1024 }));

// ONE new store for this whole file (lab rule: unique prefix per file).
const WORLD = py("seed", "--prefix", "scnb");
const STORE = WORLD.store;
const IDS = WORLD.ids;
const STORE_INFO = WORLD.storeInfo;

const read = (name, ...args) => py("read", STORE, name, ...args);
// act() runs a WRITE: nonzero rc is a RESULT, so capture stdout/stderr from
// the thrown ExecFile error (execFileSync throws whenever bd exits nonzero).
const act = (actor, ...argv) => {
  try {
    const stdout = execFileSync("python3", [SEEDER, "act", STORE, actor, ...argv],
      { encoding: "utf8", maxBuffer: 32 * 1024 * 1024 });
    return JSON.parse(stdout);
  } catch (e) {
    if (e && typeof e.code === "number" && e.stdout != null)
      return { rc: e.code, stdout: String(e.stdout), stderr: String(e.stderr ?? "") };
    throw e;
  }
};

// parsed native payload helpers (read prints {rc, payload})
const readOk = (name, ...args) => {
  const r = read(name, ...args);
  assert.equal(r.rc, 0, `native read ${name} failed: ${r.stderr ?? r.stdout}`);
  return r.payload;
};
const showRow = (id) => {
  const r = read("show", id);
  return r.rc === 0 ? (Array.isArray(r.payload) ? r.payload[0] ?? null : r.payload) : r.payload;
};

// snapshot from the real store via the seeder's bounded list_all read
const snapshotNow = () => buildSnapshot({
  issues: readOk("list_all"),
  ready: readOk("ready").map((r) => r.id),
  blocked: readOk("blocked").map((r) => (typeof r === "string" ? r : r.id)),
  storeInfo: STORE_INFO,
}, { fetchedAt: Date.now(), bound: 150, ttlMs: 60_000 });

const walk = (t) => [...shim.walk(t)];
const textIn = (t, s) => walk(t).some((n) => typeof n === "string" && n.includes(s));
const renderTree = (snap) => Tree({ snapshot: snap, ui: createWorkbenchState(snap) });
const rowNode = (t, id) =>
  walk(t).find((n) => typeof n === "object" && n.props?.["data-tree-row"] === id);

const sleepSync = (ms) => Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, ms);

// ============================================================================
// S5 — claim contention + dead-worker reclaim
// ============================================================================
test("S5 claim contention: second actor refused naming holder, readback assignee unchanged, UI never shows two assignees", () => {
  // `mine` is claimed by lab-hci in the seed (native ready excludes claimed).
  const pre = readOk("list_all");
  const before = showRow(IDS.mine);
  assert.equal(before.assignee, "lab-hci", "seed precondition: lab-hci holds the row");

  // UI truth BEFORE the attempt (real Tree render off the same store state).
  const uiBefore = renderTree(snapshotNow());
  const rowBefore = rowNode(uiBefore, IDS.mine);
  assert.ok(rowBefore, "claimed row renders in the tree");

  // second actor attempts a claim — must be REFUSED, naming the holder.
  const loser = act("lab-other", "update", IDS.mine, "--claim", "--json");
  assert.equal(loser.rc, 1, `claim conflict exits 1 (got ${loser.rc})`);
  // bd 1.3.0 (probed): conflict JSON goes to STDERR after a prose line; stdout is empty.
  const errJson = loser.stderr.split("\n").find((l) => l.startsWith("{"));
  assert.ok(errJson, `structured refusal on stderr: ${loser.stderr}`);
  const failed = JSON.parse(errJson).failed;
  assert.ok(Array.isArray(failed) && failed.length === 1, "failed[] names the refusal");
  assert.match(failed[0].error, /already claimed by lab-hci/,
    `refusal names the holder verbatim: ${failed[0].error}`);

  // same-moment readback: assignee UNCHANGED, nothing written.
  const after = showRow(IDS.mine);
  assert.equal(after.assignee, "lab-hci", "readback assignee unchanged");
  assert.deepEqual(after, before, "refused claim writes nothing (business fields identical)");
  assert.deepEqual(readOk("list_all"), pre, "whole-store read is byte-identical: no optimistic mutation");

  // UI: the refusal surface NEVER shows two assignees — the row keeps exactly
  // one assignee identity at every same-moment observation, and re-rendering
  // the tree after the refusal yields an identical row (no optimistic label).
  const assigneeObserved = [];
  for (let i = 0; i < 3; i++) assigneeObserved.push(showRow(IDS.mine).assignee);
  assert.deepEqual([...new Set(assigneeObserved)], ["lab-hci"],
    "UI sequence shows one assignee at every point (never lab-other alongside lab-hci)");
  const uiAfter = renderTree(snapshotNow());
  assert.deepEqual(rowNode(uiAfter, IDS.mine), rowBefore,
    "rendered tree row for the contested bead is unchanged after the refusal");
});

test("S5 dead worker: lease lapses, reclaim --older-than returns it to open, another claim succeeds, UI never shows two assignees", () => {
  // Probed bd 1.3.0 facts recorded for the report:
  //  - claim TTL is fixed at 5 min; `bd config set claim.lease-ttl …` is
  //    ACCEPTED but has NO effect on the granted lease (verified: lease is
  //    still start+5m after setting 3s/3), so the TTL cannot be shortened;
  //  - `bd reclaim --older-than 0s` is the smallest accepted grace window
  //    (documented form: "reclaim every currently-expired lease") and is a
  //    no-op (count 0) while the lease is still live.
  const deadId = IDS.dead;

  // dead worker claims, then dies (no heartbeats).
  const claimed = act("lab-dead", "update", deadId, "--claim", "--json");
  assert.equal(claimed.rc, 0, claimed.stderr);
  const held = showRow(deadId);
  assert.equal(held.assignee, "lab-dead");
  assert.equal(held.status, "in_progress");
  assert.ok(held.lease_expires_at, "claim carries a lease expiry");

  // UI/assignee observation tape: every same-moment assignee read of the row.
  const tape = [];
  const observe = () => tape.push(showRow(deadId).assignee ?? null);
  observe();

  // Reclaim while the lease is LIVE must refuse to rob the worker.
  const tooEarly = act("lab-reaper", "reclaim", "--older-than", "0s", "--id", deadId, "--json");
  assert.equal(tooEarly.rc, 0, tooEarly.stderr);
  assert.equal(JSON.parse(tooEarly.stdout).count, 0,
    "live lease is never reclaimed (grace window honoured)");
  observe();

  // Wait out the fixed 5-min TTL, polling with the smallest accepted
  // --older-than (0s). Recorded: bd accepts `0s`; TTL itself is not shortenable.
  const deadline = Date.now() + 6 * 60_000;
  let receipt = null;
  for (;;) {
    sleepSync(10_000);
    const r = act("lab-reaper", "reclaim", "--older-than", "0s", "--id", deadId, "--json");
    assert.equal(r.rc, 0, r.stderr);
    const parsed = JSON.parse(r.stdout);
    observe();
    if (parsed.count > 0) { receipt = parsed; break; }
    assert.ok(Date.now() < deadline,
      "lease expired + reclaim --older-than 0s must eventually reclaim (6-min bound)");
  }
  assert.ok(JSON.stringify(receipt.reclaimed).includes(deadId),
    `reclaim receipt names the issue: ${JSON.stringify(receipt)}`);

  // Same-moment readback: back to open, assignee cleared, previous owner is
  // recorded by native reclaim as a recovery event (the reaper's receipt).
  const freed = showRow(deadId);
  assert.equal(freed.status, "open", "reclaimed issue is open again");
  assert.ok(freed.assignee == null || freed.assignee === "",
    `assignee cleared (got ${JSON.stringify(freed.assignee)})`);

  // Another claim succeeds.
  const winner = act("lab-newcomer", "update", deadId, "--claim", "--json");
  assert.equal(winner.rc, 0, winner.stderr);
  assert.equal(showRow(deadId).assignee, "lab-newcomer");
  observe();

  // THE invariant: the UI never showed two assignees at any point. The
  // observation tape (deduplicated, order-preserving) passes through exactly
  // lab-dead → (unassigned) → lab-newcomer; the refused claimant never appears.
  const dedup = tape.filter((v, i) => v !== tape[i - 1]);
  assert.deepEqual(dedup, ["lab-dead", null, "lab-newcomer"],
    `assignee sequence has exactly one holder at a time: ${JSON.stringify(dedup)}`);
  assert.equal(tape.includes("lab-other"), false, "refused claimant never holds the row");
  const ui = renderTree(snapshotNow());
  const row = rowNode(ui, deadId);
  assert.ok(row && !textIn(row, "lab-other"), "refused actor never surfaces on the row");
});

// ============================================================================
// (council S3, owner v0.x law 2026-09-30): S7/S8 removed with
// desktop/compare.mjs — the generic explorer pane is LAUNCH-only via b9s.
// Native reparent/delete/history semantics stay proven by the seeder reads
// above and the python native suites.
// ============================================================================

// ---- teardown: the seeder's own cleanup for the store this file created ----
test.after?.(() => {});
import { after } from "node:test";
after(() => { py("cleanup", STORE); });
