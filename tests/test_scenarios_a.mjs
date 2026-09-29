// tests/test_scenarios_a.mjs — hbl-pnu.2.8 scenario acceptance, lane
// finish-scen-a: S1 (orient), S3 (derived-blocked badge), S4 (search reveals
// ancestor path), S10 (inherited multi-blocker diagnosis) from hci.md §3.
//
// Every scenario asserts UI state (REAL desktop components rendered through
// the jsx shim, as tests/test_blockers.mjs and tests/test_compare.mjs do)
// against a SAME-MOMENT `make_store.py read` readback of the same disposable
// store. One seeded store per test file; cleanup at the end. No mocks of bd:
// reads and external mutations drive the pinned native binary through the
// seeder CLI (make_store.py) or fixed-argv readonly invocations.
//
// Run: node --test tests/test_scenarios_a.mjs
import test from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { register } from "node:module";
import { fileURLToPath, pathToFileURL } from "node:url";
import path from "node:path";

const here = path.dirname(fileURLToPath(import.meta.url));
register(pathToFileURL(path.join(here, "__shims__", "jsx-loader.mjs")).href);
const shim = await import(pathToFileURL(path.join(here, "__shims__", "jsx-capture.mjs")).href);

const { buildSnapshot, createWorkbenchState, storeIdentityKey } =
  await import("../desktop/model.mjs");
const T = await import("../desktop/tree.mjs");
const S = await import("../desktop/search.mjs");
const { createHistoryStack } = await import("../desktop/history.mjs");
const B = await import("../desktop/blockers.mjs");

const BD_BIN = process.env.BEADS_LAB_BD
  || "/home/hermes/.hermes/work/beads-lab/bin/bd";
const SEEDER = path.join(here, "fixtures", "scenarios", "make_store.py");
const ACTOR = "lab-hci";

// ---- seeded world (one store for this whole file) ---------------------------
const world = JSON.parse(execFileSync("python3", [SEEDER, "seed",
  "--prefix", "scna"], { encoding: "utf8", maxBuffer: 64 * 1024 * 1024 }));
const STORE = world.store;
const IDS = world.ids;
const STORE_INFO = world.storeInfo;

test.after(() => {
  execFileSync("python3", [SEEDER, "cleanup", STORE], { encoding: "utf8" });
});

// same-moment READ through the seeder's fixed readonly argv table
function read(name, ...args) {
  const out = JSON.parse(execFileSync("python3",
    [SEEDER, "read", STORE, name, ...args],
    { encoding: "utf8", maxBuffer: 64 * 1024 * 1024 }));
  if (out.rc !== 0) {
    return { ...out }; // caller asserts on the failing read as a RESULT
  }
  return out.payload;
}
// external WRITE through the seeder's act passthrough (a RESULT, never a crash)
function act(actor, ...argv) {
  return JSON.parse(execFileSync("python3", [SEEDER, "act", STORE, actor, ...argv],
    { encoding: "utf8", maxBuffer: 64 * 1024 * 1024 }));
}
// bounded native search facade (read_model.py shape): one fixed-argv
// `bd search <q> --limit N --json`, rows verbatim, failure propagates.
function nativeSearch(query, bound) {
  const out = execFileSync(BD_BIN,
    ["-C", STORE, "--readonly", "--actor", ACTOR,
     "search", query, "--limit", String(bound), "--json"],
    { encoding: "utf8", maxBuffer: 64 * 1024 * 1024 });
  return JSON.parse(out);
}
const idOf = (rows) => (Array.isArray(rows) ? rows[0] : rows)?.id;

const walk = (t) => [...shim.walk(t)];
const textIn = (tree, s) => walk(tree).some((n) => typeof n === "string" && n.includes(s));

// Browse-snapshot from the bounded full listing (the tree's issue source).
function fullSnapshot() {
  return buildSnapshot({
    issues: read("list_all"),
    ready: null, blocked: null,
    storeInfo: STORE_INFO,
  }, { bound: 500 });
}
// refreshOnce provider: replays the seeder's readonly argv table (compare.mjs
// native helper pattern) for the tab + blocked queries.
const provider = {
  storeInfo: STORE_INFO,
  run: (verb, ...rest) => {
    if (verb === "ready") return read("ready");
    if (verb === "list" && rest[0] === "--assignee") return read("mine", rest[1]);
    if (verb === "blocked") return read("blocked");
    throw new Error(`unexpected provider query: ${verb} ${rest.join(" ")}`);
  },
};

// ============================================================================
// S1 — Orient (hci.md §3): first screen Ready = `bd ready --json
// --exclude-type=epic` minus already-claimed rows; Mine = `bd list
// --assignee lab-hci`; rows show id + status glyph+WORD; epic rail matches
// `bd epic status`; store header shows identity even though `bd context`
// errors (FACT 13).
// ============================================================================
test("S1 orient: Ready/Mine tabs, epic rail, store header — all vs same-moment readbacks", () => {
  const readyRows = read("ready"); // = ready --json -n 100 --exclude-type=epic
  assert.ok(Array.isArray(readyRows) && readyRows.length > 0);
  assert.ok(readyRows.every((r) => r.issue_type !== "epic"),
    "ready read excludes epics (--exclude-type=epic)");

  const r = T.refreshOnce(provider, { tab: "ready", assignee: ACTOR });
  assert.deepEqual(r.tabIds, readyRows.map((x) => x.id),
    "Ready tab rows == exact `bd ready --json --exclude-type=epic` output");

  // already-claimed exclusion is NATIVE ready behavior; proven honestly by
  // live dynamics: claim a plain ready row -> it LEAVES ready and JOINS Mine
  // (claim sets assignee + in_progress, so no fake unclaim dance).
  const spare = readyRows.find((x) => x.id === IDS.ready);
  assert.ok(spare, "the seeded plain ready task is present in ready");
  const claimed = idOf(read("mine", ACTOR)); // the seeded claimed row
  assert.ok(!readyRows.some((x) => x.id === claimed),
    "the already-claimed seeded row is absent from ready (minus-claimed term)");
  const mineRows = read("mine", ACTOR);
  assert.deepEqual(mineRows.map((x) => x.id), [IDS.mine],
    "seed: exactly one claimed row owned by lab-hci");
  const c = act(ACTOR, "update", IDS.ready, "--claim", "--json");
  assert.equal(c.rc, 0, c.stderr);
  const readyAfter = read("ready");
  assert.ok(!readyAfter.some((x) => x.id === IDS.ready),
    "claiming removes the row from ready (native minus-claimed exclusion proven live)");
  const mineAfter = read("mine", ACTOR);
  assert.deepEqual(mineAfter.map((x) => x.id).sort(),
    [IDS.mine, IDS.ready].sort(),
    "claimed row now appears in `bd list --assignee lab-hci` (Mine gains it)");
  const m = T.refreshOnce(provider, { tab: "mine", assignee: ACTOR });
  assert.deepEqual(m.tabIds, mineAfter.map((x) => x.id),
    "Mine tab rows == exact `bd list --assignee lab-hci` output");
  const tc = T.tabCommands({ assignee: ACTOR });
  assert.equal(tc.ready.command, "bd ready --exclude-type=epic");
  assert.equal(tc.mine.command, `bd list --assignee ${ACTOR}`);

  // every row shows id + type + glyph AND word status
  const snap = fullSnapshot();
  const ui = createWorkbenchState(snap);
  const built = T.buildTreeRows(snap, ui);
  const rowOf = (id) => built.rows.find((x) => x.id === id);
  for (const id of [IDS.branchB, IDS.ready, IDS.taskB, IDS.epic]) {
    const row = rowOf(id);
    assert.ok(row, `tree row present for ${id}`);
    assert.equal(row.id, id);
    const rec = snap.byId.get(id);
    assert.equal(row.statusWord, rec.status, "status WORD verbatim");
    assert.ok(row.statusGlyph && row.statusGlyph !== row.statusWord,
      "glyph present alongside the word (never glyph-only)");
    assert.ok(row.title.length > 0);
  }

  // epic rail matches `bd epic status` counts
  const es = read("epic_status");
  const epicEntry = Array.isArray(es) ? es.find((e) => e.epic?.id === IDS.epic) : null;
  assert.ok(epicEntry, "epic status readback carries the seeded epic");
  const prog = T.epicProgress(snap, IDS.epic);
  assert.deepEqual(prog, { total: epicEntry.total_children, closed: epicEntry.closed_children },
    "epic rail counts == `bd epic status`");
  const tree = T.Tree({ snapshot: snap, ui });
  assert.ok(textIn(tree, `epic progress ${epicEntry.closed_children}/${epicEntry.total_children}`),
    "epic rail rendered with the native counts");

  // store header: identity shown EVEN THOUGH `bd context` errors (FACT 13)
  const ctx = read("context");
  assert.ok(ctx && ctx.rc !== 0, "bd context fails on the embedded store (FACT 13 reproduced)");
  assert.equal(snap.storeKey, storeIdentityKey(STORE_INFO),
    "store identity present from info, not context");
  assert.ok(snap.storeKey.includes(path.basename(STORE)),
    "store header carries the store identity");
});

// ============================================================================
// S3 — Why is this blocked: derived-blocked badge while stored status is
// `open` (FACT 8), verbatim-in-meaning vs `bd blocked`, and an EXTERNAL CLI
// close of the blocker flips the badge to ready on next refresh.
// ============================================================================
test("S3 derived-blocked badge vs stored open + external close flips to ready on refresh", () => {
  // same-moment native truth
  const blocked = read("blocked");
  assert.ok(blocked.some((r) => r.id === IDS.taskB),
    "`bd blocked` lists the seeded victim taskB");
  const shown = read("show", IDS.taskB);
  const stored = (Array.isArray(shown) ? shown[0] : shown).status;
  assert.equal(stored, "open",
    "stored status is verbatim `open` while natively blocked (FACT 8)");

  // UI: snapshot built from the seeder's readonly reads
  const issues = read("list_all");
  const snap = buildSnapshot({
    issues,
    ready: read("ready").map((r) => r.id),
    blocked: blocked.map((r) => r.id),
    storeInfo: STORE_INFO,
  }, { bound: 500 });
  const node = snap.nodes.get(IDS.taskB);
  assert.equal(node.storedStatus, "open", "badge layer keeps stored status verbatim");
  assert.equal(node.derivedBlocked, true,
    "badge shows DERIVED blockage even though raw status is open");
  assert.deepEqual(node.typedBlockers.map((b) => b.id), [IDS.gateA],
    "derived diagnosis names the open blocking dependency (gateA) — matches `bd blocked` meaning");
  assert.ok(node.divergence && node.divergence.stored === "open"
    && node.divergence.derived === "blocked",
    "divergence (stale-flag class) surfaces, never normalized away");

  // rendered tree shows the blocked word for the open-status row
  const ui = createWorkbenchState(snap);
  const built = T.buildTreeRows(snap, ui);
  const row = built.rows.find((x) => x.id === IDS.taskB);
  assert.equal(row.statusWord, "open", "row keeps the stored word");
  assert.match(row.blockedWord || "", /blocked/, "derived blocked word rendered");

  // EXTERNAL close of the blocker (another actor, straight through bd)
  const closed = act("lab-outsider", "close", IDS.gateA,
    "--reason", "externally closed by other actor", "--json");
  assert.equal(closed.rc, 0, closed.stderr);
  const gateShown = read("show", IDS.gateA);
  assert.equal((Array.isArray(gateShown) ? gateShown[0] : gateShown).status, "closed",
    "readback: gateA closed");

  // REFRESH: rebuild from fresh same-moment reads — badge flips to ready
  const blocked2 = read("blocked");
  assert.ok(!blocked2.some((r) => r.id === IDS.taskB),
    "`bd blocked` no longer lists taskB");
  const ready2 = read("ready");
  assert.ok(ready2.some((r) => r.id === IDS.taskB),
    "`bd ready` now lists taskB");
  const snap2 = buildSnapshot({
    issues: read("list_all"),
    ready: ready2.map((r) => r.id),
    blocked: blocked2.map((r) => r.id),
    storeInfo: STORE_INFO,
  }, { bound: 500 });
  const node2 = snap2.nodes.get(IDS.taskB);
  assert.equal(node2.derivedBlocked, false,
    "refresh after external close flips the badge to ready");
  assert.equal(node2.storedStatus, "open", "status was never touched by the flip");
  assert.equal(node2.divergence, null, "derived/stored now agree");
  const ui2 = createWorkbenchState(snap2);
  const built2 = T.buildTreeRows(snap2, ui2);
  assert.ok(!built2.rows.find((x) => x.id === IDS.taskB).blockedWord,
    "rendered row drops the blocked word after refresh");
});

// ============================================================================
// S4 — Search reveals ancestor path: hits labeled with the parent-FIELD path,
// Enter lands IN the tree with the path expanded and hit focused, history
// pushed, and back restores the pre-search state. Call-count bound asserted
// (warm snapshot path = exactly ONE native query, zero show loop).
// ============================================================================
test("S4 search reveals ancestor path; Enter lands in tree, back restores", () => {
  const snap = fullSnapshot();
  const res = S.searchIssues({
    snapshot: snap, query: "branch B",
    searchRead: nativeSearch, limit: 25,
  });
  // native equality vs same-moment readback
  const nativeRows = read("search", "branch B");
  assert.deepEqual(res.hits.map((h) => h.id).sort(),
    nativeRows.map((r) => r.id).sort(),
    "hits == same-moment `bd search` readback (FACT: flat, hierarchy-blind source)");
  assert.deepEqual(res.hits.map((h) => h.id).sort(),
    [IDS.taskB, IDS.branchB].sort(),
    "S4: exactly the 2 expected hits (hci FACT 6) — the victim task and branch B itself");
  for (const h of res.hits) {
    assert.equal(h.pathStatus, "resolved", `hit ${h.id} path resolved from parent FIELD`);
    assert.ok(h.path.length >= 1);
    assert.equal(h.path[h.path.length - 1].id, h.id);
  }
  const tb = res.hits.find((h) => h.id === IDS.taskB);
  assert.deepEqual(tb.path.map((p) => p.id), [IDS.epic, IDS.branchB, IDS.taskB],
    "taskB path resolves under branch B from the parent FIELD");
  assert.deepEqual(tb.path.map((p) => p.label),
    ["Hermes Beads laboratory", "branch B", "branch B blocked task"],
    "path carries the ancestors' TITLES (hci S4 label shape)");
  // call-count bound: warm path = 1 query, 0 fallback/show calls
  assert.equal(res.nativeCalls, 1,
    "exactly one native query; zero per-hit show loop (owner N+1 ruling)");
  assert.equal(res.fallback.calls, 0);

  // rendered panel carries the resolved path labels
  const panel = S.SearchPanel({ results: res, cursor: 0 });
  assert.ok(textIn(panel, "Hermes Beads laboratory › branch B › branch B blocked task"),
    "panel renders the full resolved ancestor path label");

  // Enter lands IN the tree: pre-search state saved, path expanded, focus set,
  // history pushed (search is a navigation entry, not a parallel world).
  const ui = createWorkbenchState(snap, { selection: IDS.ready });
  ui.enter();
  const history = createHistoryStack({ storeKey: snap.storeKey });
  history.push({ storeKey: snap.storeKey, pane: "list", tab: "ready",
    filter: "label=impl", scroll: 42, focus: IDS.ready, selection: IDS.ready,
    expanded: [...ui.expanded].sort() });
  const preSelection = ui.selection, preFocus = ui.focus;
  const preExpanded = [...ui.expanded].sort().join(",");

  const idx = res.hits.findIndex((h) => h.id === IDS.taskB);
  const entered = S.enterSearchHit({ results: res, index: idx, snapshot: snap,
    workbench: ui, history, pane: "tree" });

  assert.equal(ui.focus, IDS.taskB, "hit focused");
  assert.equal(ui.pane, "tree", "landed IN the tree");
  for (const anc of [IDS.epic, IDS.branchB])
    assert.ok(ui.expanded.has(anc), `path ancestor ${anc} expanded`);
  const rendered = ui.visibleRows().map((r) => r.id);
  assert.ok(rendered.includes(IDS.taskB), "hit row rendered in the tree");
  const entry = history.entries()[history.entries().length - 1];
  assert.equal(entry.search, "branch B", "query lives in the history entry");
  assert.equal(entry.filter, "label=impl", "search did not erase the filter");

  // back returns to the pre-search state
  entered.restore();
  assert.equal(ui.selection, preSelection, "selection restored");
  assert.equal(ui.focus, preFocus, "focus restored");
  assert.equal([...ui.expanded].sort().join(","), preExpanded,
    "expansion restored to the pre-search bundle");
  assert.equal(history.current().focus, IDS.ready, "history back at pre-search entry");
});

// Same-moment snapshot fed from `bd show` rows: native show EXPANDS each
// dependency[] entry to its full record (probed: carries `status` +
// `dependency_type`), which is the only native read that lets the model drop
// CLOSED blockers (model.mjs activeBlockerRefs consumes d.status; raw
// `list --all` edge rows carry none).
function showSnapshot() {
  const ids = read("list_all").map((r) => r.id);
  const issues = ids.map((id) => {
    const p = read("show", id);
    const row = Array.isArray(p) ? p[0] : p;
    if (!row) throw new Error(`show ${id} returned no row`);
    return row;
  });
  return buildSnapshot({
    issues,
    ready: read("ready").map((r) => r.id),
    blocked: read("blocked").map((r) => r.id),
    storeInfo: STORE_INFO,
  }, { bound: 500 });
}

// ============================================================================
// S10 — Inherited multi-blocker diagnosis: x10 (child of p10) shows the
// INHERITED blockers g1,g2 (source p10, via path) PLUS its direct blocker g3.
// ============================================================================
test("S10 inherited multi-blocker: x10 shows inherited g1,g2 (via p10) plus direct g3", () => {
  // same-moment native truth: blocked lists both the ancestor and the victim
  const blocked = read("blocked");
  const blockedIds = blocked.map((r) => r.id);
  assert.ok(blockedIds.includes(IDS.p10) && blockedIds.includes(IDS.x10),
    "`bd blocked` lists the blocked ancestor p10 AND the victim x10");

  const snap = showSnapshot();

  const bs = B.blockersFor(snap, IDS.x10);
  const direct = bs.filter((b) => !b.inherited).map((b) => b.id).sort();
  assert.deepEqual(direct, [IDS.g3], "direct blocker: g3 only");
  const inh = bs.filter((b) => b.inherited);
  assert.deepEqual(inh.map((b) => b.id).sort(), [IDS.g1, IDS.g2].sort(),
    "inherited blockers: g1 AND g2 (multi-blocker, all offered)");
  for (const b of inh) {
    assert.equal(b.source, IDS.p10, "inherited blocker names the blocked ancestor p10");
    assert.ok(b.via.includes(IDS.p10) && b.via[0] === IDS.x10,
      "via carries the source path x10 › p10");
  }

  // the ancestor itself: g1,g2 direct there, and it must NOT see g3
  const p = B.blockersFor(snap, IDS.p10);
  assert.deepEqual(p.filter((b) => !b.inherited).map((b) => b.id).sort(),
    [IDS.g1, IDS.g2].sort(), "p10's own direct blockers are g1,g2");
  assert.ok(!p.some((b) => b.id === IDS.g3), "g3 (x10-only edge) never leaks to p10");

  // clearing only the DIRECT edge keeps x10 blocked (native proves it)
  const c3 = act("lab-outsider", "close", IDS.g3, "--reason", "direct edge cleared", "--json");
  assert.equal(c3.rc, 0, c3.stderr);
  const blocked2 = read("blocked").map((r) => r.id);
  assert.ok(blocked2.includes(IDS.x10),
    "after closing g3, native blocked STILL lists x10 — inherited pressure is real");
  const snap2 = showSnapshot();
  assert.equal(snap2.nodes.get(IDS.x10).derivedBlocked, true);
  assert.deepEqual(B.blockersFor(snap2, IDS.x10).filter((b) => !b.inherited), [],
    "diagnosis after g3 close: no direct blockers remain");
  assert.deepEqual(B.blockersFor(snap2, IDS.x10).filter((b) => b.inherited)
    .map((b) => b.id).sort(), [IDS.g1, IDS.g2].sort(),
    "remaining blockers are exactly the inherited pair");

  // closing g1,g2 (ancestors') finally frees x10
  assert.equal(act("lab-outsider", "close", IDS.g1, "--reason", "done", "--json").rc, 0);
  assert.equal(act("lab-outsider", "close", IDS.g2, "--reason", "done", "--json").rc, 0);
  const blocked3 = read("blocked").map((r) => r.id);
  assert.ok(!blocked3.includes(IDS.x10) && !blocked3.includes(IDS.p10),
    "with all three blockers closed, native blocked lists neither x10 nor p10");
});
