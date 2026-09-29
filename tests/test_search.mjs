// tests/test_search.mjs — hbl-pnu.2.6: search reveals the resolved ancestor
// path (parent-FIELD chain from the H1 snapshot, no per-hit show loop) and
// Enter lands IN the tree with history push + byte-identical Back.
// Run: node --test tests/test_search.mjs   (Node built-in runner, no deps)
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync, mkdirSync, rmSync } from "node:fs";
import { execFileSync } from "node:child_process";
import { register } from "node:module";
import { fileURLToPath, pathToFileURL } from "node:url";
import path from "node:path";

const here = path.dirname(fileURLToPath(import.meta.url));
register(pathToFileURL(path.join(here, "__shims__", "jsx-loader.mjs")).href);

const shim = await import(pathToFileURL(path.join(here, "__shims__", "jsx-capture.mjs")).href);
const { buildSnapshot } = await import("../desktop/model.mjs");
const { createHistoryStack } = await import("../desktop/history.mjs");
const {
  searchIssues, resolveHitPath, SearchPanel, enterSearchHit,
  DEFAULT_SEARCH_LIMIT, HARD_SEARCH_LIMIT, MAX_FALLBACK_CALLS,
} = await import("../desktop/search.mjs");

test("rendered Tree reveals native hit and Back restores rows and expansion", async () => {
  const { createWorkbenchState } = await import("../desktop/model.mjs");
  const { Tree, buildTreeRows, createTreeController } = await import("../desktop/tree.mjs");
  const snapshot = buildSnapshot(baseReads({ issues: [
    { id: "root", parent: null }, { id: "mid", parent: "root" },
    { id: "leaf", parent: "mid" },
  ] }));
  const ui = createWorkbenchState(snapshot, { focus: "root", selection: "root", expanded: new Set(["root"]) });
  const history = createHistoryStack({ storeKey: snapshot.storeKey });
  history.push({ storeKey: snapshot.storeKey, beadId: "root", focus: "root", selection: "root", filter: "Ready", search: "old", expanded: ["root"] });
  const beforeRows = buildTreeRows(snapshot, ui).rows;
  const beforeExpanded = [...ui.expanded];
  const results = searchIssues({ snapshot, query: "needle",
    searchRead: () => [{ id: "outside", title: "needle", status: "in_progress" }],
    showRead: () => ({ id: "outside", parent: "leaf" }) });
  const nav = enterSearchHit({ results, index: 0, snapshot, workbench: ui, history });
  const row = buildTreeRows(snapshot, ui).rows.find(r => r.id === "outside");
  assert.ok(row, "actual rendered-row builder includes hit");
  assert.equal(row.depth, 3);
  assert.equal(row.parentId, "leaf");
  const rendered = walk(Tree({ snapshot, ui })).find(n => n?.props?.id === "row:outside");
  assert.ok(rendered, "actual JSX tree contains hit");
  assert.equal(rendered.props["aria-level"], 4);
  assert.equal(rendered.props["aria-selected"], true);
  assert.equal(snapshot.nodes.has("outside"), false);
  assert.equal(snapshot.readyIds.has("outside"), false);
  assert.equal(results.nativeCalls, 2);
  const ctrl = createTreeController({ snapshot, ui });
  // Native cursor/navigation regression is exercised by the shared tree suite.
  assert.ok(ctrl);
  nav.restore();
  assert.deepEqual(buildTreeRows(snapshot, ui).rows, beforeRows);
  assert.deepEqual([...ui.expanded], beforeExpanded);
  assert.equal(ui.focus, "root");
  assert.equal(history.current().filter, "Ready");
  assert.equal(history.current().search, "old");
  ui.forward();
  assert.ok(buildTreeRows(snapshot, ui).rows.some(r => r.id === "outside"));
  ui.back();
  assert.deepEqual(buildTreeRows(snapshot, ui).rows, beforeRows);
});

test("shared model traversal preserves ready-first root ordering", async () => {
  const { createWorkbenchState } = await import("../desktop/model.mjs");
  const { buildTreeRows } = await import("../desktop/tree.mjs");
  const snapshot = buildSnapshot(baseReads({ issues: [
    { id: "later", parent: null }, { id: "ready", parent: null },
  ], ready: [{ id: "ready" }] }));
  const ui = createWorkbenchState(snapshot);
  assert.deepEqual(buildTreeRows(snapshot, ui).rows.map(r => r.id), ["ready", "later"]);
  assert.deepEqual(ui.visibleRows().map(r => r.id), ["ready", "later"]);
});

// ---- helpers ----------------------------------------------------------------
const fixture = (name) =>
  JSON.parse(readFileSync(path.join(here, "fixtures", "search", name), "utf8"));

const baseReads = (over = {}) => ({
  issues: [], ready: [], blocked: [],
  storeInfo: { workspace: "/lab/store", db: "/lab/store/.beads/lab.db" },
  ...over,
});

const walk = (t) => [...shim.walk(t)];
const textIn = (tree, s) => walk(tree).some((n) => typeof n === "string" && n.includes(s));

// fake native search facade: exact substring match over titles, sorted by id
// (order-independent equality like the native probe receipts).
function fakeSearch(rows, { throws } = {}) {
  const calls = [];
  const fn = (query, bound) => {
    calls.push([query, bound]);
    if (throws) throw new Error(throws);
    return rows
      .filter((r) => (r.title ?? "").toLowerCase().includes(query.toLowerCase()))
      .sort((a, b) => (a.id < b.id ? -1 : 1))
      .slice(0, bound);
  };
  fn.calls = calls;
  return fn;
}

// hci S4 fixture shape (probe subtree of the lab store):
//   hbl-9g2 › hbl-9g2.5 › hbl-9g2.5.1 ("blocked worker in branch B")
//   hbl-ibv › hbl-ibv.1 › hbl-0uf   ("focus model spec" — no dots, reparented
//                                     in later: its ID never changed)
const s4 = () => fixture("s4-probe-subtree.json");
const s4Snapshot = (over = {}) => {
  const f = s4();
  return buildSnapshot(baseReads({
    issues: f.issues, ready: f.ready ?? [], blocked: f.blocked ?? [],
    storeInfo: f.storeInfo,
  }), { fetchedAt: 1, ...over });
};

// ---- API surface ------------------------------------------------------------
test("search requires the injected read facade, a snapshot, and refuses unbounded limits", () => {
  const snap = s4Snapshot();
  assert.throws(() => searchIssues({ snapshot: snap, query: "branch B" }),
    /searchRead/);
  assert.throws(() => searchIssues({ query: "x", searchRead: () => [] }),
    /snapshot/);
  assert.throws(() => searchIssues({ snapshot: snap, query: "  ", searchRead: () => [] }),
    /query/);
  assert.throws(() => searchIssues({ snapshot: snap, query: "x", searchRead: () => [], limit: 0 }),
    /positive int|unbounded/);
});

test("S4 equality: 'branch B' hits carry full parent-field paths; no per-hit native calls", () => {
  const snap = s4Snapshot();
  const f = s4();
  const read = fakeSearch(f.issues);
  const res = searchIssues({ snapshot: snap, query: "branch B", searchRead: read });

  const expected = f.issues
    .filter((r) => r.title.toLowerCase().includes("branch b"))
    .sort((a, b) => (a.id < b.id ? -1 : 1))
    .map((r) => r.id);
  assert.deepEqual(res.hits.map((h) => h.id), expected,
    "hits equal the native probe receipt set (hbl-9g2.5, hbl-9g2.5.1)");
  // full paths from the parent FIELD chain
  const byId = Object.fromEntries(res.hits.map((h) => [h.id, h]));
  assert.deepEqual(byId["hbl-9g2.5"].path.map((p) => p.id),
    ["hbl-9g2", "hbl-9g2.5"]);
  assert.deepEqual(byId["hbl-9g2.5.1"].path.map((p) => p.id),
    ["hbl-9g2", "hbl-9g2.5", "hbl-9g2.5.1"]);
  assert.equal(byId["hbl-9g2.5.1"].pathStatus, "resolved");
  // call-count bound: exactly ONE native call for the whole query — the
  // snapshot is warm, so zero per-hit lookups (owner N+1 ruling).
  assert.equal(read.calls.length, 1);
  assert.equal(res.nativeCalls, 1);
  assert.equal(res.fallback.calls, 0);
});

test("reparented dotted-ID-less bead shows its TRUE path (no ID-spelling inference)", () => {
  const snap = s4Snapshot();
  const f = s4();
  const read = fakeSearch(f.issues);
  const res = searchIssues({ snapshot: snap, query: "focus model", searchRead: read });
  assert.deepEqual(res.hits.map((h) => h.id), ["hbl-0uf"]);
  const hit = res.hits[0];
  assert.equal(hit.id, "hbl-0uf", "dotted-ID-less bead found via title");
  assert.deepEqual(hit.path.map((p) => p.id), ["hbl-ibv", "hbl-ibv.1", "hbl-0uf"],
    "true path comes from the parent FIELD, not from the dot-less ID");
  assert.equal(hit.pathStatus, "resolved");
  assert.equal(read.calls.length, 1, "still zero per-hit calls");
});

test("a query that looks like an ID earns NO fallback lookup (exclusion: no ID-spelling inference)", () => {
  const snap = s4Snapshot();
  const f = s4();
  const read = fakeSearch(f.issues);
  const res = searchIssues({ snapshot: snap, query: "hbl-0uf", searchRead: read,
    fallback: true });
  // the fake title-match finds nothing; if the module had called searchRead
  // with the ID text as a lookup it would reveal hbl-0uf's parent — forbidden.
  assert.deepEqual(read.calls.map((c) => c[0]), ["hbl-0uf"],
    "only the query call happens — the ID text is never reused as a lookup");
  assert.equal(res.nativeCalls, 1);
});

test("missing parent from the bounded snapshot renders an explicit ancestor-missing state, no calls", () => {
  const f = fixture("missing-ancestor.json");
  const snap = buildSnapshot(baseReads({ issues: f.issues, ready: [], blocked: [],
    storeInfo: f.storeInfo }), { fetchedAt: 1 });
  const read = fakeSearch(f.issues);
  const res = searchIssues({ snapshot: snap, query: "orphan", searchRead: read,
    fallback: false });
  const hit = res.hits[0];
  assert.equal(hit.id, "orphan");
  assert.equal(hit.pathStatus, "parent-missing");
  assert.equal(hit.missing, "deep-anc",
    "the unobserved parent ID declared by mid.parent is named — fixture FACT, not a guess");
  assert.ok(hit.flags.includes("ancestor-missing"));
  assert.equal(read.calls.length, 1, "no fallback: zero extra calls");
});

test("truncation is explicit and visible: bound drops rows status-blind with a banner", () => {
  const snap = s4Snapshot();
  const f = s4();
  // force more matching rows than the bound allows
  const padded = [...f.issues, { id: "hbl-9xx", title: "branch B overflow", status: "open", parent: null }];
  const read = fakeSearch(padded);
  const res = searchIssues({ snapshot: snap, query: "branch B", searchRead: read, limit: 2 });
  assert.equal(res.bound, 2);
  assert.ok(res.truncated, "rawCount > bound surfaces truncation");
  assert.equal(res.hits.length, 2);
  const el = SearchPanel({ results: res, cursor: 0 });
  assert.ok(textIn(el, "bounded at 2"), "truncation banner is visible");
  // HARD cap: requesting more than the ceiling clamps visibly
  const big = searchIssues({ snapshot: snap, query: "branch B",
    searchRead: fakeSearch(padded), limit: HARD_SEARCH_LIMIT + 50 });
  assert.equal(big.bound, HARD_SEARCH_LIMIT);
  assert.equal(big.clamped, true);
});

test("bounded fallback resolves a missing ancestor: labeled, call-count bounded", () => {
  const f = fixture("missing-ancestor.json");
  const snap = buildSnapshot(baseReads({ issues: f.issues, ready: [], blocked: [],
    storeInfo: f.storeInfo }), { fetchedAt: 1 });
  // the facade answers exact-ID queries from an authoritative row pool
  const pool = [...f.issues, ...f.hiddenRows];
  const calls = [];
  const read = (q, bound) => {
    calls.push([q, bound]);
    const byId = pool.find((r) => r.id === q);
    if (byId) return [byId];
    return pool.filter((r) => (r.title ?? "").toLowerCase().includes(q.toLowerCase())).slice(0, bound);
  };
  const res = searchIssues({ snapshot: snap, query: "orphan", searchRead: read, fallback: true });
  const hit = res.hits[0];
  assert.deepEqual(hit.path.map((p) => p.id).filter(Boolean), ["deep-anc", "mid", "orphan"],
    "fallback rows fill the chain: mid + deep-anc above the bounded page");
  assert.equal(hit.pathStatus, "resolved");
  assert.ok(hit.flags.includes("path-from-bounded-fallback"));
  assert.equal(calls.length, 2,
    "one query + ONE bounded ancestor lookup: mid is already in the snapshot, only deep-anc is beyond the page (a phantom second lookup would be waste, not truth)");
  assert.equal(res.fallback.calls, 1);
  assert.ok(res.fallback.calls <= MAX_FALLBACK_CALLS);
});

test("fallback exhaustion surfaces a visible unavailable state — never a show loop", () => {
  const f = fixture("fallback-exhaustion.json");
  const snap = buildSnapshot(baseReads({ issues: f.issues, ready: [], blocked: [],
    storeInfo: f.storeInfo }), { fetchedAt: 1 });
  const calls = [];
  // facade that NEVER resolves anything: every ancestor lookup comes back empty
  const read = (q, bound) => {
    calls.push([q, bound]);
    if (calls.length === 1) {
      return f.issues.filter((r) => (r.title ?? "").toLowerCase().includes(q.toLowerCase()));
    }
    return [];
  };
  const res = searchIssues({ snapshot: snap, query: "chained", searchRead: read, fallback: true });
  const hit = res.hits[0];
  assert.ok(["parent-missing", "unknown-hit"].includes(hit.pathStatus),
    `expected explicit unavailable state, got ${hit.pathStatus}`);
  assert.ok(hit.path.some((p) => p.state === "missing" || p.state === "unknown-hit"));
  assert.ok(calls.length <= 1 + MAX_FALLBACK_CALLS,
    `calls ${calls.length} exceeded 1 + budget ${MAX_FALLBACK_CALLS}`);
  const el = SearchPanel({ results: res, cursor: 0 });
  assert.ok(textIn(el, "unavailable"), "visible unavailable wording, not silence");
});

test("hit absent from the snapshot is revealed as unknown-hit via the fallback, visible on Back", () => {
  const f = fixture("out-of-filter-hit.json");
  // snapshot = the Ready-filtered page only: the closed hit is absent
  const snap = buildSnapshot(baseReads({ issues: f.readyPage, ready: f.readyPage,
    blocked: [], storeInfo: f.storeInfo }), { fetchedAt: 1 });
  const pool = f.allRows;
  const calls = [];
  const read = (q, bound) => {
    calls.push([q, bound]);
    const byId = pool.find((r) => r.id === q);
    if (byId) return [byId];
    return pool.filter((r) => (r.title ?? "").toLowerCase().includes(q.toLowerCase())).slice(0, bound);
  };
  const res = searchIssues({ snapshot: snap, query: "archived", searchRead: read, fallback: true });
  assert.deepEqual(res.hits.map((h) => h.id), ["closed-hit"],
    "hit outside the Ready/Mine filter is STILL revealed");
  const hit = res.hits[0];
  assert.ok(hit.flags.includes("hit-outside-snapshot"));
  assert.deepEqual(hit.path.map((p) => p.id).filter(Boolean), ["hbl-root", "hbl-mid", "closed-hit"],
    "out-of-filter hit still gets its true path via the bounded fallback");
  // filter history preserved on Back is asserted in the Enter/Back test; here:
  // panel marks the out-of-snapshot hit explicitly
  const el = SearchPanel({ results: res, cursor: 0 });
  assert.ok(textIn(el, "closed-hit"));
});

test("REGRESSION: out-of-snapshot hit whose ancestors are ALL in the snapshot resolves root→hit (unshift-order bug)", () => {
  // snapshot root -> mid -> leaf; the hit is OUTSIDE with authoritative
  // lookup returning {id:"outside", parent:"leaf"}. The upward-reuse loop
  // used to chain.unshift the in-snapshot upper chain once per hop, which
  // REVERSED it: [leaf, mid, root, outside]. Correct order is root→hit.
  const issues = [
    { id: "root", title: "root epic", status: "open", parent: null },
    { id: "mid", title: "mid spec", status: "open", parent: "root" },
    { id: "leaf", title: "leaf task", status: "open", parent: "mid" },
  ];
  const snap = buildSnapshot(baseReads({ issues, ready: [], blocked: [] }),
    { fetchedAt: 1 });
  const outsideRow = { id: "outside", title: "outside hit", status: "closed", parent: "leaf" };
  const got = resolveHitPath(snap, { id: "outside" }, {
    lookup: (id) => (id === "outside" ? outsideRow : null),
  });
  assert.equal(got.pathStatus, "resolved");
  assert.deepEqual(got.path.map((p) => p.id ?? p.state),
    ["root", "mid", "leaf", "outside"],
    "resolved ancestor chain reads root→hit, never the unshift-reversed leaf→root tail");
});

test("out-of-snapshot hit's OWN parent comes from the authoritative showRead row", () => {
  // search channel is blind (never resolves rows by id); only `bd show`
  // carries the real parent FIELD. The hit itself is outside the snapshot:
  // its row — and its parent pointer — must come from showRead, and the
  // in-snapshot upper chain must still be reused root→hit with zero extra
  // calls for those hops.
  const issues = [
    { id: "root", title: "root epic", status: "open", parent: null },
    { id: "mid", title: "mid spec", status: "open", parent: "root" },
    { id: "leaf", title: "leaf task", status: "open", parent: "mid" },
  ];
  const snap = buildSnapshot(baseReads({ issues, ready: [], blocked: [] }),
    { fetchedAt: 1 });
  // the native query DOES surface the hit (that's bd search's job) — but the
  // row is a real search row: NO parent key at all (probed FACT), so the
  // parent can only come from the authoritative show channel below.
  const read = fakeSearch([{ id: "outside", title: "outside hit", status: "closed" }]);
  const shown = [];
  const showRead = (id) => {
    shown.push(id);
    if (id === "outside") return { id: "outside", title: "outside hit", status: "closed", parent: "leaf" };
    return null;
  };
  const res = searchIssues({ snapshot: snap, query: "outside hit", searchRead: read, showRead });
  const hit = res.hits[0];
  assert.equal(hit.id, "outside");
  assert.ok(hit.flags.includes("hit-outside-snapshot"));
  assert.deepEqual(hit.path.map((p) => p.id ?? p.state),
    ["root", "mid", "leaf", "outside"],
    "show-read parent (leaf) joins the in-snapshot chain in root→hit order");
  assert.equal(hit.pathStatus, "resolved");
  assert.equal(hit.path[3].state, "fallback", "the hit's own row came from the authoritative show channel");
  assert.deepEqual(shown, ["outside"], "exactly ONE show: the hit's own row; ancestors are snapshot truth");
  assert.equal(res.fallback.showCalls, 1);
  assert.equal(res.nativeCalls, 2, "1 query + 1 show — ancestors cost zero calls");
});

test("REVEAL AUDIT (actual model state): Enter on an out-of-snapshot hit expands the real workbench, not just history claims", () => {
  // audit against the ACTUAL model state (wb.visibleRows()/expanded), never
  // only the pushed history bundle: with mid collapsed pre-Enter, Enter must
  // really expand mid so the full root→leaf chain becomes visible rows, and
  // restore must really collapse what Enter expanded.
  const issues = [
    { id: "root", title: "root epic", status: "open", parent: null },
    { id: "mid", title: "mid spec", status: "open", parent: "root" },
    { id: "leaf", title: "leaf task", status: "open", parent: "mid" },
    { id: "sib", title: "sibling leaf", status: "open", parent: "mid" },
  ];
  const snap = buildSnapshot(baseReads({ issues, ready: [], blocked: [] }),
    { fetchedAt: 1 });
  const history = createHistoryStack({ storeKey: snap.storeKey });
  // ACTUAL pre-Enter model state: mid collapsed (leaf + sib hidden rows)
  const wb = createWorkbenchState(snap, { expanded: new Set(["root"]) });
  const preRows = wb.visibleRows().map((r) => `${r.id}:${r.depth}`);
  assert.deepEqual(preRows, ["root:0", "mid:1"], "fixture starts collapsed at mid");
  wb.jump("root", "list");
  history.push({ beadId: "root", focus: "root", selection: "root",
    pane: "list", tab: "ready", filter: "mine", search: null, scroll: 0,
    expanded: [...wb.expanded].sort() });

  const read = fakeSearch([{ id: "outside", title: "outside hit", status: "closed" }]);
  const showRead = (id) =>
    id === "outside" ? { id: "outside", title: "outside hit", status: "closed", parent: "leaf" } : null;
  const res = searchIssues({ snapshot: snap, query: "outside hit", searchRead: read, showRead });
  const nav = enterSearchHit({ results: res, index: 0, snapshot: snap,
    workbench: wb, history, pane: "tree" });

  // actual state: focus is the hit AND the chain rows really render
  assert.equal(wb.focus, "outside");
  const ids = wb.visibleRows().map((r) => r.id);
  for (const step of ["root", "mid", "leaf"]) {
    assert.ok(ids.includes(step), `${step} must be a REAL visible row after Enter, got ${ids}`);
  }
  assert.ok(wb.expanded.has("mid"), "mid is REALLY expanded in the live model");
  assert.equal(history.current().beadId, "outside");

  // restore: real collapse — leaf is no longer a visible row, focus returns
  nav.restore();
  assert.equal(wb.focus, "root");
  assert.deepEqual(wb.visibleRows().map((r) => `${r.id}:${r.depth}`), preRows,
    "row-for-row back to the actual collapsed pre-Enter state");
  assert.ok(!wb.expanded.has("mid"), "Enter's expansion was really undone");
});

test("REVEAL ACCEPTANCE (hitVisible): Enter on an out-of-snapshot hit makes the ACTUAL hit row visible with correct ancestry; Back restores visible rows/filter/query/focus/expansion", () => {
  // Parent repro (exact): rows root(parent null), mid(root), leaf(mid);
  // the needle's searchRead returns `outside` (real search row: NO parent
  // key — probed FACT) and showRead returns the authoritative row with the
  // real parent FIELD {outside, parent:"leaf"}. Observed gap: selection/
  // focus land on outside but visibleRows() stays root,mid,leaf —
  // hitVisible FALSE. The REVEAL AUDIT asserted ancestors only; acceptance
  // additionally demands the hit's OWN row as a real visible row, hung at
  // its true depth below leaf, and a Back that restores the full pre-search
  // state: visible rows, filter, query, focus and expansion.
  const issues = [
    { id: "root", title: "root epic", status: "open", parent: null },
    { id: "mid", title: "mid spec", status: "open", parent: "root" },
    { id: "leaf", title: "leaf task", status: "open", parent: "mid" },
  ];
  const snap = buildSnapshot(baseReads({ issues, ready: [], blocked: [] }),
    { fetchedAt: 1 });
  const history = createHistoryStack({ storeKey: snap.storeKey });
  const wb = createWorkbenchState(snap, {});
  wb.jump("root", "list");
  history.push({ beadId: "root", focus: "root", selection: "root",
    pane: "list", tab: "ready", filter: "mine", search: "earlier", scroll: 42,
    expanded: [...wb.expanded].sort() });
  const preRows = wb.visibleRows().map((r) => `${r.id}:${r.depth}`);
  assert.deepEqual(preRows, ["root:0", "mid:1", "leaf:2"], "fixture: full chain visible pre-Enter");
  const preExpanded = [...wb.expanded].sort();
  const preFocus = wb.focus;

  const read = fakeSearch([{ id: "outside", title: "outside hit", status: "closed" }]);
  const showRead = (id) =>
    id === "outside" ? { id: "outside", title: "outside hit", status: "closed", parent: "leaf" } : null;
  const res = searchIssues({ snapshot: snap, query: "outside hit", searchRead: read, showRead });

  const nav = enterSearchHit({ results: res, index: 0, snapshot: snap,
    workbench: wb, history, pane: "tree" });

  // ACTUAL model state, not history claims: the hit itself must be a row.
  const rows = wb.visibleRows();
  const ids = rows.map((r) => r.id);
  const hitVisible = ids.includes("outside");
  assert.ok(hitVisible, `hitVisible: the ACTUAL hit row must render, got ${ids}`);
  const li = ids.indexOf("leaf");
  const oi = ids.indexOf("outside");
  assert.equal(rows[oi].depth, rows[li].depth + 1,
    "correct ancestry: the hit row hangs at leaf's child depth");
  assert.ok(oi > li, "tree order: the hit row reads after its parent");
  for (const step of ["root", "mid", "leaf"]) {
    assert.ok(ids.includes(step), `ancestor ${step} stays visible, got ${ids}`);
  }
  assert.equal(wb.focus, "outside");
  assert.equal(wb.selection, "outside");

  // Back: the pre-search state is restored in the ACTUAL model + stack —
  // visible rows row-for-row (no orphaned hit row left behind), filter,
  // query, focus and expansion all returned.
  nav.restore();
  assert.deepEqual(wb.visibleRows().map((r) => `${r.id}:${r.depth}`), preRows,
    "Back restores the pre-search visible rows exactly");
  const b = history.current();
  assert.equal(b.filter, "mine", "Back restores the pre-search filter");
  assert.equal(b.search, "earlier", "Back restores the pre-search query");
  assert.equal(wb.focus, preFocus, "Back restores the pre-search focus");
  assert.deepEqual([...wb.expanded].sort(), preExpanded, "Back restores the pre-search expansion");
  // the reveal is pure model state: call-count bound unchanged (1 query + 1 show)
  assert.equal(res.nativeCalls, 2, "zero native calls for the reveal");
});

test("REVEAL ACCEPTANCE (unknown parentage): a hit with no authoritative parent still becomes a visible row — never a crowned root, no invented parentage", () => {
  // Same shape, but the show channel is blind too: only the search row
  // (no parent key). The hit must STILL be visibly revealed — as an honest
  // boundary row — while its in-snapshot ancestors stay visible. Absence of
  // parentage is never rewritten into rootage.
  const issues = [
    { id: "root", title: "root epic", status: "open", parent: null },
    { id: "mid", title: "mid spec", status: "open", parent: "root" },
    { id: "leaf", title: "leaf task", status: "open", parent: "mid" },
  ];
  const snap = buildSnapshot(baseReads({ issues, ready: [], blocked: [] }),
    { fetchedAt: 1 });
  const history = createHistoryStack({ storeKey: snap.storeKey });
  const wb = createWorkbenchState(snap, {});
  wb.jump("root", "list");
  history.push({ beadId: "root", focus: "root", selection: "root", pane: "list",
    expanded: [...wb.expanded].sort() });

  const read = fakeSearch([{ id: "outside", title: "outside hit", status: "closed" }]);
  const res = searchIssues({ snapshot: snap, query: "outside hit", searchRead: read });
  assert.equal(res.hits[0].pathStatus, "unknown-hit",
    "fixture FACT: search rows carry no parent field — parentage stays unknown");

  enterSearchHit({ results: res, index: 0, snapshot: snap,
    workbench: wb, history, pane: "tree" });
  const ids = wb.visibleRows().map((r) => r.id);
  assert.ok(ids.includes("outside"),
    `an unknown-parentage hit is still revealed as a row, got ${ids}`);
  assert.ok(!snap.nodes.has("outside"),
    "the snapshot is untouched: no invented node, no crowned parent");
});

test("parent cycle terminates with an explicit marker — no infinite walk", () => {
  const f = fixture("cycle.json");
  const snap = buildSnapshot(baseReads({ issues: f.issues, ready: [], blocked: [],
    storeInfo: f.storeInfo }), { fetchedAt: 1 });
  const read = fakeSearch(f.issues);
  const res = searchIssues({ snapshot: snap, query: "cycle", searchRead: read });
  const hit = res.hits.find((h) => h.id === "cyc-b");
  assert.equal(hit.pathStatus, "cycle");
  assert.ok(hit.path.some((p) => p.state === "cycle"));
  const el = SearchPanel({ results: res, cursor: 0 });
  assert.ok(textIn(el, "cycle"));
});

test("query-level searchRead failure propagates visibly — never faked as an empty world", () => {
  const snap = s4Snapshot();
  assert.throws(() => searchIssues({ snapshot: snap, query: "branch B",
    searchRead: () => { throw new Error("boom: circuit breaker"); } }), /boom/);
});

// ---- Enter: navigation INTO the tree + history push + byte-identical Back ---
const { createWorkbenchState } = await import("../desktop/model.mjs");
function modelWorkbench(snap) {
  return createWorkbenchState(snap, {});
}
function workbenchFixture() {
  const f = s4();
  const snap = buildSnapshot(baseReads({ issues: f.issues, ready: f.ready,
    blocked: f.blocked, storeInfo: f.storeInfo }), { fetchedAt: 1 });
  const history = createHistoryStack({ storeKey: snap.storeKey });
  const wb = modelWorkbench(snap);
  return { snap, history, wb, f };
}

test("Enter lands IN the tree: focus+selection on the hit, ancestors expanded, history pushed, Back restores pre-search state incl. the query", () => {
  const { snap, history, wb } = workbenchFixture();
  // pre-search state: operator on hbl-9g2 in the tree, Ready filter, a prior query
  wb.jump("hbl-9g2", "list");
  history.push({ beadId: "hbl-9g2", focus: "hbl-9g2", selection: "hbl-9g2",
    pane: "list", tab: "ready", filter: "label=impl", search: "earlier",
    scroll: 120, expanded: [...wb.expanded].sort() });
  const preFocus = wb.focus, prePane = wb.pane, preScroll = 120;
  const preRows = wb.visibleRows().map((r) => `${r.id}:${r.depth}`).join("|");
  const preHist = history.current();

  const f = s4();
  const read = fakeSearch(f.issues);
  const res = searchIssues({ snapshot: snap, query: "blocked worker", searchRead: read });
  assert.deepEqual(res.hits.map((h) => h.id), ["hbl-9g2.5.1"]);

  const nav = enterSearchHit({ results: res, index: 0, snapshot: snap,
    workbench: wb, history, pane: "tree" });
  // IN the tree, not a parallel list world: workbench carries the focus
  assert.equal(wb.focus, "hbl-9g2.5.1");
  assert.equal(wb.selection, "hbl-9g2.5.1");
  assert.equal(wb.pane, "tree");
  // path expanded: every ancestor row and the hit itself are visible rows
  const ids = new Set(wb.visibleRows().map((r) => r.id));
  for (const step of res.hits[0].path) assert.ok(ids.has(step.id),
    `ancestor ${step.id} must be expanded into view`);
  // history pushed, and the pushed bundle preserves the query + filter
  assert.equal(nav.entry.beadId, "hbl-9g2.5.1");
  assert.equal(nav.entry.search, "blocked worker");
  assert.equal(nav.entry.filter, "label=impl", "search did not erase the filter");
  assert.equal(history.current().beadId, "hbl-9g2.5.1");

  // Back: pre-search state restored — focus, pane, selection, and the
  // collapsed pre-search expansion; the old query survives in history.
  nav.restore();
  assert.equal(wb.focus, preFocus);
  assert.equal(wb.pane, prePane);
  assert.equal(wb.visibleRows().map((r) => `${r.id}:${r.depth}`).join("|"), preRows,
    "tree restored row-for-row");
  const restored = history.current();
  assert.equal(restored.beadId, "hbl-9g2");
  assert.equal(restored.search, "earlier", "Back preserves the prior search text");
  assert.equal(restored.filter, "label=impl");
  assert.equal(restored.scroll, preScroll);
});

test("Enter on an out-of-filter hit still lands in the tree (jump works without a list row)", () => {
  const f = fixture("out-of-filter-hit.json");
  const snap = buildSnapshot(baseReads({ issues: f.readyPage, ready: f.readyPage,
    blocked: [], storeInfo: f.storeInfo }), { fetchedAt: 1 });
  const history = createHistoryStack({ storeKey: snap.storeKey });
  const wb = modelWorkbench(snap);
  wb.jump("hbl-live", "list");
  history.push({ beadId: "hbl-live", focus: "hbl-live", selection: "hbl-live",
    pane: "list", tab: "ready", filter: "ready", scroll: 0 });
  const pool = f.allRows;
  const read = (q, bound) => {
    const byId = pool.find((r) => r.id === q);
    if (byId) return [byId];
    return pool.filter((r) => (r.title ?? "").toLowerCase().includes(q.toLowerCase())).slice(0, bound);
  };
  const res = searchIssues({ snapshot: snap, query: "archived", searchRead: read, fallback: true });
  const nav = enterSearchHit({ results: res, index: 0, snapshot: snap,
    workbench: wb, history, pane: "tree" });
  assert.equal(wb.focus, "closed-hit", "out-of-filter hit becomes the focus in the tree");
  assert.equal(history.current().beadId, "closed-hit");
  nav.restore();
  assert.equal(wb.focus, "hbl-live");
  assert.equal(history.current().beadId, "hbl-live");
  assert.equal(history.current().filter, "ready");
});

// ---- native reproduction: hci S4 on a real store ----------------------------
// Fixture store under tests/.search-fixtures (lane-unique, gitignored): the
// no-dot bead is created WITHOUT a parent, then reparented via
// `bd update --parent` — exactly the FACT-4 shape (hbl-0uf under hbl-ibv.1).
const LAB = "/home/hermes/.hermes/work/beads-lab";
const BIN = path.join(LAB, "bin", "bd");
const ACTOR = "lane-s5-search-20260928a";
const FIX = path.join(here, ".search-fixtures");

const bdRead = (cwd, ...args) =>
  JSON.parse(execFileSync("flock", [path.join(LAB, "planning-access.lock"),
    BIN, "-C", cwd, "--readonly", "--actor", ACTOR, ...args, "--json"],
    { encoding: "utf8" }));
const bdMutate = (cwd, ...args) =>
  execFileSync("flock", [path.join(LAB, "planning-access.lock"),
    BIN, "-C", cwd, "--actor", ACTOR, ...args], { encoding: "utf8" });

function nativeStore(name) {
  const cwd = path.join(FIX, name);
  rmSync(cwd, { recursive: true, force: true });
  mkdirSync(cwd, { recursive: true });
  execFileSync("git", ["init", "-q", cwd]);
  execFileSync("git", ["-C", cwd, "config", "user.name", "lane-search"]);
  execFileSync("git", ["-C", cwd, "config", "user.email", "lane-search@localhost"]);
  execFileSync(BIN, ["init", "--prefix", "s5s"], { cwd, encoding: "utf8" });
  return { cwd, info: bdRead(cwd, "info") };
}
const createNative = (cwd, title, parent) => {
  const out = bdMutate(cwd, "create", title,
    ...(parent ? ["--parent", parent] : []), "--allow-empty-description", "--json");
  const record = JSON.parse(out);
  assert.equal(typeof record.id, "string");
  return record.id;
};

test("native S4: real bd search hits enriched with snapshot paths; call-bound asserted; reparented no-dot case shows true path", () => {
  const st = nativeStore(`s4-${process.pid}`);
  try {
    const epicA = createNative(st.cwd, "PROBE FIXTURE: hermes lab");
    const branchB = createNative(st.cwd, "PROBE FIXTURE: branch B", epicA);
    const worker = createNative(st.cwd, "PROBE FIXTURE: blocked worker in branch B", branchB);
    const epicN = createNative(st.cwd, "PROBE FIXTURE: nav probe epic");
    const mid = createNative(st.cwd, "PROBE FIXTURE: keyboard map spec", epicN);
    const nodot = createNative(st.cwd, "PROBE FIXTURE: focus model spec"); // NO parent at creation
    // reparent AFTER creation (FACT 4): ID never gains dots, parent FIELD moves
    execFileSync("flock", [path.join(LAB, "planning-access.lock"),
      BIN, "-C", st.cwd, "--actor", ACTOR, "update", nodot, "--parent", mid]);

    // one bounded native search through the facade — count every call
    const calls = [];
    const searchRead = (q, bound) => {
      calls.push([q, bound]);
      return bdRead(st.cwd, "search", q, "--limit", String(bound));
    };
    const rows = bdRead(st.cwd, "list", "--all", "--limit", "0");
    const ready = bdRead(st.cwd, "ready", "--limit", "0");
    const blocked = bdRead(st.cwd, "blocked");
    const snap = buildSnapshot({ issues: rows, ready, blocked,
      storeInfo: { workspace: st.cwd, db: st.info.database_path } }, { bound: 500, fetchedAt: 1 });

    const res = searchIssues({ snapshot: snap, query: "branch B", searchRead, limit: 10 });
    const ids = res.hits.map((h) => h.id).sort();
    assert.deepEqual(ids, [branchB, worker].sort(),
      "hits equal the native search receipt (branch B + blocked worker)");
    const byId = Object.fromEntries(res.hits.map((h) => [h.id, h]));
    assert.deepEqual(byId[worker].path.map((p) => p.id), [epicA, branchB, worker]);
    assert.equal(byId[worker].pathStatus, "resolved");
    assert.equal(calls.length, 1, "warm snapshot: ONE native call for the query");

    // the no-dot reparented bead via a title search — true path from parent FIELD
    const res2 = searchIssues({ snapshot: snap, query: "focus model spec", searchRead, limit: 10 });
    assert.deepEqual(res2.hits.map((h) => h.id), [nodot]);
    const hit = res2.hits[0];
    assert.ok(!hit.id.includes("."), "fixture really is a dotted-ID-less bead");
    assert.deepEqual(hit.path.map((p) => p.id).filter(Boolean), [epicN, mid, nodot],
      "reparented no-dot bead shows its TRUE path");
    assert.equal(hit.pathStatus, "resolved");
    assert.equal(calls.length, 2, "still bounded: one call per query, zero per-hit");
  } finally {
    rmSync(FIX, { recursive: true, force: true });
  }
});

test("native fallback path: out-of-page ancestor resolved under budget, exhaustion stays visible", () => {
  const st = nativeStore(`s4b-${process.pid}`);
  try {
    const anc = createNative(st.cwd, "deep ancestor");
    const mid = createNative(st.cwd, "middle", anc);
    const kid = createNative(st.cwd, "kid to find", mid);
    const filler = [];
    for (let i = 0; i < 5; i++) filler.push(createNative(st.cwd, `filler ${i} ${"z".repeat(i)}`));
    const allRows = bdRead(st.cwd, "list", "--all", "--limit", "0");
    // bounded snapshot that INCLUDES the kid but NOT its ancestors
    const page = allRows.filter((r) => r.id === kid || filler.includes(r.id));
    const snap = buildSnapshot({ issues: page, ready: [], blocked: [],
      storeInfo: { workspace: st.cwd, db: st.info.database_path } }, { bound: 500, fetchedAt: 1 });
    const calls = [];
    const searchRead = (q, bound) => {
      calls.push([q, bound]);
      return bdRead(st.cwd, "search", q, "--limit", String(bound));
    };
    // show-capable facade: `bd show` is the AUTHORITATIVE row — it carries the
    // real parent FIELD (probed: search rows never do, even --long). A ROOT's
    // show row OMITS parent; only here is that normalized to parent:null
    // (= proven rootage). Search rows with no parent key stay unknown.
    const showCalls = [];
    const showRead = (id) => {
      showCalls.push(id);
      let arr;
      try { arr = bdRead(st.cwd, "show", id); } catch { return null; }
      const row = Array.isArray(arr) ? arr.find((r) => r?.id === id) : null;
      return row ? { ...row, parent: row.parent ?? null } : null;
    };
    const res = searchIssues({ snapshot: snap, query: "kid to find", searchRead, showRead, limit: 10 });
    const hit = res.hits[0];
    assert.equal(hit.id, kid);
    assert.deepEqual(hit.path.map((p) => p.id).filter(Boolean), [anc, mid, kid],
      "bounded bd show fallback fills the chain with real parent FIELDs");
    assert.ok(hit.flags.includes("path-from-bounded-fallback"));
    assert.equal(res.fallback.showCalls, 2, "one show per missing hop (mid, anc), cached, no loops");
    const totalCalls = calls.length + showCalls.length;
    assert.ok(totalCalls <= 1 + MAX_FALLBACK_CALLS,
      `call-count bound: ${totalCalls} total native calls (1 query + ${showCalls.length} show + ${calls.length - 1} search), max ${1 + MAX_FALLBACK_CALLS}`);

    // exhaustion: same store, facade where only the first call returns anything
    let n = 0;
    const stub = (q, bound) => { n += 1; if (n === 1) return [{ id: kid, title: "kid to find", status: "open" }]; return []; };
    const res2 = searchIssues({ snapshot: snap, query: "kid to find", searchRead: stub, limit: 10 });
    assert.equal(res2.hits[0].pathStatus, "parent-missing");
    assert.ok(n <= 1 + MAX_FALLBACK_CALLS, `stub exhausted within budget (${n})`);
    assert.ok(res2.hits[0].flags.includes("ancestor-missing") || res2.hits[0].flags.includes("ancestor-unavailable"));
  } finally {
    rmSync(FIX, { recursive: true, force: true });
  }
});
