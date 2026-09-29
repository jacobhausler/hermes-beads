// tests/test_mounted_smoke.mjs — hbl-pnu.4.5: MOUNTED integration smoke of the
// real desktop surface against a local isolated store. jsdom 27+ + React 19 +
// react-dom 19 (READ-ONLY from the rich-ui plugin's pinned node_modules via
// tests/__shims__/jsx-real-loader.mjs) mount the actual desktop components
// (tree, record, blockers, search, compare, drafts, bot_action) with
// createRoot + act, exactly the pattern proven by
// rich-ui/hermes-rich-ui-plugin/tests/test_render_smoke.mjs.
//
// The capture shim is REPLACED by the real react/jsx-runtime through the
// loader hook — no component is rendered through a fake jsx.
//
// Store: one disposable scenario store from tests/fixtures/scenarios/
// make_store.py (seed/read/act/cleanup). Snapshot is built from its
// READONLY reads exactly as desktop/model.mjs expects (list_all + ready +
// blocked + storeInfo), the same same-moment-readback discipline as
// tests/test_scenarios_a.mjs.
//
// LIVE-DOM assertions: roles/aria (treeitem, aria-level, aria-expanded,
// roving tabindex), real KeyboardEvents (arrows/Enter; x/Esc proven inert),
// hostile titles (script tag, onerror attr, RTL override) rendered as TEXT
// with zero <script> elements and zero on* attributes in the DOM, drafts
// surviving unmount/remount through the injected storage adapter and
// reporting memory-only when storage throws, prefers-reduced-motion probed
// through a stubbed matchMedia, and no fixed px widths in inline styles
// (320px reflow proxy: only the declared 16px indent uses px).
//
// No browser exists on this host: there are NO screenshots and this test
// makes NO human-usability claim — it asserts machine-checkable DOM
// structure, focus movement, escaping, and storage behavior only.
//
// Run: node --test tests/test_mounted_smoke.mjs
//      (scripts/run-mounted-smoke.sh sets RICH_UI_NODE_MODULES + reports dir)
import test from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdirSync, writeFileSync, rmSync } from "node:fs";
import { register } from "node:module";
import { fileURLToPath, pathToFileURL } from "node:url";
import path from "node:path";

const here = path.dirname(fileURLToPath(import.meta.url));
const RU = process.env.RICH_UI_NODE_MODULES
  || "/home/hermes/.hermes/work/rich-ui/hermes-rich-ui-plugin/node_modules";
const REPORT_DIR = process.env.MOUNTED_REPORT_DIR
  || "/home/hermes/.hermes/work/beads-lab/reports/finish-mounted";

// ---- real browser-less DOM + React (pattern of rich-ui test_render_smoke) ----
register(pathToFileURL(path.join(here, "__shims__", "jsx-real-loader.mjs")).href);
const { JSDOM } = await import("jsdom");
const dom = new JSDOM("<!doctype html><html><body><div id=\"root\"></div></body></html>", {
  url: "http://localhost/beads-workbench", // neutral origin; no network
  pretendToBeVisual: false,
});
globalThis.window = dom.window;
globalThis.document = dom.window.document;
try { globalThis.navigator = dom.window.navigator; }
catch {
  // Node >=21 ships a native navigator getter — override it definably.
  Object.defineProperty(globalThis, "navigator",
    { value: dom.window.navigator, configurable: true, writable: true });
}
globalThis.Event = dom.window.Event;
globalThis.KeyboardEvent = dom.window.KeyboardEvent;
globalThis.HTMLElement = dom.window.HTMLElement;
globalThis.IS_REACT_ACT_ENVIRONMENT = true;

// matchMedia stub: records every query, answer from a controllable table.
const mmQueries = [];
let reducedMotion = false;
dom.window.matchMedia = (q) => {
  mmQueries.push(q);
  return {
    matches: q === "(prefers-reduced-motion: reduce)" && reducedMotion,
    media: q, addEventListener() {}, removeEventListener() {},
    addListener() {}, removeListener() {},
  };
};

const React = (await import("react")).default;
const { act } = await import("react");
const { createRoot } = await import("react-dom/client");

// ---- components under test (REAL shipped modules; react/jsx-runtime real) ---
// hbl-pnu.4.6: the ONLY app/panel imports are the shipped desktop/workbench.mjs
// WorkbenchApp (which itself composes the real panels). No test-authored app,
// panel wrapper, or record converter exists in this file.
const { buildSnapshot, createWorkbenchState } = await import("../desktop/model.mjs");
const T = await import("../desktop/tree.mjs");
const { RecordCard } = await import("../desktop/record.mjs");
const B = await import("../desktop/blockers.mjs");
const { searchIssues, SearchPanel, enterSearchHit } = await import("../desktop/search.mjs");
const { createHistoryStack } = await import("../desktop/history.mjs");
const { createSplitPanes, diffSnapshots, confirmDeletions, SplitCompare } =
  await import("../desktop/compare.mjs");
const { createDraftStore } = await import("../desktop/drafts.mjs");
const BA = await import("../desktop/bot_action.mjs");
// hbl-pnu.4.6: mount ONLY shipped exports — the product root composes the
// panels and binds the keymap; the harness defines no app, panel, or converter.
const { WorkbenchApp } = await import("../desktop/workbench.mjs");

// ---- assertion + evidence accounting ----------------------------------------
let CHECKS = 0;
const ok = (cond, msg) => { CHECKS++; assert.ok(cond, msg); };
const eq = (a, b, msg) => { CHECKS++; assert.equal(a, b, msg); };
mkdirSync(REPORT_DIR, { recursive: true });
const evidence = {}; // name -> file written under REPORT_DIR
let evidenceSeq = 0;
async function snap(name, rootEl) {
  let html;
  await act(async () => { html = rootEl.innerHTML; });
  const file = `${String(++evidenceSeq).padStart(2, "0")}-${name}.html`;
  writeFileSync(path.join(REPORT_DIR, file), html);
  evidence[name] = { file, bytes: html.length };
  return html;
}

// ---- seeded world (native bd v1.3.0 through the seeder CLI) ------------------
const SEEDER = path.join(here, "fixtures", "scenarios", "make_store.py");
const BD_BIN = process.env.BEADS_LAB_BD || "/home/hermes/.hermes/work/beads-lab/bin/bd";
const ACTOR = "lab-hci";
const world = JSON.parse(execFileSync("python3", [SEEDER, "seed", "--prefix", "mnt"],
  { encoding: "utf8", maxBuffer: 64 * 1024 * 1024 }));
const STORE = world.store;
const IDS = world.ids;
const STORE_INFO = world.storeInfo;

test.after(() => {
  execFileSync("python3", [SEEDER, "cleanup", STORE], { encoding: "utf8" });
});

function read(name, ...args) {
  const out = JSON.parse(execFileSync("python3",
    [SEEDER, "read", STORE, name, ...args],
    { encoding: "utf8", maxBuffer: 64 * 1024 * 1024 }));
  return out.rc === 0 ? out.payload : { ...out };
}
function actCli(actor, ...argv) {
  return JSON.parse(execFileSync("python3", [SEEDER, "act", STORE, actor, ...argv],
    { encoding: "utf8", maxBuffer: 64 * 1024 * 1024 }));
}
// bounded native search/show facades (read_model shape; same as scenarios_a)
function nativeSearch(query, bound) {
  const out = execFileSync(BD_BIN,
    ["-C", STORE, "--readonly", "--actor", ACTOR,
      "search", query, "--limit", String(bound), "--json"],
    { encoding: "utf8", maxBuffer: 64 * 1024 * 1024 });
  return JSON.parse(out);
}
function nativeShow(id) {
  const rows = read("show", id);
  return Array.isArray(rows) ? (rows.find((r) => r && r.id === id) ?? null) : null;
}

// snapshot from the readonly reads exactly as desktop/model.mjs expects
// (issues/ready/blocked/storeInfo), the test_scenarios_a.mjs browse shape.
function liveSnapshot() {
  return buildSnapshot({
    issues: read("list_all"),
    ready: null,
    blocked: read("blocked"),
    storeInfo: STORE_INFO,
  }, { bound: 500 });
}
const provider = {
  storeInfo: STORE_INFO,
  run: (verb, ...rest) => {
    if (verb === "dep" && rest[0] === "tree") return read("deptree", rest[1]);
    if (verb === "show") return read("show", rest[0]);
    if (verb === "ready") return read("ready");
    if (verb === "blocked") return read("blocked");
    if (verb === "list" && rest[0] === "--all") return read("list_all");
    throw new Error(`unexpected provider query: ${verb} ${rest.join(" ")}`);
  },
};

// ---- hostile content (created in the store WE own, then read back native) ----
const HOSTILE_TITLE =
  "<script>alert(\"xss\")</script> <img src=x onerror=alert(1)> \u202Eevil\u202C \u0022quoted\u0022";
const hostileResult = actCli(ACTOR, "create", HOSTILE_TITLE, "--json");
eq(hostileResult.rc, 0, "hostile-title bead created (native store is the source of truth)");
const HOSTILE_ID = JSON.parse(hostileResult.stdout).id;
// prove the hostile bytes round-tripped the NATIVE store, not just the UI
{
  const shown = nativeShow(HOSTILE_ID);
  CHECKS++;
  assert.equal(shown?.title, HOSTILE_TITLE, "native show returns the hostile title verbatim");
}

// ============================================================================
// one mounted session, many states (tracer bullet first: mount + tree aria)
// ============================================================================
let snapshot = liveSnapshot();
const ui = createWorkbenchState(snapshot);
const controller = T.createTreeController({ snapshot, ui });
const stack = createHistoryStack({ storeKey: snapshot.storeKey });

const session = {
  searchResults: null,
  card: null,
  draftStore: null,
  pane: "tree",
  compare: null, // {panes, side, diff, confirmed}
  showBot: false,
};

const box = {}; // live re-render handle (handed out by the shipped root)

// shared storage adapter so persistence can be proven across remounts
function makeStorage() {
  const map = new Map();
  return {
    map,
    get: (k) => (map.has(k) ? map.get(k) : null),
    set: (k, v) => { map.set(k, v); },
    remove: (k) => { map.delete(k); },
    keys: () => [...map.keys()],
  };
}
const sharedStorage = makeStorage();

let mountEl = document.getElementById("root");
let BEFORE_LIST_ALL = null; // captured after the test's own sanctioned native writes
let root = null;
async function mount(sessionOpts = {}) {
  Object.assign(session, sessionOpts);
  root = createRoot(mountEl);
  await act(async () => {
    root.render(React.createElement(WorkbenchApp, {
      snapshot, ui, controller, stack, session,
      storeInfo: STORE_INFO, draftBeadId: IDS.conflict,
      bindRerender: (fn) => { box.rerender = fn; },
    }));
  });
}
async function unmount() {
  await act(async () => { root.unmount(); });
  root = null;
}
async function press(key, opts = {}, targetSel = null) {
  await act(async () => {
    const target = targetSel
      ? mountEl.querySelector(targetSel)
      // focus lives in the mounted tree: dispatch on the roving tab stop (or the
      // focused node when it is inside the React root), never on <body>
      : (mountEl.contains(document.activeElement) && document.activeElement !== mountEl
          ? document.activeElement
          : mountEl.querySelector('[data-tree-focusable="true"]') ?? mountEl);
    const ev = new dom.window.KeyboardEvent("keydown",
      { key, bubbles: true, cancelable: true, ...opts });
    target.dispatchEvent(ev);
  });
}

// ---- T1 mount + tree roles/aria ----------------------------------------------
test("mounted tree: real React DOM with treeitem roles, aria-level/expanded, roving tab stop", async () => {
  await mount();
  const tree = mountEl.querySelector('[role="tree"]');
  ok(tree, "role=tree mounted");
  const items = [...mountEl.querySelectorAll('[role="treeitem"]')];
  ok(items.length >= 6, `multiple treeitems rendered (got ${items.length})`);
  // every row carries aria-level and a parent-FIELD-driven indentation
  for (const it of items) {
    CHECKS++;
    assert.match(it.getAttribute("aria-level"), /^\d+$/, "aria-level numeric");
  }
  const epicRow = mountEl.querySelector(`[data-tree-row="${IDS.epic}"]`);
  ok(epicRow, "epic row present");
  eq(epicRow.getAttribute("aria-expanded"), "true", "epic expanded by default (model truth)");
  const taskRow = mountEl.querySelector(`[data-tree-row="${IDS.taskB}"]`);
  ok(taskRow, "blocked task row present");
  eq(taskRow.getAttribute("data-blocked-word"), "blocked", "blocked word rendered (not glyph-only)");
  // roving tab stop: exactly one tabIndex=0
  const tabbable = items.filter((i) => i.getAttribute("tabindex") === "0");
  eq(tabbable.length, 1, "exactly one roving tab stop");
  eq(tabbable[0], mountEl.querySelector('[data-tree-focusable="true"]'),
    "roving tab stop marks the selection cursor");
  await snap("mounted-initial-tree", mountEl);
  await unmount();
});

// ---- T2 hostile text renders as text ------------------------------------------
test("hostile titles: rendered as escaped TEXT; zero <script>/<img> and zero on* attributes in DOM", async () => {
  snapshot = liveSnapshot(); // include the hostile bead (fresh native read)
  // rebuild ui/controller on the fresh snapshot so nav tests continue coherently
  session.showBot = true;
  session.draftStore = createDraftStore({ storage: sharedStorage });
  sharedStorage.map.clear();
  session.draftStore.saveDraft(STORE_INFO, IDS.conflict, "draft under hostile session");
  await mount();
  const row = mountEl.querySelector(`[data-tree-row="${HOSTILE_ID}"]`);
  ok(row, "hostile row mounted");
  ok(row.textContent.includes("<script>"), "literal <script> text present as TEXT");
  ok(row.textContent.includes("onerror=alert(1)"), "literal onerror text present as TEXT");
  ok(row.textContent.includes("\u202E"), "RTL override present as text");
  eq(mountEl.querySelectorAll("script").length, 0, "zero <script> elements mounted");
  eq(mountEl.querySelectorAll("img").length, 0, "zero <img> elements mounted");
  const onAttrs = [...mountEl.querySelectorAll("*")]
    .flatMap((e) => [...e.attributes])
    .filter((a) => /^on[a-z]+$/i.test(a.name));
  eq(onAttrs.length, 0, `zero on*-handler attributes (got ${onAttrs.map((a) => a.name)})`);
  await snap("hostile-text", mountEl);
  await unmount();
});

// ---- T3 keyboard navigation on the LIVE surface -------------------------------
test("keyboard: arrows move the cursor, Enter promotes focus, collapse/expand via arrows, x/Esc inert", async () => {
  session.showBot = true;
  session.draftStore = createDraftStore({ storage: sharedStorage });
  await mount();
  // cursor starts null: ArrowDown lands on a real row and the DOM follows
  eq(ui.selection, null, "cursor starts unset");
  await press("ArrowDown");
  ok(ui.selection != null, "ArrowDown set the model cursor");
  let selRow = mountEl.querySelector(`[data-tree-row="${ui.selection}"][data-tree-focusable="true"]`);
  ok(selRow, "cursor row is the marked focusable row in the DOM");
  const first = ui.selection;
  await press("ArrowDown");
  ok(ui.selection !== first, "second ArrowDown advanced the cursor");
  eq(mountEl.querySelectorAll('[data-tree-focusable="true"]').length, 1,
    "exactly one row marked focusable after moves");
  await snap("keyboard-cursor", mountEl);

  // Enter: cursor -> keyboard focus, DOM marker + aria-label follow
  await press("Enter");
  eq(ui.focus, ui.selection, "Enter promotes cursor to keyboard focus");
  const focusRow = mountEl.querySelector(`[data-tree-row="${ui.focus}"][data-keyboard-focus="true"]`);
  ok(focusRow, "focused row carries data-keyboard-focus=true in the live DOM");
  ok(focusRow.getAttribute("aria-label").includes("keyboard focus"),
    "aria-label announces keyboard focus");
  await snap("keyboard-enter-focus", mountEl);

  // ArrowRight/ArrowLeft drive the ONE expansion truth (aria-expanded flips)
  const parentRow = mountEl.querySelector(`[data-tree-row="${IDS.branchB}"]`);
  ok(parentRow, "branchB row present");
  eq(parentRow.getAttribute("aria-expanded"), "true", "expanded initially (model default)");
  // move the cursor onto branchB via ArrowDown presses (bounded walk)
  let guard = ui.visibleRows().length * 2 + 2;
  while (ui.selection !== IDS.branchB && guard-- > 0) await press("ArrowDown");
  eq(ui.selection, IDS.branchB, "cursor reached branchB by repeated ArrowDown");
  await press("ArrowLeft"); // collapse
  eq(mountEl.querySelector(`[data-tree-row="${IDS.branchB}"]`).getAttribute("aria-expanded"),
    "false", "ArrowLeft collapsed (aria-expanded=false)");
  ok(mountEl.querySelector(`[data-tree-row="${IDS.taskB}"]`) == null,
    "collapsed subtree rows leave the DOM");
  await snap("keyboard-collapsed", mountEl);
  await press("ArrowRight"); // expand again
  eq(mountEl.querySelector(`[data-tree-row="${IDS.branchB}"]`).getAttribute("aria-expanded"),
    "true", "ArrowRight re-expanded");
  ok(mountEl.querySelector(`[data-tree-row="${IDS.taskB}"]`), "subtree rows return");

  // x and Esc: NOT bound in the v1 keymap — must be INERT (no state change).
  const before = { sel: ui.selection, focus: ui.focus, exp: [...ui.expanded].sort().join() };
  await press("x");
  await press("Escape");
  eq(ui.selection, before.sel, "unbound 'x' changed nothing");
  eq(ui.focus, before.focus, "unbound Escape changed nothing");
  eq([...ui.expanded].sort().join(), before.exp, "unbound keys did not touch expansion");
  // a bare 'x' while a text input has focus must be swallowed by the keymap
  eq(T.resolveKey({ key: "ArrowDown", target: { tagName: "INPUT" } }), null,
    "arrows fall through to text inputs (keymap swallow)");
  await unmount();
});

// ---- T4 search + record + blockers + compare mounted --------------------------
test("search/record/blockers/compare mount with live DOM evidence", async () => {
  session.showBot = true;
  session.draftStore = createDraftStore({ storage: sharedStorage });
  // search: real native rows, panel mounted, hostile query title escaped too
  session.searchResults = searchIssues({
    snapshot, query: "blocked", searchRead: nativeSearch, showRead: nativeShow, limit: 25,
  });
  ok(session.searchResults.hits.length > 0, "native search returned hits");
  await mount();
  const panel = mountEl.querySelector('#search-panel[role="listbox"]');
  ok(panel, "SearchPanel mounted as listbox");
  const opts = [...mountEl.querySelectorAll('[role="option"]')];
  eq(opts.length, session.searchResults.hits.length, "one option per hit");
  ok([...mountEl.querySelectorAll(".search-hit-path")].some((n) => (n.getAttribute("aria-label") ?? "").startsWith("path: ")), "ancestor path labels present (aria-label on .search-hit-path)");
  await snap("search-panel", mountEl);

  // Enter on a hit = a navigation entry into the tree (not a parallel world).
  // hbl-pnu.4.6 RED: equality with the HIT id (the old `!= null` check was
  // vacuous — ui.focus was already non-null from T3 and the dispatched Enter
  // resolved to the tree focus-cursor command, not the hit).
  const hit0id = session.searchResults.hits[0].id;
  ok(opts.every((o) => o.hasAttribute("tabindex")),
    "every search option carries a tabIndex (keyboard-activatable)");
  ui.jump(hit0id === IDS.epic ? IDS.taskB : IDS.epic); // park focus away from the hit
  await press("Enter", {}, '#search-hit-0');
  eq(ui.focus, hit0id, "Enter on a search hit moves ui focus to THAT hit id");
  eq(mountEl.querySelector('#search-panel') != null, false,
    "activation closes the panel (navigation entry, not a parallel world)");

  // Click does the same (the same navigation entry): park focus away from
  // the hit, click it, and require equality with THAT hit id.
  const results2 = searchIssues({ snapshot, query: "blocked", searchRead: nativeSearch,
    showRead: nativeShow, limit: 25 });
  ok(results2.hits.length >= 1, "search returns at least one hit for the click leg");
  const targetId = results2.hits[0].id;
  ui.jump(targetId === IDS.epic ? IDS.taskB : IDS.epic);
  session.searchResults = results2;
  box.rerender();
  await act(async () => {});
  await act(async () => { mountEl.querySelector("#search-hit-0").click(); });
  eq(ui.focus, targetId, "click on a search hit moves ui focus to THAT hit id");
  session.searchResults = null;

  // Space is the third equal activation path.
  ui.jump(targetId === IDS.epic ? IDS.taskB : IDS.epic);
  session.searchResults = results2;
  box.rerender();
  await act(async () => {});
  await press(" ", {}, "#search-hit-0");
  eq(ui.focus, targetId, "Space on a search hit moves ui focus to THAT hit id");
  session.searchResults = null;

  // blockers: jumpToBlocker through the readonly provider, card mounted
  const jump = B.jumpToBlocker({ snapshot, ui, stack, state: {}, provider,
    targetId: IDS.taskB, pane: "tree" });
  session.card = jump.card;
  box.rerender();
  await act(async () => {});
  const cardEl = document.getElementById(`blocker-card:${IDS.taskB}`);
  ok(cardEl, "BlockerCard mounted for the blocked task");
  eq(cardEl.getAttribute("role"), "complementary", "card is a complementary region");
  ok(cardEl.textContent.includes(IDS.gateA) || cardEl.textContent.includes("blocked"),
    "card names the blocker evidence");
  eq(B.cardMutationSurface(jump.card), false, "card carries no mutation surface");
  await snap("blocker-card", mountEl);
  session.card = null;

  // record: stored vs derived rendered separately
  box.rerender();
  await act(async () => {});
  const recSection = [...mountEl.querySelectorAll("section")]
    .find((e) => (e.getAttribute("aria-label") || "").startsWith("Record "));
  ok(recSection, "RecordCard mounted (aria-label Record <id>)");
  ok(recSection.textContent.includes("stored status"), "stored status row");
  ok(recSection.textContent.includes("derived readiness"), "derived readiness row (separate value)");
  await snap("record-card", mountEl);

  // compare: native churn — reparent moveMe under the OTHER host, diff, mount
  const before = liveSnapshot();
  const rep = actCli("lab-churn", "update", IDS.moveMe, "--parent", IDS.moveHostB, "--json");
  eq(rep.rc, 0, "native reparent succeeded");
  const after = liveSnapshot();
  const diff = diffSnapshots(before, after);
  ok(diff.moved.some((m) => m.id === IDS.moveMe), "diff reports the reparent as moved");
  const panes = createSplitPanes({
    left: { snapshot: after, focusable: { focus: IDS.moveMe }, breadcrumb: null },
    right: { snapshot: after },
  });
  const confirmed = confirmDeletions(diffSnapshots(after, after), {
    reads: { show: () => [], history: () => [] },
  });
  session.compare = { panes, side: "left", diff, confirmed };
  box.rerender();
  await act(async () => {});
  const sc = mountEl.querySelector('[aria-label="Split compare"]');
  ok(sc, "SplitCompare mounted");
  ok(sc.textContent.includes("moved"), "moved line rendered");
  await snap("split-compare", mountEl);
  session.compare = null;
  BEFORE_LIST_ALL = JSON.stringify(read("list_all"));
  await unmount();
});

// ---- T5 drafts: persistence through unmount/remount + honest unavailability ---
test("drafts: survive unmount/remount via the injected storage; report memory-only when storage throws", async () => {
  // durable leg: shared adapter already holds the draft saved in T2's session
  session.showBot = true;
  session.draftStore = createDraftStore({ storage: sharedStorage });
  eq(session.draftStore.durability(), "durable", "adapter with get/set/remove/keys is durable");
  await mount();
  eq(mountEl.querySelector('[data-durability="durable"]') != null, true,
    "mounted surface declares durable");
  const dt = mountEl.querySelector("#draft-text");
  eq(dt.textContent, "draft under hostile session",
    "draft SURVIVED unmount/remount (read back through storage after root re-created)");
  const saveBtn = mountEl.querySelector("#draft-save");
  eq(saveBtn.disabled, true, "Save disabled (no proven atomic content guard)");

  // hbl-pnu.4.6 acceptance: typing in the description field STEALS letter
  // keys — bare keys while the textarea owns focus must not move the tree
  // cursor or open help (the shipped root defers via resolveKey's swallow).
  const input = mountEl.querySelector("#draft-input");
  ok(input && input.tagName === "TEXTAREA", "description field is a real textarea");
  const beforeTyping = { sel: ui.selection, focus: ui.focus,
    exp: [...ui.expanded].sort().join() };
  await press("ArrowDown", {}, "#draft-input");
  await press("x", {}, "#draft-input");
  await press("?", {}, "#draft-input");
  await press("Enter", {}, "#draft-input");
  eq(ui.selection, beforeTyping.sel, "letters/arrows/Enter in the description field move nothing in the tree");
  eq(ui.focus, beforeTyping.focus, "description typing left keyboard focus untouched");
  eq([...ui.expanded].sort().join(), beforeTyping.exp, "description typing left expansion untouched");
  eq(controller.helpOpen, false, "bare ? in the description field did not open help");
  ok(mountEl.querySelector("#save-limitation").textContent.includes("NO ATOMIC CONTENT GUARD"),
    "product-limitation notice rendered, no collaborative-edit claim");
  await snap("drafts-durable", mountEl);
  await unmount();

  // third mount from a DIFFERENT store object but the SAME storage: still there
  session.draftStore = createDraftStore({ storage: makeStorageKept(sharedStorage.map) });
  await mount();
  eq(mountEl.querySelector("#draft-text").textContent, "draft under hostile session",
    "draft re-read by a fresh store instance from the same storage");
  await unmount();

  // unavailable leg: adapter that throws during setup probe
  const boom = { get() { throw new Error("quota"); }, set() { throw new Error("quota"); },
    remove() { throw new Error("quota"); }, keys: () => [] };
  session.draftStore = createDraftStore({ storage: boom });
  eq(session.draftStore.durability(), "memory-only", "throwing adapter => memory-only");
  await mount();
  eq(mountEl.querySelector('[data-durability="memory-only"]') != null, true,
    "mounted surface honestly declares memory-only");
  const alert = mountEl.querySelector('[role="alert"][data-warning-kind="storage-error"]');
  ok(alert, "storage-error warning rendered as an alert");
  ok(alert.textContent.includes("session-only"), "warning states drafts are session-only");
  await snap("drafts-unavailable", mountEl);
  // no-adapter leg: honest memory-only without any throw
  await unmount();
  session.draftStore = createDraftStore({});
  await mount();
  eq(session.draftStore.warning().kind, "memory-only", "no adapter => memory-only warning kind");
  await snap("drafts-no-adapter", mountEl);
  await unmount();
});
function makeStorageKept(map) {
  return { get: (k) => (map.has(k) ? map.get(k) : null),
    set: (k, v) => { map.set(k, v); }, remove: (k) => { map.delete(k); },
    keys: () => [...map.keys()] };
}

// ---- T6 reduced motion + reflow ------------------------------------------------
test("prefers-reduced-motion probed via matchMedia stub; no fixed px widths in inline styles", async () => {
  session.showBot = true;
  session.draftStore = createDraftStore({ storage: sharedStorage });
  reducedMotion = false;
  await mount();
  ok(mmQueries.includes("(prefers-reduced-motion: reduce)"),
    "the mounted surface queried the reduced-motion media query");
  eq(mountEl.querySelector("#workbench").getAttribute("data-motion"), "no-preference",
    "motion preference wired to the mounted root");
  reducedMotion = true;
  box.rerender();
  await act(async () => {});
  eq(mountEl.querySelector("#workbench").getAttribute("data-motion"), "reduce",
    "prefers-reduced-motion honoured live (root flips to data-motion=reduce)");
  await snap("reduced-motion", mountEl);

  // 320px reflow proxy: no fixed pixel WIDTHS anywhere in inline styles;
  // the only px usage allowed is the declared INDENT_PX indent (padding).
  const offenders = [];
  for (const e of mountEl.querySelectorAll("*")) {
    const s = e.getAttribute("style");
    if (!s) continue;
    for (const m of s.split(";")) {
      const [prop, val] = m.split(":").map((x) => (x ?? "").trim());
      if (!prop || !val) continue;
      if (/px\s*$/.test(val) && /width|height|flex-basis|inset|left|right/i.test(prop)) {
        offenders.push(`${e.tagName}[${e.id || e.getAttribute("role")}] ${prop}:${val}`);
      }
    }
  }
  eq(offenders.length, 0, `no fixed px sizing (offenders: ${offenders.join(", ")})`);
  // widths are content-driven; indent px is the ONLY px value (source-declared)
  const pxVals = new Set();
  for (const e of mountEl.querySelectorAll("*")) {
    for (const m of (e.getAttribute("style") ?? "").matchAll(/([a-z-]+)\s*:\s*([^;]+)/gi)) {
      if (/\d+px/.test(m[2])) pxVals.add(`${m[1]}=px`);
    }
  }
  eq([...pxVals].every((v) => /^padding-inline-start=px$/.test(v)), true,
    `only padding-inline-start uses px (indent); found: ${[...pxVals]}`);
  // components do not read matchMedia themselves (source truth): nothing
  // animates, so there is nothing to suppress — recorded honestly.
  await snap("reflow-320-proxy", mountEl);
  await unmount();
  reducedMotion = false;
});

// ---- T7 bot_action: precise incompatibility + faithful mount ------------------
test("bot_action: botActionPanel emits REAL jsx and mounts UNCONVERTED with Work disabled", async () => {
  // Direct mount attempt with the raw panel (no converter — hbl-pnu.4.6).
  const raw = BA.botActionPanel({
    ask: BA.askDecision({ ok: false, error: "session_door_unqualified", read_only: true }),
    work: { present: true, enabled: false, disabledReason: "runner door not bound" },
  });
  CHECKS++;
  assert.ok(raw && raw.$$typeof && /^Symbol\(react\./.test(String(raw.$$typeof)),
    "hbl-pnu.4.6: botActionPanel emits REAL jsx (react/jsx-runtime $$typeof), so React can mount it directly");
  const scratch = document.createElement("div");
  document.body.appendChild(scratch);
  const r2 = createRoot(scratch);
  let directErr = null;
  {
    const origErr = console.error; console.error = () => {};
    try { await act(async () => { r2.render(raw); }); }
    catch (e) { directErr = e; }
    finally { console.error = origErr; }
  }
  const directHtml = scratch.innerHTML;
  await act(async () => { r2.unmount(); });
  scratch.remove();
  CHECKS++;
  assert.equal(directErr, null, `direct unconverted mount must not throw (${directErr?.message?.slice(0, 80)})`);
  CHECKS++;
  assert.ok(/bot-action-panel/.test(directHtml) && /disabled/.test(directHtml),
    "unconverted mount renders the panel with the Work button disabled");

  session.showBot = true;
  session.draftStore = createDraftStore({ storage: sharedStorage });
  await mount();
  const panel = mountEl.querySelector(".bot-action-panel");
  ok(panel, "unconverted botActionPanel mounted by the shipped WorkbenchApp");
  const buttons = [...panel.querySelectorAll("button")];
  const work = buttons.find((b) => b.textContent.startsWith("Work"));
  ok(work, "Work button present");
  eq(work.disabled, true, "Work button disabled (runner door unbound)");
  ok(panel.textContent.includes("runner door"), "typed disabled reason rendered as title text");
  const refine = buttons.find((b) => b.textContent.startsWith("Refine"));
  eq(refine.disabled, false, "Refine stays enabled (draft path works)");
  await snap("bot-panel", mountEl);
  await unmount();
});

// final native readback proof the surface never wrote the store
test("mounted session never mutated the store (before/after list_all JSON equality)", () => {
  CHECKS++;
  assert.ok(BEFORE_LIST_ALL != null, "before-snapshot captured after the sanctioned writes only");
  const after = JSON.stringify(read("list_all"));
  CHECKS++;
  assert.equal(after, BEFORE_LIST_ALL,
    "list_all is byte-identical before vs after the mounted session (no hidden writes)");
  const rows = JSON.parse(after);
  CHECKS++;
  assert.ok(rows.some((r) => r.id === IDS.epic) && rows.some((r) => r.id === HOSTILE_ID),
    "store still holds the seeded world + the one bead the harness explicitly created via the seeder act CLI");
});

test.childChecks = () => CHECKS;
process.on("exit", () => {
  // surface machine-checkable totals + the evidence index for the receipt
  writeFileSync(path.join(REPORT_DIR, "evidence-index.json"),
    JSON.stringify({ checks: CHECKS, evidence }, null, 2));
  console.log(`[mounted-smoke] checks=${CHECKS} evidence=${Object.keys(evidence).length}`);
});
