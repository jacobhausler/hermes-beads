// tests/test_history.mjs — hbl-pnu.2.3: clickable breadcrumb (parent-field
// chain, re-resolved live) + INDEPENDENT history stack with full restore
// bundle (filter/search/tab/pane/selection/focus/scroll/expansion),
// immutability, and workspace isolation.
// Run: node --test tests/test_history.mjs   (Node built-in runner, no deps)
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync, mkdirSync, rmSync } from "node:fs";
import { execFileSync } from "node:child_process";
import { register } from "node:module";
import { fileURLToPath, pathToFileURL } from "node:url";
import path from "node:path";

const here = path.dirname(fileURLToPath(import.meta.url));
// map react/jsx-runtime (the app's loader does the same) onto the capture
// shim so pure components render to plain records under node.
register(pathToFileURL(path.join(here, "__shims__", "jsx-loader.mjs")).href);

const shim = await import(pathToFileURL(path.join(here, "__shims__", "jsx-capture.mjs")).href);
const {
  createHistoryStack, Breadcrumb, HistoryPanel, trailFromSnapshot,
} = await import("../desktop/history.mjs");
const { buildSnapshot } = await import("../desktop/model.mjs");

// ---- helpers ----------------------------------------------------------------
const fixture = (name) =>
  JSON.parse(readFileSync(path.join(here, "fixtures", "history", name), "utf8"));

const baseReads = (over = {}) => ({
  issues: [], ready: [], blocked: [],
  storeInfo: { workspace: "/lab/store", db: "/lab/store/.beads/lab.db" },
  ...over,
});

const walk = (t) => [...shim.walk(t)];
// substring match over rendered text nodes (labels are joined " · " strings)
const textIn = (tree, s) => walk(tree).some((n) => typeof n === "string" && n.includes(s));
const findById = (tree, id) =>
  walk(tree).filter((n) => typeof n === "object" && n.props?.id === id);

const FULL_BUNDLE = {
  storeKey: "WS-A", beadId: "root",
  filter: "label=impl", search: "breadcrumb", tab: "tree", pane: "detail",
  selection: "a", focus: "deep1", scroll: 120,
  expanded: ["epic", "root"],
};

// ---- history stack -----------------------------------------------------------
test("history stack: push/back/forward index semantics + forward-tail truncation", () => {
  const h = createHistoryStack({ storeKey: "WS-A" });
  assert.equal(h.current(), null);
  assert.equal(h.canBack(), false);
  assert.equal(h.canForward(), false);
  h.push({ beadId: "x" });
  h.push({ beadId: "y" });
  h.push({ beadId: "z" });
  assert.equal(h.index, 2);
  assert.deepEqual(h.entries().map((e) => e.beadId), ["x", "y", "z"]);
  assert.equal(h.back().beadId, "y");
  assert.equal(h.back().beadId, "x");
  assert.equal(h.back(), null); // bottom: stays, returns null
  assert.equal(h.index, 0);
  assert.equal(h.forward().beadId, "y");
  h.push({ beadId: "w" }); // push after back drops the forward tail
  assert.deepEqual(h.entries().map((e) => e.beadId), ["x", "y", "w"]);
  assert.equal(h.canForward(), false);
  assert.equal(h.forward(), null);
});

test("restore bundle carries EVERY contract field incl filter/search/tab/pane/focus/scroll/expansion", () => {
  const h = createHistoryStack({ storeKey: "WS-A" });
  h.push(FULL_BUNDLE);
  const e = h.current();
  for (const f of ["storeKey", "beadId", "filter", "search", "tab", "pane",
                   "selection", "focus", "scroll", "expanded", "draft"])
    assert.ok(f in e, `bundle must carry ${f}`);
  assert.deepEqual(e, { ...FULL_BUNDLE, draft: null });
  const bare = h.push({}); // missing fields normalize, no undefined holes
  assert.equal(bare.beadId, null);
  assert.equal(bare.filter, null);
  assert.equal(bare.scroll, 0);
  assert.deepEqual(bare.expanded, []);
});

test("immutable restore: entries are frozen deep clones; caller mutation cannot poison the stack", () => {
  const h = createHistoryStack({ storeKey: "WS-A" });
  const original = { ...FULL_BUNDLE, expanded: ["epic", "root"] };
  h.push(original);
  const got = h.current();
  assert.ok(Object.isFrozen(got), "restored bundle must be frozen");
  assert.ok(Object.isFrozen(got.expanded), "nested expansion list frozen too");
  assert.throws(() => { got.focus = "evil"; }, TypeError);
  assert.throws(() => { got.expanded.push("evil"); }, TypeError);
  // mutating the caller's pre-push objects must not leak in either
  original.expanded.push("poison");
  original.focus = "caller-changed";
  assert.deepEqual(h.current().expanded, ["epic", "root"]);
  assert.equal(h.current().focus, "deep1");
  // the clone handed back equals the pre-jump snapshot exactly
  assert.deepEqual(h.current(), { ...FULL_BUNDLE, draft: null });
});

test("history Set expansion normalizes; capacity bounds keep the current position", () => {
  const h = createHistoryStack({ storeKey: "WS-A", capacity: 3 });
  h.push({ beadId: "seed", expanded: new Set(["root", "epic"]) });
  assert.deepEqual(h.current().expanded, ["root", "epic"]);
  for (const id of ["e1", "e2", "e3"]) h.push({ beadId: id });
  assert.deepEqual(h.entries().map((e) => e.beadId), ["e1", "e2", "e3"]);
  assert.equal(h.index, 2);
  assert.equal(h.current().beadId, "e3");
  assert.equal(h.back().beadId, "e2");
});

// ---- workspace isolation ------------------------------------------------------
test("workspace isolation: per-store stacks never share entries, drafts or context", () => {
  const a = createHistoryStack({ storeKey: "WS-A" });
  const b = createHistoryStack({ storeKey: "WS-B" });
  a.push({ ...FULL_BUNDLE, beadId: "a1", draft: "secret from ws A", search: "alpha" });
  b.push({ beadId: "b1", tab: "board" });
  assert.deepEqual(a.entries().map((e) => e.beadId), ["a1"]);
  assert.deepEqual(b.entries().map((e) => e.beadId), ["b1"]);
  assert.equal(b.current().draft, null);   // no draft leak across stores
  assert.equal(b.current().search, null);  // no query leak
  assert.equal(b.current().storeKey, "WS-B");
  assert.equal(a.back(), null);            // independent sequences
  // back into store A restores the FULL pre-jump context untouched
  a.push({ ...FULL_BUNDLE, beadId: "a2" });
  const back = a.back();
  assert.equal(back.beadId, "a1");
  assert.equal(back.draft, "secret from ws A");
  assert.equal(back.filter, "label=impl");
  assert.deepEqual(back.expanded, ["epic", "root"]);
  assert.equal(back.scroll, 120);
});

test("wrong-store bundle is refused, not absorbed", () => {
  const a = createHistoryStack({ storeKey: "WS-A" });
  assert.throws(() => a.push({ storeKey: "WS-B", beadId: "b1" }), /storeKey mismatch/);
});

// ---- breadcrumb: parent-field truth, never ID spelling -------------------------
test("breadcrumb renders the parent-FIELD path while its own ID is dotted-wrong", () => {
  const f = fixture("reparented-dotted.json");
  const s = buildSnapshot(baseReads(f), { fetchedAt: 1 });
  const el = Breadcrumb({ snapshot: s, id: "x.y.z" });
  const t = trailFromSnapshot(s, "x.y.z");
  assert.deepEqual(t.map((c) => c.id), ["branch", "reparented-from-x", "x.y.z"]);
  assert.ok(!t.some((c) => c.id === "x.y"), "ID-dot-inferred parent must not render");
  assert.ok(textIn(el, "x.y.z"), "the bead's own (misleading) ID renders verbatim");
  assert.ok(!textIn(el, "x \u203A y"), "no synthesized x \u203A y hierarchy from spelling");
});

test("breadcrumb re-resolves live after a reparent — same API, fresh snapshot", () => {
  const before = fixture("reparented-dotted.json");
  const s1 = buildSnapshot(baseReads(before), { fetchedAt: 1 });
  assert.deepEqual(trailFromSnapshot(s1, "x.y.z").map((c) => c.id),
    ["branch", "reparented-from-x", "x.y.z"]);
  const after = { ...before, issues: before.issues.map((r) =>
    r.id === "x.y.z" ? { ...r, parent: "branch" } : r) };
  const s2 = buildSnapshot(baseReads(after), { fetchedAt: 2 });
  assert.deepEqual(trailFromSnapshot(s2, "x.y.z").map((c) => c.id), ["branch", "x.y.z"]);
});

test("breadcrumb dots are clickable and jump to exactly the dot's id", () => {
  const f = fixture("reparented-dotted.json");
  const s = buildSnapshot(baseReads(f), { fetchedAt: 1 });
  const jumped = [];
  const el = Breadcrumb({ snapshot: s, id: "x.y.z", onNavigate: (id) => jumped.push(id) });
  const dots = walk(el).filter((n) =>
    typeof n === "object" && /^dot:/.test(String(n.props?.id)));
  assert.equal(dots.length, 3);
  dots[0].props.onClick();
  dots[2].props.onClick();
  assert.deepEqual(jumped, ["branch", "x.y.z"]);
  for (const d of dots) assert.match(d.props.href, /^\/bead\//); // SDK nav pairing
});

test("missing parent stays visible as contextual path with reason; not clickable", () => {
  const f = fixture("missing-parent.json");
  const s = buildSnapshot(baseReads(f), { fetchedAt: 1 });
  assert.equal(s.nodes.get("kid").pathStatus, "parent-missing");
  const el = Breadcrumb({ snapshot: s, id: "kid" });
  const t = trailFromSnapshot(s, "kid");
  assert.deepEqual(t.map((c) => c.id), ["kid", null]);
  assert.equal(t[1].missing, "gone"); // name known, row absent
  assert.ok(textIn(el, "\u2026"), "explicit unknown-context marker rendered");
  assert.ok(textIn(el, "gone"), "missing ancestor's name rendered");
  assert.equal(t[1].href, null);
  assert.equal(t[1].clickable, false);
});

test("filtered-out ancestor surfaces as contextual path with the filter reason", () => {
  const s = buildSnapshot(baseReads({
    issues: { rows: [{ id: "kid", parent: "mid", status: "open", dependencies: [] }],
              filter: "label=impl" },
  }), { fetchedAt: 1 });
  const t = trailFromSnapshot(s, "kid");
  assert.equal(s.nodes.get("kid").pathStatus, "parent-missing");
  assert.equal(t[t.length - 1].missing, "mid");
  assert.match(t[t.length - 1].reason, /filtered-out .*absent . deleted/);
});

test("corrupted/cyclic chain terminates with an explicit error, never a hang", () => {
  const f = fixture("cycle.json");
  const s = buildSnapshot(baseReads(f), { fetchedAt: 1 });
  assert.equal(s.nodes.get("c1").pathStatus, "cycle");
  const el = Breadcrumb({ snapshot: s, id: "c1" });
  const t = trailFromSnapshot(s, "c1");
  assert.deepEqual(t.slice(0, 2).map((c) => c.id), ["c2", "c1"]); // model truth
  assert.equal(t[t.length - 1].error, "parent cycle detected \u2014 chain truncated");
  assert.ok(textIn(el, "\u27f2 cycle"), "cycle error marker rendered");
});

// ---- independent history presentation widget ------------------------------------
test("HistoryPanel renders the stack as its own widget, fed only entries+index", () => {
  const h = createHistoryStack({ storeKey: "store-key-1" });
  h.push({ beadId: "root" });
  h.push({ beadId: "deep1", pane: "detail", tab: "tree" });
  let restored = null;
  const el = HistoryPanel({ entries: h.entries(), index: h.index,
    onRestore: (e) => { restored = e; } });
  assert.equal(findById(el, "history-row-0").length, 1);
  assert.equal(findById(el, "history-row-1").length, 1);
  assert.ok(textIn(el, "deep1"));
  assert.ok(textIn(el, "tree \u203A detail"), "row names tab \u203A pane");
  findById(el, "history-row-0")[0].props.onClick();
  assert.equal(restored.beadId, "root");
});

test("independence: breadcrumb reads no history state; history module keeps zero imports/state", () => {
  const src = readFileSync(path.join(here, "..", "desktop", "history.mjs"), "utf8");
  assert.doesNotMatch(src, /import[^;]*model\.mjs/, "history must not import the model");
  for (const banned of ["window.", "document.", "localStorage", "ctx.rest",
                        "host.", "@hermes/plugin-sdk"])
    assert.ok(!src.includes(banned), `history.mjs must not touch ${banned}`);
  const f = fixture("reparented-dotted.json");
  const s = buildSnapshot(baseReads(f), { fetchedAt: 1 });
  const h = createHistoryStack({ storeKey: "WS" });
  h.push({ beadId: "stale-ancestor" });
  const t = trailFromSnapshot(s, "x.y.z"); // history presence changes nothing
  assert.deepEqual(t.map((c) => c.id), ["branch", "reparented-from-x", "x.y.z"]);
  Breadcrumb({ snapshot: s, id: "x.y.z" });
  assert.ok(!JSON.stringify(shim.last()).includes("stale-ancestor"),
    "breadcrumb render never sees history entries");
});

test("read-once: breadcrumb render performs no new reads (snapshot injected, chain re-derived)", () => {
  const f = fixture("reparented-dotted.json");
  let calls = 0;
  const fn = (v) => () => { calls += 1; return v; };
  const s = buildSnapshot({
    storeInfo: fn({ workspace: "/lab/store", db: "/lab/store/.beads/lab.db" }),
    issues: fn(f.issues), ready: fn([]), blocked: fn([]),
  }, { fetchedAt: 1 });
  assert.equal(calls, 4);
  Breadcrumb({ snapshot: s, id: "x.y.z" });
  Breadcrumb({ snapshot: s, id: "x.y.z" });
  assert.equal(calls, 4, "rendering must not trigger further native reads");
});

// ---- native reproduction: hci reparent case (create-without-parent, --parent move)
const LAB = "/home/hermes/.hermes/work/beads-lab";
const BIN = path.join(LAB, "bin", "bd");
const ACTOR = "lane-sprint3-history-20260928a";
const FIX = path.join(here, ".history-fixtures");

const bdRead = (cwd, ...args) =>
  JSON.parse(execFileSync("flock", [path.join(LAB, "planning-access.lock"),
    BIN, "-C", cwd, "--readonly", "--actor", ACTOR, ...args, "--json"],
    { encoding: "utf8" }));
const bdMutate = (cwd, ...args) =>
  execFileSync("flock", [path.join(LAB, "planning-access.lock"),
    BIN, "-C", cwd, "--actor", ACTOR, ...args], { encoding: "utf8" });

// own `git init` + `bd init --prefix tst` per store (test_native.py pattern):
// without its own .git the embedded dolt home falls through to the parent repo.
function nativeStore(name) {
  const cwd = path.join(FIX, name);
  rmSync(cwd, { recursive: true, force: true });
  mkdirSync(cwd, { recursive: true });
  execFileSync("git", ["init", "-q", cwd]);
  execFileSync("git", ["-C", cwd, "config", "user.name", "lane-test"]);
  execFileSync("git", ["-C", cwd, "config", "user.email", "lane-test@localhost"]);
  execFileSync(BIN, ["init", "--prefix", "tst"], { cwd, encoding: "utf8" });
  return { cwd, info: bdRead(cwd, "info") };
}
function createNative(cwd, title, parent) {
  const out = bdMutate(cwd, "create", title,
    ...(parent ? ["--parent", parent] : []), "--allow-empty-description");
  const m = out.match(/tst-[a-z0-9]+/i);
  assert.ok(m, `create returned no id: ${out}`);
  return m[0];
}
const snapOf = (st, rows, fetchedAt) =>
  buildSnapshot(baseReads({ issues: rows, storeInfo:
    { workspace: st.cwd, db: st.info.database_path } }), { fetchedAt });

test("native reparent (hci case): create without parent, then --parent move — breadcrumb follows the parent FIELD", () => {
  const st = nativeStore(`reparent-${process.pid}`);
  try {
    const p1 = createNative(st.cwd, "lane p1");
    const p2 = createNative(st.cwd, "lane p2");
    const kid = createNative(st.cwd, "lane kid");
    bdMutate(st.cwd, "update", kid, "--parent", p1);
    let rows = bdRead(st.cwd, "list", "--all", "--limit", "0");
    let s = snapOf(st, rows, 1);
    assert.deepEqual(trailFromSnapshot(s, kid).map((c) => c.id), [p1, kid],
      "one-hop native truth renders as a chain, snapshot-composed");
    // the hci move: reparent via the parent FIELD
    bdMutate(st.cwd, "update", kid, "--parent", p2);
    rows = bdRead(st.cwd, "list", "--all", "--limit", "0");
    s = snapOf(st, rows, 2);
    const t = trailFromSnapshot(s, kid);
    assert.deepEqual(t.map((c) => c.id), [p2, kid],
      "breadcrumb must re-resolve to the NEW parent field");
    assert.ok(!t.some((c) => c.id === p1), "old parent must be gone from the trail");
    assert.equal(rows.find((r) => r.id === kid).parent, p2);
  } finally {
    rmSync(FIX, { recursive: true, force: true });
  }
});

test("native delete of the parent: dotted ID survives, parent FIELD is truth (no phantom ancestor)", () => {
  // probed native fact (bd 1.3.0): create --parent rewrites the child ID
  // dotted under the parent, and `bd delete <parent>` without --cascade
  // CLEARS the child's parent FIELD. The child ID still spells ancestry the
  // FIELD denies — the breadcrumb must render the FIELD, never the spelling.
  const st = nativeStore(`del-${process.pid}`);
  try {
    const p1 = createNative(st.cwd, "lane p1");
    createNative(st.cwd, "lane keep"); // sibling so delete cannot cascade
    const kid = createNative(st.cwd, "lane kid", p1);
    bdMutate(st.cwd, "delete", p1, "--force");
    const rows = bdRead(st.cwd, "list", "--all", "--limit", "0");
    const s = snapOf(st, rows, 1);
    const kidRow = rows.find((r) => r.id === kid);
    assert.ok(kid.id.startsWith(`${p1}.`), "native dotted ID implies a deleted parent");
    assert.equal(kidRow.parent ?? null, null, "native cleared the parent FIELD");
    const t = trailFromSnapshot(s, kid);
    assert.deepEqual(t.map((c) => c.id), [kid], "trail follows the FIELD, not the spelling");
    assert.ok(!t.some((c) => c.id === p1), "deleted parent must not render as ancestor");
    const el = Breadcrumb({ snapshot: s, id: kid });
    assert.ok(textIn(el, kid.id), "own dotted ID renders verbatim as a label");
  } finally {
    rmSync(FIX, { recursive: true, force: true });
  }
});

test("native ancestor outside the bounded read renders as contextual missing-parent path", () => {
  // same store, bounded snapshot: kid is in the page, its parent is clipped
  // out by the bound — the trail must show the missing ancestor + reason.
  const st = nativeStore(`bounded-${process.pid}`);
  try {
    const p1 = createNative(st.cwd, "lane p1");
    const kid = createNative(st.cwd, "lane kid", p1);
    const rows = bdRead(st.cwd, "list", "--all", "--limit", "0");
    // bounded page: the kid row is in scope, its parent clipped out of it
    const s = buildSnapshot(baseReads({
      issues: { rows: rows.filter((r) => r.id === kid) },
      storeInfo: { workspace: st.cwd, db: st.info.database_path } }), { fetchedAt: 1 });
    assert.equal(s.nodes.get(kid).pathStatus, "parent-missing");
    const t = trailFromSnapshot(s, kid);
    assert.equal(t[t.length - 1].missing, p1);
    assert.match(t[t.length - 1].reason, /absent . deleted/);
    const el = Breadcrumb({ snapshot: s, id: kid });
    assert.ok(textIn(el, "\u2026"), "contextual path with unknown marker rendered");
  } finally {
    rmSync(FIX, { recursive: true, force: true });
  }
});
