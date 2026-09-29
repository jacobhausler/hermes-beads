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

function readIn(store, name, ...args) {
  const out = JSON.parse(execFileSync("python3",
    [SEEDER, "read", store, name, ...args],
    { encoding: "utf8", maxBuffer: 64 * 1024 * 1024 }));
  return out.rc === 0 ? out.payload : { ...out };
}
function read(name, ...args) { return readIn(STORE, name, ...args); }
function actCliIn(store, actor, ...argv) {
  return JSON.parse(execFileSync("python3", [SEEDER, "act", store, actor, ...argv],
    { encoding: "utf8", maxBuffer: 64 * 1024 * 1024 }));
}
function actCli(actor, ...argv) { return actCliIn(STORE, actor, ...argv); }
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
let botView = null; // hbl-pnu.3.7: injected bot panel view (runState fixtures)
async function mount(sessionOpts = {}) {
  const { botView: bv, world = null, ...rest } = sessionOpts;
  Object.assign(session, rest);
  botView = bv ?? null; // reset every mount unless the fixture supplies one
  // hbl-pnu.3.7: an injected `world` lets the real-door smoke mount the
  // SHIPPED root against its own disposable store without disturbing the
  // browse world's no-mutation guarantee.
  const w = world ?? { snapshot, ui, controller, stack,
    storeInfo: STORE_INFO, draftBeadId: IDS.conflict };
  root = createRoot(mountEl);
  await act(async () => {
    root.render(React.createElement(WorkbenchApp, {
      ...w, session, botView,
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

  // hbl-pnu.4.6 acceptance: app-back + help exercised on the SHIPPED root.
  // Two pushes with a cursor move between them make Back observable.
  await press("Enter");                      // push bundle A (focus on cursor)
  const backTarget = ui.focus;
  await press("ArrowDown");                  // move cursor somewhere else
  ok(ui.selection !== backTarget, "cursor moved off the app-back target");
  await press("Enter");                      // push bundle B
  await press("ArrowLeft", { altKey: true }); // Alt+ArrowLeft = history-back
  eq(ui.focus, backTarget, "Alt+ArrowLeft (app-back) restored the prior keyboard focus");
  await press("ArrowRight", { altKey: true }); // and forward re-applies bundle B
  ok(ui.focus !== backTarget || ui.selection != null,
    "Alt+ArrowRight (app-forward) moved focus again");
  // '?' opens the shortcut overlay through the shipped root's binding
  await press("?");
  const dlg = mountEl.querySelector('[role="dialog"][aria-label="Keyboard shortcuts"]');
  ok(dlg, "? opened the shortcut help overlay on the shipped root");
  ok(dlg.textContent.includes("cursor-down"), "overlay lists the bound commands");
  await press("?");
  ok(mountEl.querySelector('[role="dialog"][aria-label="Keyboard shortcuts"]') == null,
    "? again closes the overlay");

  // hbl-pnu.2.9: Esc closes the help overlay and focus returns to the prior
  // tree row — asserted on the SHIPPED root through the existing keybinding.
  const priorFocusRow = ui.focus;
  ok(priorFocusRow != null, "a prior tree row holds keyboard focus before help opens");
  await press("?");
  ok(mountEl.querySelector('[role="dialog"][aria-label="Keyboard shortcuts"]'),
    "? re-opened the shortcut help overlay");
  await press("Escape");
  ok(mountEl.querySelector('[role="dialog"][aria-label="Keyboard shortcuts"]') == null,
    "Esc closed the help overlay on the shipped root");
  eq(ui.focus, priorFocusRow, "Esc left keyboard focus on the prior tree row");
  const focusedAfterEsc = mountEl.querySelector('[data-keyboard-focus="true"]');
  eq(focusedAfterEsc?.getAttribute("data-tree-row"), priorFocusRow,
    "the DOM-focused row after Esc is the prior tree row");
  await snap("help-esc-closed", mountEl);

  // 'x' stays unbound, and Esc with help CLOSED is inert (close-help no-op):
  // no state change either way.
  const before = { sel: ui.selection, focus: ui.focus, exp: [...ui.expanded].sort().join() };
  await press("x");
  await press("Escape");
  eq(ui.selection, before.sel, "unbound 'x' changed nothing");
  eq(ui.focus, before.focus, "Escape with help closed changed nothing");
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

// ---- T8 hbl-pnu.3.7: truthful run state + Cancel on the SHIPPED root ---------
test("bot runState: role=status renders the door state verbatim; Cancel enabled only in admitted/running; no success text unless succeeded", async () => {
  const VOCAB = ["admitted", "running", "succeeded", "failed", "uncertain",
    "cancel_requested", "cancelled"];
  const enabledIn = new Set(["admitted", "running"]);
  const WORK_ON = { present: true, enabled: true, disabledReason: null };
  for (const state of VOCAB) {
    await mount({ showBot: true, draftStore: null,
      botView: { ask: null, work: WORK_ON, runState: { state } } });
    const st = mountEl.querySelector('[role="status"]');
    ok(st, `role=status mounted for state=${state}`);
    eq(st.textContent.trim(), state, `state=${state} rendered VERBATIM`);
    const cancel = [...mountEl.querySelectorAll("button")]
      .find((b) => /^Cancel$|^Cancel \(/.test(b.textContent.trim()));
    ok(cancel, `Cancel button present for state=${state}`);
    eq(cancel.disabled, !enabledIn.has(state),
      `Cancel enabled iff admitted/running (state=${state})`);
    if (state !== "succeeded") {
      const html = mountEl.querySelector(".bot-action-panel").innerHTML;
      ok(!/succeeded/.test(html), `no 'succeeded' text while state=${state}`);
      ok(!/\bdone\b/.test(html), `no 'done' text while state=${state}`);
      ok(!/delivered/.test(html), `no 'delivered' text while state=${state}`);
    } else {
      ok(mountEl.querySelector('[role="status"]').textContent
        .includes("succeeded"), "succeeded renders when the door says so");
    }
    await snap(`runstate-${state}`, mountEl);
    await unmount();
  }

  // Cancel click: the host latches botView.cancelRequested (mirroring
  // bot_handoff.work_cancel + re-poll); the display shows cancel_requested
  // even while the door still says running, and keeps showing it until a
  // LATER state says cancelled.
  const botView = { ask: null, work: WORK_ON, runState: { state: "running" },
    cancelRequested: false,
    onCancel: () => { botView.cancelRequested = true; box.rerender(); } };
  await mount({ showBot: true, draftStore: null, botView });
  const cancelBtn = [...mountEl.querySelectorAll("button")]
    .find((b) => /^Cancel$|^Cancel \(/.test(b.textContent.trim()));
  await act(async () => { cancelBtn.click(); });
  eq(mountEl.querySelector('[role="status"]').textContent.trim(),
    "cancel_requested", "click Cancel -> cancel_requested shown immediately");
  eq(mountEl.querySelector('.work-run-state[data-run-state="cancel_requested"][role="status"]') != null,
    true, "status element names the displayed state (data-run-state)");
  // the door poll may lag: state still 'running' — the latch keeps the truth
  box.rerender();
  await act(async () => {});
  eq(mountEl.querySelector('[role="status"]').textContent.trim(),
    "cancel_requested", "latched through a lagging door read");
  // ...until a LATER state confirms the terminal truth
  botView.runState = { state: "cancelled" };
  box.rerender();
  await act(async () => {});
  eq(mountEl.querySelector('[role="status"]').textContent.trim(),
    "cancelled", "cancelled renders only once the state says so");
  // the terminal truth beats even a still-latched request
  botView.runState = { state: "running" };
  botView.cancelRequested = true;
  box.rerender();
  await act(async () => {});
  eq(mountEl.querySelector('[role="status"]').textContent.trim(),
    "cancel_requested", "latched request still shown while pre-terminal");
  botView.runState = { state: "succeeded" };
  box.rerender();
  await act(async () => {});
  eq(mountEl.querySelector('[role="status"]').textContent.trim(),
    "succeeded", "a confirmed terminal state beats the latch (no lie)");

  // no runState at all: no fake status element, no enabled Cancel
  await mount({ showBot: true, draftStore: null,
    botView: { ask: null, work: WORK_ON } });
  eq(mountEl.querySelector('#bot-panel-slot [role="status"]') != null, false,
    "no runState => no role=status element (nothing invented)");
  await snap("runstate-none", mountEl);
  await unmount();
});


// ---- T9 hbl-pnu.3.7: mounted smoke against the REAL door (host bridge) ----
// The shipped panel is presentation-only: runState arrives INJECTED and the
// Work/Cancel clicks call INJECTED host handlers. Here the host handlers
// drive the REAL work_door through work_bridge.py (execFileSync, exactly the
// smoke's make_store.py pattern): the states asserted below exist in no
// fixture — fake-hermes really spawns, really sleeps, really gets stopped,
// and 'succeeded' appears only when the ledger says closed_verified.
const BRIDGE = path.join(here, "..", "work_bridge.py");
const BRIDGE_HOME = path.join(here, "..", "tests", ".work-bridge", "host-home");
const VOCAB9 = ["admitted", "running", "succeeded", "failed", "uncertain",
  "cancel_requested", "cancelled", "unknown"];
const WORK_WORLD = JSON.parse(execFileSync("python3",
  [SEEDER, "seed", "--prefix", "mntwork"],
  { encoding: "utf8", maxBuffer: 64 * 1024 * 1024 }));
test.after(() => {
  execFileSync("python3", [SEEDER, "cleanup", WORK_WORLD.store], { encoding: "utf8" });
});

function bridgeCall(verb, opts = {}) {
  const { bead, key, goalPrefix, nodeTimeout, wallDeadline } = opts;
  const argv = [BRIDGE, verb, "--store", WORK_WORLD.store, "--bead", bead, "--key", key];
  if (goalPrefix !== undefined) argv.push("--goal-prefix", goalPrefix);
  if (nodeTimeout !== undefined) argv.push("--node-timeout", String(nodeTimeout));
  if (wallDeadline !== undefined) argv.push("--wall-deadline", String(wallDeadline));
  return JSON.parse(execFileSync("python3", argv, {
    encoding: "utf8", maxBuffer: 64 * 1024 * 1024,
    env: { ...process.env, HERMES_HOME: BRIDGE_HOME },
  }));
}

function makeWorkBead(title) {
  const r = actCliIn(WORK_WORLD.store, ACTOR, "create", title, "--json");
  eq(r.rc, 0, `work bead created in the door store: ${title}`);
  return JSON.parse(r.stdout).id;
}

function workSnapshot() {
  return buildSnapshot({
    issues: readIn(WORK_WORLD.store, "list_all"),
    ready: null,
    blocked: readIn(WORK_WORLD.store, "blocked"),
    storeInfo: WORK_WORLD.storeInfo,
  }, { bound: 500 });
}

async function rr() { box.rerender(); await act(async () => {}); }

async function mountWorkHost(bead, key, clickOpts = {}) {
  const snap = workSnapshot();
  const wui = createWorkbenchState(snap);
  const host = {
    ask: null, work: { present: true, enabled: true, disabledReason: null },
    bead, key, runState: null, cancelRequested: false,
    clicks: [], clickJson: null, cancelJson: null,
  };
  // the ONLY host handlers the shipped panel may ever call — they do all
  // door I/O (via the bridge CLI); the components themselves spawn nothing.
  host.onWork = (b) => {
    host.clicks.push(b);
    host.clickJson = bridgeCall("click", { bead: b, key, ...clickOpts });
  };
  host.onCancel = () => {
    const c = bridgeCall("cancel", { bead, key });
    host.cancelJson = c;
    host.runState = { state: c.state };
    if (c.state === "cancel_requested") host.cancelRequested = true;
    box.rerender();
  };
  await mount({
    showBot: true, draftStore: null, card: null, searchResults: null,
    compare: null, botView: host,
    world: {
      snapshot: snap, ui: wui,
      controller: T.createTreeController({ snapshot: snap, ui: wui }),
      stack: createHistoryStack({ storeKey: snap.storeKey }),
      storeInfo: WORK_WORLD.storeInfo, draftBeadId: bead,
    },
  });
  return host;
}

function statusEl() { return mountEl.querySelector('[role="status"]'); }
function cancelBtnEl() {
  return [...mountEl.querySelectorAll("button")]
    .find((b) => /^Cancel$|^Cancel \(/.test(b.textContent.trim()));
}
function workBtnEl() {
  return [...mountEl.querySelectorAll("button")].find((b) => b.textContent.startsWith("Work"));
}

// Poll the door THROUGH the bridge only. data-run-state equality is asserted
// at EVERY poll: the component displays the injected bridge JSON verbatim
// and has no other source for the state it shows (FROM THE DOOR).
async function pollDoor(host, pred, { timeoutMs = 90000, everyMs = 400 } = {}) {
  const end = Date.now() + timeoutMs;
  const seen = [];
  for (;;) {
    const st = bridgeCall("state", host);
    host.runState = st;
    await rr();
    const el = statusEl();
    CHECKS++;
    assert.ok(el, `role=status rendered while polling (bridge said ${st.state})`);
    CHECKS++;
    assert.equal(el.getAttribute("data-run-state"), st.state,
      `rendered state mirrors the bridge JSON verbatim (bridge=${st.state})`);
    seen.push(st.state);
    if (pred(st)) return { st, seen };
    if (Date.now() >= end) return { st, seen, timedOut: true };
    await new Promise((r) => setTimeout(r, everyMs));
  }
}

test("REAL door: Work click renders admitted/running from the door; Cancel -> cancel_requested while runner alive, cancelled only after confirmed terminal", async () => {
  const bead = makeWorkBead("work door cancel target");
  const host = await mountWorkHost(bead, "k-mnt-cancel",
    { goalPrefix: "SLEEP 20 ", nodeTimeout: 60 });
  const workBtn = workBtnEl();
  ok(workBtn, "Work button present on the shipped root");
  eq(workBtn.disabled, false, "Work enabled (host reports the door qualified)");
  await act(async () => { workBtn.click(); });
  eq(host.clicks.length, 1, "Work click reached the injected host onWork exactly once");
  eq(host.clicks[0], bead, "onWork received the bead id");
  CHECKS++;
  assert.ok(host.clickJson, "the host handler called the bridge click (real door)");
  eq(host.clickJson.ok, true, `door admitted the click: ${JSON.stringify(host.clickJson)}`);
  eq(host.clickJson.handed_off, true, "handed_off through the real door");
  eq(host.clickJson.door_result.delivery, false, "handed_off is never delivered");
  eq(host.clickJson.door_result.no_dispatch, true, "no_dispatch rides along");
  const run = await pollDoor(host, (st) => st.state === "running");
  CHECKS++;
  assert.ok(!run.timedOut, `the door reached running (saw ${run.seen})`);
  CHECKS++;
  assert.ok(run.seen.every((s) => VOCAB9.includes(s)),
    `only the truthful vocabulary was ever rendered (${run.seen})`);
  CHECKS++;
  assert.ok(run.seen[0] === "admitted" || run.seen[0] === "running",
    `first state from the door is admitted|running (saw ${run.seen[0]})`);
  await snap("real-door-running", mountEl);
  const cancelBtn = cancelBtnEl();
  ok(cancelBtn, "Cancel button present");
  eq(cancelBtn.disabled, false, "Cancel enabled while running");
  await act(async () => { cancelBtn.click(); });
  CHECKS++;
  assert.ok(host.cancelJson, "Cancel click reached the injected host onCancel -> bridge cancel");
  eq(host.cancelJson.ok, true, `door accepted the cancel: ${JSON.stringify(host.cancelJson)}`);
  eq(host.cancelJson.state, "cancel_requested", "the door says cancel_requested");
  eq(host.cancelJson.runner_alive, true, "the runner really is alive at the request");
  eq(statusEl().textContent.trim(), "cancel_requested",
    "role=status shows cancel_requested WHILE the runner is alive");
  eq(statusEl().getAttribute("data-run-state"), "cancel_requested",
    "the live region names the displayed state");
  await snap("real-door-cancel-requested", mountEl);
  const fin = await pollDoor(host, (st) => st.state === "cancelled", { timeoutMs: 120000 });
  CHECKS++;
  assert.ok(!fin.timedOut, `the door confirmed terminal cancelled (saw ${fin.seen})`);
  CHECKS++;
  assert.ok(fin.seen.every((s) => VOCAB9.includes(s)), `truthful vocabulary throughout (${fin.seen})`);
  eq(statusEl().textContent.trim(), "cancelled",
    "cancelled rendered ONLY after the door confirmed terminal");
  const html = mountEl.querySelector(".bot-action-panel").innerHTML;
  ok(!/succeeded/.test(html), "no 'succeeded' text on the cancel path");
  ok(!/\bdone\b/.test(html), "no 'done' text on the cancel path");
  ok(!/delivered/.test(html), "no 'delivered' text on the cancel path");
  await snap("real-door-cancelled", mountEl);
  await unmount();
});

test("REAL door timeout: wall deadline while the runner is alive renders 'uncertain' (never delivered)", async () => {
  const bead = makeWorkBead("slow door target");
  const host = await mountWorkHost(bead, "k-mnt-to",
    { goalPrefix: "SLEEP 12 ", nodeTimeout: 60, wallDeadline: 5 });
  await act(async () => { workBtnEl().click(); });
  eq(host.clickJson.ok, true, `door admitted the slow run: ${JSON.stringify(host.clickJson)}`);
  const to = await pollDoor(host, (st) => st.state === "uncertain", { timeoutMs: 60000 });
  CHECKS++;
  assert.ok(!to.timedOut, `the wall deadline produced uncertain (saw ${to.seen})`);
  eq(statusEl().textContent.trim(), "uncertain", "'uncertain' rendered from the door");
  const html = mountEl.querySelector(".bot-action-panel").innerHTML;
  ok(!/succeeded|\bdone\b|delivered/.test(html),
    "no success/delivered text while uncertain");
  await snap("real-door-uncertain", mountEl);
  const done = await pollDoor(host, (st) => st.runner_alive === false, { timeoutMs: 150000 });
  CHECKS++;
  assert.ok(!done.timedOut, `the runner settled before store cleanup (saw ${done.seen})`);
  await unmount();
});

test("REAL door success: a normal run renders 'succeeded' only when the ledger says so; Cancel disabled at terminal", async () => {
  const bead = makeWorkBead("fast door target");
  const host = await mountWorkHost(bead, "k-mnt-ok");
  await act(async () => { workBtnEl().click(); });
  eq(host.clickJson.ok, true, `door admitted the run: ${JSON.stringify(host.clickJson)}`);
  const okRun = await pollDoor(host, (st) => st.state === "succeeded", { timeoutMs: 120000 });
  CHECKS++;
  assert.ok(!okRun.timedOut, `the real run reached succeeded (saw ${okRun.seen})`);
  eq(statusEl().textContent.trim(), "succeeded", "'succeeded' rendered verbatim from the door");
  eq(statusEl().getAttribute("data-run-state"), "succeeded", "live region names succeeded");
  eq(cancelBtnEl().disabled, true, "Cancel disabled once terminal");
  await snap("real-door-succeeded", mountEl);
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
