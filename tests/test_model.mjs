// tests/test_model.mjs — targeted checks for desktop/model.mjs (hbl-pnu.2.1).
// Run: node tests/test_model.mjs   (Node built-in test runner, no deps)
import test from "node:test";
import assert from "node:assert/strict";
import {
  buildSnapshot, storeIdentityKey, absenceReason,
  createWorkbenchState, isStale, invalidateOnMutation,
} from "../desktop/model.mjs";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const here = path.dirname(fileURLToPath(import.meta.url));
const fixture = (name) =>
  JSON.parse(readFileSync(path.join(here, "fixtures", "model", name), "utf8"));

const baseReads = (over = {}) => ({
  storeInfo: { workspace: "/lab/store", db: "/lab/store/.beads/lab.db" },
  issues: [], ready: [], blocked: [], ...over,
});

test("store identity: workspace+db key; missing part refused", () => {
  const k = storeIdentityKey({ workspace: "/w", db: "/w/.beads/x.db" });
  assert.equal(k, storeIdentityKey({ workspace: "/w", db: "/w/.beads/x.db" }));
  assert.throws(() => storeIdentityKey({ workspace: "/w" }), /db=missing/);
  assert.throws(() => storeIdentityKey({ db: "/w/.beads/x.db" }), /workspace=missing/);
});

test("snapshot joins fixtures: parent FIELD chains, depth, breadcrumb", () => {
  const f = fixture("tree.json");
  const snap = buildSnapshot(baseReads(f), { fetchedAt: 1000 });
  assert.equal(snap.storeKey, storeIdentityKey(f.storeInfo));
  assert.deepEqual([...snap.nodes.keys()].sort(), ["a", "b", "deep1", "epic", "root"]);
  assert.deepEqual(snap.nodes.get("deep1").path, ["root", "epic", "a", "deep1"]);
  assert.equal(snap.nodes.get("deep1").depth, 3);
  assert.deepEqual(snap.nodes.get("a").childIds, ["deep1"]);
  // native-join equality: node parent fields == fixture parent fields
  for (const r of f.issues) assert.equal(snap.nodes.get(r.id).parent, r.parent ?? null);
});

test("reparent truth: ID-dot spelling ignored after move", () => {
  const f = fixture("reparent.json");
  const snap = buildSnapshot(baseReads(f), { fetchedAt: 1 });
  // x.y.z is dotted as if under x.y, but its parent FIELD says otherwise
  const n = snap.nodes.get("x.y.z");
  assert.equal(n.parent, "reparented-from-x");
  assert.deepEqual(n.path, ["branch", "reparented-from-x", "x.y.z"]);
  assert.notEqual(n.parent, "x.y"); // ID-dot implication is false and unused
  // dotted sibling gets NO edge from ID spelling
  assert.equal(snap.nodes.get("x.y").childIds.length, 0);
});

test("blockers: typed direct, closed-filtered, multiple, inherited via parent chain", () => {
  const f = fixture("blockers.json");
  const snap = buildSnapshot(baseReads(f), { fetchedAt: 1 });
  const leaf = snap.nodes.get("leaf");
  assert.deepEqual(leaf.typedBlockers.map((b) => b.id).sort(), ["live1", "live2"]);
  assert.ok(!leaf.typedBlockers.some((b) => b.id === "closed-dep"));
  assert.ok(leaf.typedBlockers.every((b) => b.inherited === false));
  const inh = leaf.inheritedBlockers;
  assert.deepEqual([...new Set(inh.map((b) => b.id))], ["anc-blocker"]);
  assert.ok(inh.every((b) => b.inherited === true && b.source === "mid"));
});

test("unknown status/type/edge values preserved verbatim", () => {
  const f = fixture("unknowns.json");
  const snap = buildSnapshot(baseReads(f), { fetchedAt: 1 });
  const n = snap.nodes.get("weird");
  assert.equal(n.storedStatus, "pinned-zebra");
  assert.equal(snap.nodes.get("weird").statusKnown, true);
  assert.ok(n.edges.some((e) => e.edgeType === "relativistic-entanglement"));
});

test("derived blocked vs stored 'blocked' divergence flagged both ways", () => {
  const reads = baseReads({
    issues: [
      { id: "stale-flag", parent: null, status: "blocked", dependencies: [] },
      { id: "lying-open", parent: null, status: "open", dependencies: [
        { depends_on_id: "wall", dependency_type: "blocks", status: "open" }] },
    ],
    ready: ["stale-flag"], blocked: ["lying-open"], // native reads contradict stored status
  });
  const snap = buildSnapshot(reads, { fetchedAt: 1 });
  const a = snap.nodes.get("stale-flag"), b = snap.nodes.get("lying-open");
  assert.equal(a.derivedBlocked, false);
  assert.equal(a.divergence.stored, "blocked");
  assert.equal(a.divergence.derived, "ready");
  assert.equal(b.derivedBlocked, true);
  assert.equal(b.divergence.derived, "blocked");
});

test("parent cycle terminates with explicit marker, no hang, no children expansion", () => {
  const f = fixture("cycle.json");
  const snap = buildSnapshot(baseReads(f), { fetchedAt: 1 });
  assert.equal(snap.nodes.get("c1").cyclic, true);
  assert.equal(snap.nodes.get("c1").pathStatus, "cycle");
  // One chosen representation: root..node order over the parent FIELD,
  // bounded, terminated at the first repeat — consistently for every member.
  assert.deepEqual(snap.nodes.get("c1").path, ["c2", "c1"]); // parent-first, terminated at first repeat
  assert.equal(snap.nodes.get("c2").cyclic, true);
  assert.equal(snap.nodes.get("c2").pathStatus, "cycle");
  assert.deepEqual(snap.nodes.get("c2").path, ["c1", "c2"]);
  assert.equal(snap.nodes.get("c3").cyclic, true);
  assert.deepEqual(snap.nodes.get("c3").path, ["c3"]);
});

test("bounded: missing parent is parent-missing (NOT deleted); truncation distinct; tombstone=deleted", () => {
  const reads = baseReads({
    issues: { rows: [
      { id: "kid", parent: "gone", status: "open", dependencies: [] },
      { id: "dead", parent: null, status: "open", dependencies: [] },
    ], truncated: true },
    ready: [], blocked: [],
    tombstones: ["dead"],
  });
  const snap = buildSnapshot(reads, { fetchedAt: 1 });
  assert.equal(snap.nodes.get("kid").pathStatus, "parent-missing");
  assert.equal(snap.truncated, true);
  assert.equal(absenceReason(snap, "ghost").kind, "unknown-truncated");
  assert.equal(absenceReason(snap, "ghost").deleted, false);
  assert.deepEqual(absenceReason(snap, "dead"), { kind: "deleted", deleted: true, proof: "explicit tombstone" });
  const unfiltered = buildSnapshot(baseReads({ issues: [{ id: "ok", status: "open" }], ready: [], blocked: [] }), {});
  assert.equal(absenceReason(unfiltered, "never-there").deleted, false);
  assert.equal(absenceReason(unfiltered, "never-there").kind, "unknown-not-in-scope");
  const filtered = buildSnapshot(baseReads({ issues: { rows: [], filter: "label=impl" }, ready: [], blocked: [] }), {});
  assert.equal(absenceReason(filtered, "z").kind, "unknown-filtered");
});

test("read budget: each injected read-fn invoked at most once on a 150-issue fixture", () => {
  const f = fixture("bulk150.json");
  let n = 0;
  const fn = (v) => () => { n += 1; return v; };
  const snap = buildSnapshot({
    storeInfo: fn(f.storeInfo, "storeInfo"), issues: fn(f.issues, "issues"),
    ready: fn(f.ready, "ready"), blocked: fn(f.blocked, "blocked"),
  }, { fetchedAt: 1 });
  assert.equal(snap._reads._calls.length, 4); // issues, ready, blocked, storeInfo
  assert.equal(n, 4);
  assert.equal(snap.nodes.size, 150);
  // bound clipping is honored even if the source over-returns
  const clipped = buildSnapshot(baseReads({ ...f, issues: { rows: f.issues } }), { bound: 10 });
  assert.equal(clipped.nodes.size, 10);
});

test("freshness: TTL staleness + local-mutation invalidation", () => {
  const reads = baseReads({ issues: [{ id: "a", status: "open" }], ready: ["a"], blocked: [] });
  const snap = buildSnapshot(reads, { fetchedAt: 1000, ttlMs: 500 });
  assert.equal(isStale(snap, 1400), false);
  assert.equal(isStale(snap, 1600), true);
  invalidateOnMutation(snap);
  assert.equal(isStale(snap, 1000), true);
});

test("selection vs focus, arrows, Enter, back/forward return context", () => {
  const f = fixture("tree.json");
  const snap = buildSnapshot(baseReads(f), { fetchedAt: 1 });
  const ui = createWorkbenchState(snap, { selection: "root" });
  const rows = ui.visibleRows();
  assert.deepEqual(rows.map((r) => r.id), ["root", "b", "epic", "a", "deep1"]);
  ui.arrow(1);
  assert.equal(ui.selection, "b");
  assert.equal(ui.focus, null); // arrow moves the cursor only; focus unchanged
  ui.enter();
  assert.equal(ui.focus, "b"); // Enter promotes selection to keyboard focus
  ui.jump("deep1", "detail");
  assert.equal(ui.focus, "deep1");
  ui.back();
  assert.equal(ui.focus, "b");
  assert.equal(ui.pane, "list");
  ui.forward();
  assert.equal(ui.focus, "deep1");
  assert.equal(ui.pane, "detail");
});

test("claimed-not-ready regression: absent from ready AND blocked => unknown, never inferred blocked", () => {
  const snap = buildSnapshot(baseReads({
    issues: [{ id: "mine", parent: null, status: "in_progress", dependencies: [] }],
    ready: ["other"], blocked: [], // mine absent because claimed, not because blocked
  }), { fetchedAt: 1 });
  const n = snap.nodes.get("mine");
  assert.equal(n.derivedBlocked, null);
  assert.equal(n.divergence, null);
});

test("tree expansion respects cycles: cyclic node not descended", () => {
  const f = fixture("cycle.json");
  const snap = buildSnapshot(baseReads(f), { fetchedAt: 1 });
  const ui = createWorkbenchState(snap);
  const rows = ui.visibleRows().map((r) => r.id);
  for (const id of ["c1", "c2", "c3"]) assert.equal(rows.filter((x) => x === id).length, 1);
});
