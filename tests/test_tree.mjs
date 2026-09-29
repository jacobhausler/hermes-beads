// tests/test_tree.mjs — hbl-pnu.2.2: conventional ARIA tree + navigation
// controller for the beads workbench.
// Run: node --test tests/test_tree.mjs   (Node built-in runner, no deps)
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
const { buildSnapshot, createWorkbenchState } = await import("../desktop/model.mjs");
const tree = await import("../desktop/tree.mjs");
const {
  buildTreeRows, statusLabel, epicProgress, ShortcutHelp, KEYMAP,
  resolveKey, createTreeController, tabCommands, refreshOnce,
  createRefreshScheduler, Tree, INDENT_PX, expandedOf,
} = tree;

const fixture = (name) =>
  JSON.parse(readFileSync(path.join(here, "fixtures", "tree", name), "utf8"));

const baseReads = (over = {}) => ({
  issues: [], ready: [], blocked: [],
  storeInfo: { workspace: "/lab/store", db: "/lab/store/.beads/lab.db" },
  ...over,
});

const walk = (t) => [...shim.walk(t)];
const texts = (tree_) => walk(tree_).filter((n) => typeof n === "string");
const textIn = (tree_, s) => texts(tree_).some((n) => n.includes(s));
const nodesOf = (tree_) => walk(tree_).filter((n) => typeof n === "object" && n.type);
const findById = (tree_, id) =>
  nodesOf(tree_).find((n) => n.props?.id === id || n.props?.["data-tree-row"] === id);

const snapOf = (f, opts = {}) => buildSnapshot(baseReads(f), { fetchedAt: 1, ...opts });

// ============================================================================
// A. Tree rows: hierarchy only, indent from parent-field depth, glyph + WORD
// ============================================================================

test("tree rows render the hierarchy only: indent from parent-field depth, never ID-dot spelling", () => {
  const f = fixture("hierarchy.json");
  const snap = snapOf(f);
  const w = createWorkbenchState(snap);
  const box = buildTreeRows(snap, w);
  const rows = box.rows.filter((r) => r.kind !== "load-more");
  const byId = Object.fromEntries(rows.map((r) => [r.id, r]));
  // dotted ID x.y implies nothing: its parent FIELD is null -> depth 0
  assert.equal(byId["x.y"].depth, 0, "ID-dot spelling must not create indent");
  assert.equal(byId["x.y"].indentPx, 0);
  // real chain epic -> a -> deep1 gets one indent unit per level
  assert.equal(byId["deep1"].depth, 3);
  assert.equal(byId["deep1"].indentPx, byId["deep1"].depth * INDENT_PX);
  assert.equal(byId["a"].indentPx, byId["a"].depth * INDENT_PX);
  assert.ok(byId["deep1"].indentPx > byId["a"].indentPx && byId["a"].indentPx > byId["epic"].indentPx);
  // byte-match snapshot parents
  for (const r of rows) {
    assert.equal(JSON.stringify(r.parentId), JSON.stringify(snap.nodes.get(r.id).parent),
      `row ${r.id} parent must byte-match the snapshot node`);
  }
});

test("status renders glyph AND word; never glyph-only; unknown statuses verbatim with a distinct glyph", () => {
  const f = fixture("hierarchy.json");
  const snap = snapOf(f);
  const box = buildTreeRows(snap, createWorkbenchState(snap));
  for (const r of box.rows.filter((x) => x.kind !== "load-more")) {
    assert.ok(r.statusGlyph && r.statusWord, `row ${r.id} needs BOTH glyph and word`);
    assert.notEqual(r.statusGlyph, r.statusWord, "glyph and word are distinct tokens");
    assert.ok(r.statusWord.length > 1 && /[A-Za-z]/.test(r.statusWord),
      `row ${r.id} word must be a readable word, not the glyph alone`);
  }
  // every known status maps to glyph+word; unknown keeps the raw word verbatim
  const lbl = statusLabel("in_progress");
  assert.ok(/in_progress/.test(lbl) && lbl.length > "in_progress".length);
  const unk = statusLabel("awaiting-review");
  assert.ok(unk.includes("awaiting-review"), "unknown status word is verbatim");
  assert.notEqual(unk[0], "i", "unknown gets its own glyph, not a copy");
  const nul = statusLabel(null);
  assert.ok(/unknown/.test(nul), "missing status renders the word unknown, never blank");
});

test("text labels, not color alone: rows carry textual status + label; focus and selection separately labelled", () => {
  const f = fixture("hierarchy.json");
  const snap = snapOf(f);
  const w = createWorkbenchState(snap, { selection: "a" });
  w.enter(); // focus follows selection only via Enter
  const el = Tree({ snapshot: snap, ui: w });
  const rowA = findById(el, "row:a");
  assert.ok(rowA, "row element for a exists");
  assert.equal(rowA.props["aria-selected"], true, "selection cursor renders aria-selected");
  assert.equal(rowA.props["data-keyboard-focus"], "true", "keyboard focus separately labelled");
  assert.match(String(rowA.props["aria-label"]), /cursor/, "aria-label names the selection cursor");
  assert.match(String(rowA.props["aria-label"]), /keyboard focus/, "aria-label names keyboard focus");
  // a selected-but-not-focused row keeps them distinct
  const w2 = createWorkbenchState(snap, { selection: "a", focus: "deep1" });
  const el2 = Tree({ snapshot: snap, ui: w2 });
  assert.equal(findById(el2, "row:a").props["aria-selected"], true);
  assert.equal(findById(el2, "row:a").props["data-keyboard-focus"], "false");
  assert.equal(findById(el2, "row:deep1").props["aria-selected"], false);
  assert.equal(findById(el2, "row:deep1").props["data-keyboard-focus"], "true");
});

test("ARIA tree contract: role tree/treeitem, aria-expanded/level/posinset/setsize consistent with the loaded set; roving tab stop; visible focus", () => {
  const f = fixture("hierarchy.json");
  const snap = snapOf(f);
  const w = createWorkbenchState(snap, { selection: "a" });
  const el = Tree({ snapshot: snap, ui: w });
  assert.equal(el.props.role, "tree");
  assert.ok(el.props["aria-label"], "tree has an accessible name");
  const rows = nodesOf(el).filter((n) => n.props.role === "treeitem");
  assert.ok(rows.length >= 5, "treeitems render for the loaded rows");
  for (const r of rows) {
    const id = r.props["data-tree-row"];
    const node = snap.nodes.get(id);
    assert.equal(r.props["aria-level"], node.depth + 1, `level for ${id}`);
    assert.equal(typeof r.props["aria-posinset"], "number");
    assert.equal(typeof r.props["aria-setsize"], "number");
    assert.ok(r.props["aria-posinset"] >= 1 && r.props["aria-posinset"] <= r.props["aria-setsize"],
      `posinset within setsize for ${id}`);
    if (node.childIds.length) {
      assert.equal(r.props["aria-expanded"], expandedOf(snap, w).has(id),
        `expanded reflects state for ${id}`);
    } else {
      assert.equal(r.props["aria-expanded"], undefined, "leaf has no aria-expanded");
    }
    // roving tab stop: exactly one tabbable row (the selection), the rest -1
    assert.equal(r.props.tabIndex, r.props["aria-selected"] === true ? 0 : -1);
    // visible focus: keyboard focus is rendered via a data hook AND a class
    assert.ok("data-keyboard-focus" in r.props, "focus hook present on every row");
  }
  const tabbables = rows.filter((r) => r.props.tabIndex === 0);
  assert.equal(tabbables.length, 1, "roving tab stop: exactly one tabbable row");
});

test("epic rows show progress counts matching native epic-status semantics (closed/total children)", () => {
  const f = fixture("epic-progress.json");
  const snap = snapOf(f);
  const prog = epicProgress(snap, "epic");
  assert.deepEqual({ total: prog.total, closed: prog.closed }, { total: 4, closed: 1 });
  assert.ok(textIn(TreeOf(f, "epic"), `${prog.closed}/${prog.total}`), "count renders as text on the row");
  assert.equal(epicProgress(snap, "plain-task"), null, "non-epic rows carry no counts");
  function TreeOf(fx, id) {
    const s = snapOf(fx);
    return Tree({ snapshot: s, ui: createWorkbenchState(s, { selection: id }) });
  }
});

// ============================================================================
// B. Keymap: every binding resolvable programmatically; text inputs swallow
// ============================================================================

test("keymap is data: every binding resolves to its command without a human", () => {
  for (const b of KEYMAP) {
    const ev = synth(b);
    assert.equal(resolveKey(ev), b.command, `binding ${b.keys.join("+")} resolves to ${b.command}`);
  }
  // the required conventional set is bound
  const bound = new Set(KEYMAP.map((b) => b.keys.join("+")));
  for (const k of ["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "Enter", "Home", "End", "Tab", "Alt+ArrowLeft", "Alt+ArrowRight", "?"])
    assert.ok(bound.has(k), `${k} is bound`);
  // NO bare letter keys are bound (owner ruling: h/l scheme dropped)
  for (const b of KEYMAP)
    for (const k of b.keys)
      assert.ok(k.length > 1 || /[+]/.test(b.keys.join("+")) || k === "?" || k === " ",
        `bare letter binding ${k} must not exist`);
});

function synth(b, target = buttonTarget()) {
  const parts = b.keys.join("+").split("+");
  const key = parts[parts.length - 1];
  return {
    key,
    ctrlKey: parts.includes("Ctrl"), metaKey: parts.includes("Meta"),
    altKey: parts.includes("Alt"), shiftKey: b.keys.join("+").includes("Shift"),
    isComposing: false, target,
    preventDefault: () => { evPrevented = true; },
  };
}
let evPrevented = false;
const buttonTarget = () => ({ tagName: "DIV" });
const editableTarget = (tag = "INPUT") =>
  ({ tagName: tag, type: "text", isContentEditable: false });

test("text-input focus swallows ALL letter keys (and bare keys generally); no shortcut interception in editors", () => {
  const ed = editableTarget("INPUT");
  const letters = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ".split("");
  for (const k of [...letters, "0", "9", " ", "?", "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "Enter", "Home", "End", "Tab"])
    assert.equal(resolveKey({ key: k, isComposing: false, target: ed }), null,
      `bare ${JSON.stringify(k)} must be swallowed while a text input has focus`);
  // contentEditable (editor) swallows the same
  const ce = { tagName: "DIV", isContentEditable: true };
  assert.equal(resolveKey({ key: "j", isComposing: false, target: ce }), null);
  assert.equal(resolveKey({ key: "?", isComposing: false, target: ce }), null);
  // textarea too
  assert.equal(resolveKey({ key: "ArrowDown", isComposing: false, target: editableTarget("TEXTAREA") }), null);
  // IME composition is never intercepted even outside editors
  assert.equal(resolveKey({ key: "ArrowDown", isComposing: true, target: buttonTarget() }), null);
  // explicit modifier combos STILL resolve with an editor focused (not bare keys)
  assert.equal(resolveKey({ key: "ArrowLeft", altKey: true, isComposing: false, target: ed }), "history-back");
  // unknown keys resolve to null even outside editors
  assert.equal(resolveKey({ key: "F13", isComposing: false, target: buttonTarget() }), null);
});

test("controller executes exactly the bound commands; Tab exits without preventDefault; '?' toggles the overlay", () => {
  const f = fixture("hierarchy.json");
  const snap = snapOf(f);
  const w = createWorkbenchState(snap);
  const c = createTreeController({ snapshot: snap, ui: w });

  evPrevented = false;
  let cmd = c.press({ key: "ArrowDown", isComposing: false, target: buttonTarget(), preventDefault: () => { evPrevented = true; } });
  assert.equal(cmd, "cursor-down");
  assert.equal(w.selection, "root", "Down moves the selection cursor");
  assert.equal(w.focus, null, "cursor movement must not move keyboard focus");

  c.press({ key: "Enter", isComposing: false, target: buttonTarget() });
  assert.equal(w.focus, "root", "Enter promotes the cursor to keyboard focus");

  evPrevented = false;
  assert.equal(c.press({ key: "Tab", isComposing: false, target: buttonTarget(), preventDefault: () => { evPrevented = true; } }), "exit-tree");
  assert.equal(evPrevented, false, "Tab must not be intercepted — it exits the tree natively");
  assert.equal(c.tabExit, true);

  assert.equal(c.press({ key: "?", isComposing: false, target: buttonTarget() }), "toggle-help");
  assert.equal(c.helpOpen, true);
  c.press({ key: "?", isComposing: false, target: buttonTarget() });
  assert.equal(c.helpOpen, false);

  // hbl-pnu.2.9: Esc closes the overlay and returns keyboard focus to the
  // prior tree row (the row that held focus when help opened).
  const f2snap = snapOf(f);
  const w2 = createWorkbenchState(f2snap);
  const c2 = createTreeController({ snapshot: f2snap, ui: w2 });
  const P2 = (key) => c2.press({ key, isComposing: false, target: buttonTarget(), preventDefault: () => {} });
  P2("ArrowDown"); P2("Enter"); // cursor + keyboard focus on the first row
  const prior = w2.focus;
  assert.ok(prior != null, "prior row focused");
  assert.equal(P2("?"), "toggle-help");
  assert.equal(c2.helpOpen, true, "help open");
  w2.jump("deep1", "detail"); // something steals keyboard focus while help is open
  assert.equal(w2.focus, "deep1", "focus moved while help open");
  assert.equal(P2("Escape"), "close-help", "Escape resolves to close-help");
  assert.equal(c2.helpOpen, false, "Esc closed the overlay");
  assert.equal(w2.focus, prior, "Esc returned keyboard focus to the prior tree row");
  // Esc with help CLOSED is a no-op (no state change, no history push)
  const before2 = { sel: w2.selection, foc: w2.focus, hist: w2.history.length };
  assert.equal(P2("Escape"), "close-help");
  assert.deepEqual({ sel: w2.selection, foc: w2.focus, hist: w2.history.length }, before2,
    "Esc with help closed changes nothing");
  // Esc never works from a text target (editor swallow law)
  assert.equal(c2.press({ key: "Escape", isComposing: false, target: editableTarget("INPUT") }), null,
    "Escape is swallowed while a text input has focus");

  // while typing in the search box, every letter is swallowed: no command, no state change
  const before = { sel: w.selection, foc: w.focus, help: c.helpOpen };
  for (const k of "hjkl?") {
    assert.equal(c.press({ key: k, isComposing: false, target: editableTarget("INPUT"), preventDefault: () => {} }), null);
  }
  assert.deepEqual({ sel: w.selection, foc: w.focus, help: c.helpOpen }, before);
});

test("conventional tree arrows: Right expands then visits first child, Left collapses in place then visits parent, Home/End jump", () => {
  const f = fixture("hierarchy.json");
  const snap = snapOf(f);
  // NOTE: under the single expansion truth the model's initial.expanded IS
  // the view (the old tree WeakMap silently overrode it with all-expanded).
  // The Right-on-expanded-node case needs 'a' in the model's set to state
  // the same precondition the test's own message asserts.
  const w = createWorkbenchState(snap, { selection: "epic", expanded: new Set(["root", "epic", "a"]) });
  const c = createTreeController({ snapshot: snap, ui: w });
  const P = (key, extra = {}) => c.press({ key, isComposing: false, target: buttonTarget(), preventDefault: () => {}, ...extra });
  const vis = () => c.visibleIds(); // the tree's presentation view of the rows

  assert.equal(P("ArrowRight"), "expand-or-first-child");
  assert.equal(w.selection, "a", "Right on an expanded node visits its first child");

  assert.equal(P("ArrowLeft"), "collapse-or-parent");
  assert.equal(w.selection, "a", "Left on an expanded node collapses it in place");
  assert.ok(!vis().includes("deep1"), "collapse hid the grandchildren");

  assert.equal(P("ArrowLeft"), "collapse-or-parent");
  assert.equal(w.selection, "epic", "Left on a collapsed node moves the cursor to its parent");

  P("ArrowLeft"); // collapse epic in place
  assert.ok(!vis().includes("a"), "epic collapsed hides its subtree");
  P("ArrowRight"); // expand again, cursor stays
  assert.equal(w.selection, "epic");
  assert.ok(vis().includes("a"), "Right expanded the node");

  P("Home");
  assert.equal(w.selection, vis()[0], "Home jumps to the first visible row");
  P("End");
  assert.equal(w.selection, vis().at(-1), "End jumps to the last visible row");
  P("End");
  const leaf = vis().at(-1);
  assert.equal(w.selection, leaf);
  assert.equal(P("ArrowRight"), "expand-or-first-child", "Right on a leaf is bound but a no-op");
  assert.equal(w.selection, leaf, "leaf Right moved nothing");

  assert.equal(w.focus, null, "arrows never move keyboard focus — only Enter does");
});

test("app back/forward restore prior selection+focus context (Alt+Arrow bindings)", () => {
  const f = fixture("hierarchy.json");
  const snap = snapOf(f);
  const w = createWorkbenchState(snap);
  const c = createTreeController({ snapshot: snap, ui: w });
  const P = (key, extra = {}) => c.press({ key, isComposing: false, target: buttonTarget(), preventDefault: () => {}, ...extra });

  P("ArrowDown"); P("Enter");            // select+focus root
  w.jump("deep1", "detail");             // app-style navigation deep in
  assert.equal(w.focus, "deep1");
  assert.equal(P("ArrowLeft", { altKey: true }), "history-back");
  assert.equal(w.focus, "root", "back returns the prior context");
  assert.equal(P("ArrowRight", { altKey: true }), "history-forward");
  assert.equal(w.focus, "deep1", "forward re-enters the newer context");
});

test("'?' overlay lists exactly the bound keys — no more, no fewer", () => {
  const el = ShortcutHelp();
  const t = texts(el).join("\n");
  for (const b of KEYMAP) {
    for (const k of b.keys) assert.ok(t.includes(k), `overlay lists ${k}`);
    assert.ok(t.includes(b.command) || t.includes(b.desc), `overlay explains ${b.command}`);
  }
  const rowNodes = nodesOf(el).filter((n) => n.props?.["data-shortcut"]);
  assert.equal(rowNodes.length, KEYMAP.length, "overlay renders one row per binding");
  for (const r of rowNodes) {
    assert.ok(KEYMAP.some((b) => b.keys.join("+") === r.props["data-shortcut"]),
      `overlay row ${r.props["data-shortcut"]} is a real binding`);
  }
});

// ============================================================================
// C. Tabs are entry filters: Ready = ready --exclude-type=epic,
//    Mine = list --assignee, Browse exposes blocked + unassigned branches
// ============================================================================

test("tab descriptors reproduce their bd commands verbatim (argv + display string)", () => {
  const tabs = tabCommands({ assignee: "lane-s5-tree" });
  assert.deepEqual(tabs.ready.argv, ["ready", "--exclude-type=epic", "--json"]);
  assert.equal(tabs.ready.command, "bd ready --exclude-type=epic");
  assert.deepEqual(tabs.mine.argv, ["list", "--assignee", "lane-s5-tree", "--json"]);
  assert.equal(tabs.mine.command, "bd list --assignee lane-s5-tree");
  // Browse is the unfiltered surface: blocked and unassigned rows are visible
  assert.ok(Array.isArray(tabs.browse.argv) && !tabs.browse.argv.includes("--ready"),
    "Browse must not apply ready-work filtering");
});

test("Browse exposes blocked and unassigned branches (Ready/Mine are not the only navigation)", async () => {
  const f = fixture("tabs.json");
  const snap = snapOf(f);
  const { createWorkbenchState } = await import("../desktop/model.mjs");
  const w = createWorkbenchState(snap);
  const box = buildTreeRows(snap, w, { tab: "browse" });
  const ids = box.rows.filter((r) => r.kind !== "load-more").map((r) => r.id);
  assert.ok(ids.includes("blockedkid"), "Browse renders a blocked row");
  assert.ok(ids.includes("t2"), "Browse renders an unassigned row");
  // blocked rows carry a textual blocked marker (never color alone)
  const row = box.rows.find((r) => r.id === "blockedkid");
  assert.match(row.blockedWord || row.statusWord, /blocked/i);
});

// ============================================================================
// D. Refresh budgets (moved H1 ownership)
// ============================================================================

test("bounded 150-row refresh issues <=4 native queries", () => {
  const f = fixture("hierarchy.json");
  const calls = [];
  const provider = {
    run: (...argv) => {
      calls.push(argv.join(" "));
      if (argv[0] === "list") return f.issues;
      if (argv[0] === "ready") return f.ready;
      if (argv[0] === "blocked") return f.blocked;
      throw new Error("unexpected query " + argv.join(" "));
    },
    info: () => ({ database_path: "/lab/store/.beads/lab.db", config: { issue_prefix: "lab" } }),
    storeInfo: { workspace: "/lab/store", db: "/lab/store/.beads/lab.db" },
  };
  const res = refreshOnce(provider, { assignee: "lane-s5-tree", maxRows: 150 });
  assert.ok(calls.length <= 4, `<=4 native queries, got ${calls.length}: ${calls}`);
  assert.ok(calls.some((c) => /--limit 150|--limit=150/.test(c)), "row query is bounded to 150");
  assert.ok(res.snapshot.nodes.size >= 5);
});

test("warm parent/sibling/back navigation: zero native queries; only an explicit refresh queries", () => {
  const f = fixture("hierarchy.json");
  let calls = 0;
  const provider = {
    run: (...argv) => { calls++; if (argv[0] === "list") return f.issues; if (argv[0] === "ready") return f.ready; if (argv[0] === "blocked") return f.blocked; return []; },
    info: () => ({ database_path: "/lab/x", config: {} }),
    storeInfo: { workspace: "/lab/store", db: "/lab/store/.beads/lab.db" },
  };
  const { snapshot } = refreshOnce(provider, { assignee: "lane-s5-tree", maxRows: 150 });
  calls = 0;
  const w = createWorkbenchState(snapshot);
  const c = createTreeController({ snapshot, ui: w });
  const P = (key, extra = {}) => c.press({ key, isComposing: false, target: buttonTarget(), preventDefault: () => {}, ...extra });
  P("ArrowDown"); P("ArrowDown"); P("Enter"); P("ArrowLeft"); P("ArrowRight");
  P("Home"); P("End");
  w.jump("deep1", "detail"); P("ArrowLeft", { altKey: true }); P("ArrowRight", { altKey: true });
  Tree({ snapshot, ui: w });
  assert.equal(calls, 0, "warm navigation must issue ZERO native queries");
});

test("warm visual p95 <= 100ms on this test hardware (render + navigate, recorded host)", () => {
  const f = fixture("bulk150.json"); // 150-row bounded page
  const snapshot = snapOf(f);
  const w = createWorkbenchState(snapshot);
  const c = createTreeController({ snapshot, ui: w });
  const P = (key) => c.press({ key, isComposing: false, target: buttonTarget(), preventDefault: () => {} });
  const samples = [];
  for (let i = 0; i < 100; i++) {
    const t0 = process.hrtime.bigint();
    P(i % 2 ? "ArrowDown" : "ArrowRight");
    Tree({ snapshot, ui: w });
    samples.push(Number(process.hrtime.bigint() - t0) / 1e6);
  }
  samples.sort((a, b) => a - b);
  const p95 = samples[Math.floor(samples.length * 0.95)];
  assert.ok(p95 <= 100, `warm visual p95 ${p95.toFixed(2)}ms exceeds 100ms`);
});

test("coalesced 2s auto-refresh: bursts collapse to one fetch; stale and error states surface", () => {
  const f = fixture("hierarchy.json");
  let fetches = 0;
  let fail = false;
  const provider = {
    run: (...argv) => { if (argv[0] === "list") { fetches++; if (fail) throw new Error("native down"); return f.issues; } return argv[0] === "ready" ? f.ready : f.blocked; },
    info: () => ({ database_path: "/lab/x", config: {} }),
    storeInfo: { workspace: "/lab/store", db: "/lab/store/.beads/lab.db" },
  };
  let t = 0;
  const sched = createRefreshScheduler({
    provider, assignee: "lane-s5-tree", maxRows: 150,
    now: () => t, delayMs: 2000,
  });
  sched.start();
  t += 10; sched.requestRefresh();
  t += 20; sched.requestRefresh();
  t += 30; sched.requestRefresh();
  assert.equal(sched.pending(), true, "a coalescing window is open");
  t += 2000; sched.tick();
  assert.equal(fetches, 1, "three requests inside the 2s window produce one fetch");
  assert.equal(sched.state(), "ok");

  fail = true;
  sched.requestRefresh(); t += 2000; sched.tick();
  assert.equal(sched.state(), "error", "failed refresh reports an error state");
  assert.ok(sched.snapshot().stale, "the stale snapshot stays on screen (never silently blank)");
  assert.ok(textIn(Tree({ snapshot: sched.snapshot(), ui: createWorkbenchState(sched.snapshot()), scheduler: sched }), "stale"));

  fail = false;
  sched.requestRefresh(); t += 2000; sched.tick();
  assert.equal(sched.state(), "ok");
});

test("1000-row cap: explicit partial-scope + load-more, never silent truncation", () => {
  const snap = snapOf(fixture("bulk1001.json"), { bound: 5000 });
  const w = createWorkbenchState(snap);
  const box = buildTreeRows(snap, w, { maxRows: 1000 });
  const data = box.rows.filter((r) => r.kind !== "load-more");
  assert.equal(data.length, 1000, "exactly the cap of data rows render");
  assert.equal(box.partialScope, true, "partial scope is declared, not silent");
  const last = box.rows.at(-1);
  assert.equal(last.kind, "load-more");
  assert.match(last.label, /1001|more/i, "load-more row states the truth in words");
});

// ============================================================================
// E0. Expansion single-truth regressions (independent-review P1/P2/P3/P4/P4b/P6)
// The tree and the model must share ONE expansion truth: history restore,
// initial.expanded, ui.toggleExpanded and ui.jump all drive what renders.
// ============================================================================

const pressFactory = (c) => (key, extra = {}) =>
  c.press({ key, isComposing: false, target: buttonTarget(), preventDefault: () => {}, ...extra });

test("P1: collapse then Alt+Back/Alt+Forward restores expansion in the RENDERED tree", () => {
  const snap = snapOf(fixture("hierarchy.json"));
  const w = createWorkbenchState(snap);
  const c = createTreeController({ snapshot: snap, ui: w });
  const P = pressFactory(c);
  w.jump("deep1", "detail");                      // seed a history entry
  const expBefore = [...expandedOf(snap, w)].sort();
  w.jump("a");
  P("ArrowLeft");                                 // collapse a (cursor in place)
  assert.ok(!c.visibleIds().includes("mid"), "collapse hid mid");
  P("ArrowLeft", { altKey: true });               // Alt+Back
  assert.deepEqual([...expandedOf(snap, w)].sort(), expBefore,
    "restored bundle's expansion must equal the pre-collapse expansion");
  assert.ok(c.visibleIds().includes("mid"), "back re-expands the RENDERED tree");
  P("ArrowRight", { altKey: true });               // Alt+Forward
  assert.deepEqual([...expandedOf(snap, w)].sort(), expBefore,
    "forward keeps one expansion truth with the model");
});

test("P2: model initial.expanded is the tree's starting view (collapsed parents hide descendants, no tail re-emit)", () => {
  const snap = snapOf(fixture("hierarchy.json"));
  const w = createWorkbenchState(snap, { selection: "deep1", expanded: new Set(["root"]) });
  const c = createTreeController({ snapshot: snap, ui: w });
  const vis = c.visibleIds();
  assert.ok(!vis.includes("mid") && !vis.includes("deep1") && !vis.includes("a"),
    "tree view honors initial.expanded: epic collapsed hides its whole subtree");
  const modelVis = w.visibleRows().map((r) => r.id);
  assert.ok(!modelVis.includes("mid") && !modelVis.includes("deep1") && !modelVis.includes("a"),
    "model view honors initial.expanded too: collapsed descendants never re-emit at depth 0");
  // cursor on a hidden row: tree stays reachable — exactly one tabbable row remains
  const items = nodesOf(Tree({ snapshot: snap, ui: w })).filter((n) => n.props.role === "treeitem");
  assert.equal(items.filter((r) => r.props.tabIndex === 0).length, 1,
    "selection on a hidden descendant must not leave the tree with zero tabbable rows");
});

test("P3: ui.toggleExpanded (the model's expansion API) drives the rendered tree", () => {
  const snap = snapOf(fixture("hierarchy.json"));
  const w = createWorkbenchState(snap);
  const c = createTreeController({ snapshot: snap, ui: w });
  assert.ok(c.visibleIds().includes("a"));
  w.toggleExpanded("epic"); // collapse via the MODEL's documented API
  assert.ok(!c.visibleIds().includes("a"), "toggleExpanded collapses in the tree view");
  assert.ok(!w.visibleRows().map((r) => r.id).includes("a"),
    "tree view and model view agree after toggleExpanded");
  const el = Tree({ snapshot: snap, ui: w });
  assert.equal(findById(el, "row:epic").props["aria-expanded"], false,
    "aria-expanded follows the single truth");
});

test("P4: ui.jump is a reveal primitive — jumping into a collapsed subtree renders the row", () => {
  const snap = snapOf(fixture("hierarchy.json"));
  const w = createWorkbenchState(snap);
  const c = createTreeController({ snapshot: snap, ui: w });
  const P = pressFactory(c);
  w.jump("a");
  P("ArrowLeft"); // collapse a via controller (model truth)
  assert.ok(!c.visibleIds().includes("deep1"), "deep1 hidden pre-jump");
  w.jump("deep1"); // the search lane's reveal path
  assert.ok(c.visibleIds().includes("deep1"), "jump reveals the ancestor chain");
  assert.equal(w.selection, "deep1");
  assert.ok(findById(Tree({ snapshot: snap, ui: w }), "row:deep1"), "jumped row renders");
});

test("P4b: cursor on a previously-hidden row keeps exactly one tabbable + one aria-selected row", () => {
  const snap = snapOf(fixture("hierarchy.json"));
  const w = createWorkbenchState(snap);
  const c = createTreeController({ snapshot: snap, ui: w });
  const P = pressFactory(c);
  w.jump("a");
  P("ArrowLeft"); // collapse a
  w.jump("deep1"); // search-style reveal
  const items = nodesOf(Tree({ snapshot: snap, ui: w })).filter((n) => n.props.role === "treeitem");
  assert.equal(items.filter((n) => n.props.tabIndex === 0).length, 1, "roving tab stop exists");
  assert.equal(items.filter((n) => n.props["aria-selected"] === true).length, 1,
    "the selected row is visible to AT");
});

test("P6: buildTreeRows renders the same collapsed view as Tree, with per-parent posinset <= setsize", () => {
  const snap = snapOf(fixture("hierarchy.json"));
  const w = createWorkbenchState(snap);
  const c = createTreeController({ snapshot: snap, ui: w });
  const P = pressFactory(c);
  w.jump("epic");
  P("ArrowLeft"); // collapse epic via controller
  const treeView = c.visibleIds();
  const box = buildTreeRows(snap, w);
  const data = box.rows.filter((r) => r.kind !== "load-more");
  const boxIds = data.map((r) => r.id);
  assert.deepEqual(boxIds, treeView, "builder rows are exactly the Tree rows");
  const ordinal = new Map();
  for (const r of data) {
    const pk = JSON.stringify(r.parentId);
    const pos = (ordinal.get(pk) ?? 0) + 1;
    ordinal.set(pk, pos);
    assert.equal(r.posinset, pos, `posinset is the per-parent ordinal for ${r.id}`);
    assert.ok(r.posinset >= 1 && r.posinset <= r.setsize,
      `posinset<=setsize for ${r.id} (${r.posinset}>${r.setsize})`);
  }
});

// ============================================================================
// E. Native reproduction: Ready/Mine tabs reproduce their bd commands
// ============================================================================
const LAB = "/home/hermes/.hermes/work/beads-lab";
const BIN = path.join(LAB, "bin", "bd");
const ACTOR = "lane-s5-tree-native-20260928a";
const FIX = path.join(here, ".tree-fixtures");

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
  execFileSync("git", ["-C", cwd, "config", "user.name", "lane-s5-tree"]);
  execFileSync("git", ["-C", cwd, "config", "user.email", "lane-s5-tree@localhost"]);
  execFileSync(BIN, ["init", "--prefix", "tst"], { cwd, encoding: "utf8" });
  return { cwd, info: bdRead(cwd, "info") };
}
function createNative(cwd, title, extra = []) {
  const out = bdMutate(cwd, "create", title, ...extra, "--allow-empty-description", "--json");
  return JSON.parse(out).id;
}

test("native parity: Ready tab rows === `bd ready --exclude-type=epic`, Mine tab rows === `bd list --assignee`", () => {
  const st = nativeStore(`parity-${process.pid}`);
  try {
    const epic = createNative(st.cwd, "tree epic", ["-t", "epic"]);
    const t1 = createNative(st.cwd, "tree task one", ["--parent", epic]);
    const t2 = createNative(st.cwd, "tree task two");
    const mine = createNative(st.cwd, "tree mine", ["--parent", epic]);
    const wall = createNative(st.cwd, "tree wall");
    const kid = createNative(st.cwd, "tree blocked kid");
    bdMutate(st.cwd, "dep", "add", kid, wall);
    bdMutate(st.cwd, "update", mine, "--assignee", ACTOR, "-s", "in_progress");

    // the commands under test, run natively:
    const readyRows = bdRead(st.cwd, "ready", "--exclude-type=epic");
    const mineRows = bdRead(st.cwd, "list", "--assignee", ACTOR);
    const readyIds = readyRows.map((r) => r.id);
    const mineIds = mineRows.map((r) => r.id);
    assert.ok(readyIds.includes(t1) && readyIds.includes(t2), "native ready contains the open tasks");
    assert.ok(!readyIds.includes(epic), "native ready excludes epics");
    assert.ok(!readyIds.includes(kid), "native ready excludes the blocked kid");
    assert.deepEqual(mineIds, [mine], "native list --assignee returns exactly the claimed issue");

    // tab-to-command equality: the plugin's tab pipeline over the same rows
    // reproduces the command results verbatim — same IDs, same order.
    const allRows = bdRead(st.cwd, "list", "--all", "--limit", "0");
    const tabs = tabCommands({ assignee: ACTOR });
    assert.deepEqual(tabs.ready.argv.slice(0, 2), ["ready", "--exclude-type=epic"]);
    assert.deepEqual(tabs.mine.argv.slice(0, 3), ["list", "--assignee", ACTOR]);

    const provider = {
      run: (...argv) => {
        if (argv[0] === "ready" && argv[1] === "--exclude-type=epic") return readyRows;
        if (argv[0] === "list" && argv[1] === "--assignee") return mineRows;
        if (argv[0] === "list") return allRows;
        if (argv[0] === "blocked") return allRows.filter((r) => r.status === "blocked");
        return [];
      },
      info: () => st.info,
      storeInfo: { workspace: st.cwd, db: st.info.database_path },
    };
    const ready = refreshOnce(provider, { assignee: ACTOR, maxRows: 150, tab: "ready" });
    assert.deepEqual(ready.tabIds, readyIds, "Ready tab rows byte-equal `bd ready --exclude-type=epic`");
    const mine2 = refreshOnce(provider, { assignee: ACTOR, maxRows: 150, tab: "mine" });
    assert.deepEqual(mine2.tabIds, mineIds, "Mine tab rows byte-equal `bd list --assignee`");

    // Browse still exposes the blocked kid and the unassigned row
    const browse = refreshOnce(provider, { assignee: ACTOR, maxRows: 150, tab: "browse" });
    const bset = new Set(browse.rows.map((r) => r.id));
    assert.ok(bset.has(kid), "Browse exposes the blocked branch");
    assert.ok(bset.has(t2), "Browse exposes the unassigned branch");

    // tree rows byte-match native parent fields
    const parentOf = Object.fromEntries(allRows.map((r) => [r.id, r.parent ?? null]));
    for (const r of browse.rows) {
      if (r.kind === "load-more") continue;
      assert.equal(JSON.stringify(r.parentId), JSON.stringify(parentOf[r.id]),
        `native parent field byte-matched for ${r.id}`);
    }
  } finally {
    rmSync(FIX, { recursive: true, force: true });
  }
});

test("hbl-pnu.2.10: Tab onto the fallback tab stop, first ArrowDown moves FROM that row (real-Chrome finding: first arrow was swallowed)", () => {
  const f = fixture("hierarchy.json");
  const snap = snapOf(f);
  const w = createWorkbenchState(snap);
  const c = createTreeController({ snapshot: snap, ui: w });
  const vis = w.visibleRows().map((r) => r.id);
  assert.equal(w.selection, null, "fresh model: no cursor yet (tab stop falls back to the first row)");
  const onFirst = { tagName: "LI", getAttribute: (k) => (k === "data-tree-row" ? vis[0] : null) };
  c.press({ key: "ArrowDown", isComposing: false, target: onFirst, preventDefault() {} });
  assert.equal(w.selection, vis[1], "first ArrowDown lands on the SECOND row, not the one already focused");
});
