// desktop/tree.mjs — hbl-pnu.2.2: conventional ARIA tree + navigation
// controller for the beads workbench.
//
// Purity rules (CONTRACTS-v3 C1, bd-expert, desktop-plugin rules):
//  - hierarchy comes from the snapshot's parent FIELD only (model.mjs);
//    ID-dot spelling is never ancestry and is never parsed here;
//  - zero I/O: native data arrives through an injected `provider`
//    ({run(...argv), info(), storeInfo}) — the same fixed-argv boundary
//    native.py/desktop/plugin.js use; this module never shells out itself;
//  - selection (the cursor) and keyboard focus are SEPARATE labels on every
//    row; only Enter promotes cursor -> focus (delegated to model.mjs
//    createWorkbenchState, reused unchanged);
//  - the keymap is DATA: every binding resolves programmatically, and no
//    bare key (any single-character key, h/l included — dropped per owner
//    ruling) ever resolves while a text input / contentEditable has focus
//    or an IME composition is active. Only explicit modifier combos survive
//    in editors, and only outside composition.
import { jsx } from "react/jsx-runtime";
import { buildSnapshot, createWorkbenchState, invalidateOnMutation } from "./model.mjs";

export const INDENT_PX = 16; // reflow-safe: px indent, plus aria-level for AT
const DEFAULT_ASSIGNEE = "lane-s5-tree"; // lab lane identity (fixture default)

// ---- status: glyph AND word, never glyph-only, never color alone ----------
const STATUS_GLYPHS = {
  open: "\u25CB", ready: "\u25D0", in_progress: "\u25D6", blocked: "\u25A0",
  deferred: "\u23F8", closed: "\u2713", done: "\u2713", hooked: "\u2935",
  unknown: "?",
};

export function statusLabel(status) {
  if (typeof status !== "string" || status === "") return "? unknown";
  const glyph = STATUS_GLYPHS[status] ?? "\u2753"; // unknown statuses: own glyph
  return `${glyph} ${status}`; // word is the stored value, verbatim
}

// ---- epic progress: matches native `bd epic status` semantics -------------
// native (probed bd 1.3.0): total/closed over the epic's DIRECT children.
export function epicProgress(snapshot, id) {
  const node = snapshot.nodes.get(id);
  const rec = snapshot.byId.get(id);
  if (!node || !node.childIds.length || rec?.issue_type !== "epic") return null;
  let closed = 0;
  for (const c of node.childIds) {
    const st = snapshot.byId.get(c)?.status;
    if (typeof st === "string" && /^(closed|done)$/i.test(st)) closed += 1;
  }
  return { total: node.childIds.length, closed };
}

// ---- tree rows: hierarchy only, byte-matching snapshot parents ------------
// THE row source is treeVisibleRows — the one expansion truth (model's
// `expanded` via ui). Builder and Tree therefore render identical rows;
// posinset is the per-parent ordinal among rendered rows, setsize the
// per-parent sibling count in the loaded set (same rule as Tree).
export function buildTreeRows(snapshot, ui, opts = {}) {
  const maxRows = opts.maxRows ?? Infinity;
  const rows = treeVisibleRows(snapshot, ui);
  const partial = rows.length > maxRows;
  const kept = partial ? rows.slice(0, maxRows) : rows;
  const setsizeOf = (id) => siblingCount(snapshot, ui, id);
  const posByParent = new Map(); // 1-based ordinal among rendered siblings
  const out = kept.map(({ id, depth, boundary }) => {
    const node = snapshot.nodes.get(id);
    const rec = snapshot.byId.get(id) ?? ui.revealed.get(id) ?? {};
    const parentId = rowParent(snapshot, ui, id);
    const pk = parentId == null ? "" : parentId;
    const posinset = (posByParent.get(pk) ?? 0) + 1;
    posByParent.set(pk, posinset);
    const status = node?.storedStatus ?? rec.status ?? "unknown";
    const row = {
      kind: "row", id,
      parentId, boundary: !!boundary,
      depth,
      indentPx: depth * INDENT_PX,
      statusWord: status,
      statusGlyph: statusLabel(status).split(" ")[0],
      posinset,
      setsize: setsizeOf(id),
      title: typeof rec.title === "string" ? rec.title : id, // verbatim
    };
    if (snapshot.blockedIds?.has(id)) row.blockedWord = "blocked";
    if (node?.derivedBlocked === true && !row.blockedWord) row.blockedWord = "blocked (derived)";
    const prog = epicProgress(snapshot, id);
    if (prog) row.epicProgress = `${prog.closed}/${prog.total}`;
    return row;
  });
  if (partial) {
    out.push({ kind: "load-more", id: "\u0000load-more",
      label: `Showing first ${maxRows} of ${rows.length}+ rows \u2014 partial scope, load more` });
  }
  return { rows: out, partialScope: partial, maxRows };
}

// ---- keymap: data, programmatically resolvable ----------------------------
export const KEYMAP = [
  { keys: ["ArrowUp"], command: "cursor-up", desc: "Move selection cursor up" },
  { keys: ["ArrowDown"], command: "cursor-down", desc: "Move selection cursor down" },
  { keys: ["ArrowRight"], command: "expand-or-first-child", desc: "Expand, or visit first child" },
  { keys: ["ArrowLeft"], command: "collapse-or-parent", desc: "Collapse in place, or visit parent" },
  { keys: ["Enter"], command: "focus-cursor", desc: "Keyboard-focus the selected row" },
  { keys: ["Home"], command: "cursor-first", desc: "First visible row" },
  { keys: ["End"], command: "cursor-last", desc: "Last visible row" },
  { keys: ["Tab"], command: "exit-tree", desc: "Leave the tree (never intercepted)" },
  { keys: ["Alt+ArrowLeft"], command: "history-back", desc: "App back" },
  { keys: ["Alt+ArrowRight"], command: "history-forward", desc: "App forward" },
  { keys: ["?"], command: "toggle-help", desc: "Shortcut help overlay" },
  { keys: ["Escape"], command: "close-help", desc: "Close the help overlay, focus back to the prior row" },
];

const isTextTarget = (target) => {
  if (!target) return false;
  const tag = String(target.tagName ?? "").toUpperCase();
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return true;
  return target.isContentEditable === true;
};

const hasModifier = (keys) => keys.includes("+");

// Returns the bound command for an event, or null (swallowed / unbound).
// "Every binding resolvable without a human": a pure function of (KEYMAP, ev).
export function resolveKey(ev) {
  if (!ev || typeof ev.key !== "string") return null;
  if (ev.isComposing === true) return null; // IME composition: never intercept
  const mod = !!(ev.altKey || ev.ctrlKey || ev.metaKey);
  // Editor/text-input swallow: NO bare key (any unmodified single-character
  // key — h/l scheme dropped per owner ruling) resolves while a text input,
  // textarea or contentEditable owns focus. Letters, digits, space, '?' and
  // the plain arrows/Enter/Home/End all fall through to the widget.
  if (!mod && isTextTarget(ev.target)) return null;
  for (const b of KEYMAP) {
    const parts = b.keys.join("+").split("+");
    const key = parts[parts.length - 1];
    if (key !== ev.key) continue;
    const wantAlt = parts.includes("Alt");
    const wantCtrl = parts.includes("Ctrl");
    const wantMeta = parts.includes("Meta");
    if (!!ev.altKey !== wantAlt || !!ev.ctrlKey !== wantCtrl || !!ev.metaKey !== wantMeta) continue;
    return b.command;
  }
  return null;
}

// ---- expansion: ONE truth, owned by the model ------------------------------
// model.mjs's createWorkbenchState owns `expanded` (visibleRows/toggleExpanded/
// jump/history bundles all read+write it). The tree renders THAT state and
// never keeps a parallel copy: the old per-ui WeakMap let model history
// restore, initial.expanded, ui.toggleExpanded and ui.jump all go unrendered.
// The model exposes its live set via ui.expanded; expandedOf is the
// read-only snapshot for tests/assertions.
function modelExpanded(snapshot, ui) {
  return ui.expanded;
}
// read-only view of THE expansion (tests/assertions) — delegates to the model
export function expandedOf(snapshot, ui) {
  return new Set(modelExpanded(snapshot, ui));
}

// The model owns both expansion and transient native rows. The tree must
// consume its exact visible rows rather than maintaining a second traversal.
function treeVisibleRows(snapshot, ui) { return ui.visibleRows(); }

function rowParent(snapshot, ui, id) {
  return snapshot.nodes.get(id)?.parent ?? ui.revealed.get(id)?.parent ?? null;
}
function rowChildren(snapshot, ui, id) {
  return [...(snapshot.nodes.get(id)?.childIds ?? []),
    ...[...ui.revealed].filter(([, r]) => r.parent === id).map(([rid]) => rid)];
}
function siblingCount(snapshot, ui, id) {
  const parent = rowParent(snapshot, ui, id);
  if (parent != null) return rowChildren(snapshot, ui, parent).length || 1;
  return [...snapshot.nodes.values()].filter((n) => n.parent === null).length
    + [...ui.revealed.values()].filter((r) => r.parent == null).length || 1;
}

// ---- navigation controller ---------------------------------------------------
// Cursor moves go through model.mjs arrow() ONLY — ui.jump() would promote
// focus and push app history, which arrows must never do (selection cursor is
// separated from keyboard focus).
export function createTreeController({ snapshot, ui }) {
  const box = { helpOpen: false, tabExit: false };
  const rows = () => treeVisibleRows(snapshot, ui);
  const idxOf = (id) => rows().findIndex((r) => r.id === id);
  // Step the model cursor one row at a time in model order until it lands on
  // the target, so model-only tail rows are transits, never destinations.
  const slide = (toId) => {
    let guard = ui.visibleRows().length * 2 + 4;
    while (ui.selection !== toId && guard-- > 0) {
      const all = ui.visibleRows();
      const j = all.findIndex((r) => r.id === toId);
      if (j === -1) break;
      const i = all.findIndex((r) => r.id === ui.selection);
      ui.arrow(i === -1 ? 1 : Math.sign(j - i) || 1);
    }
  };
  const stepBy = (d) => {
    const vis = rows();
    if (ui.selection == null) {
      // fresh cursor lands on the ready head of the board (readyIds order);
      // with no ready rows, the first/last visible row.
      let t = vis[0];
      if (d > 0 && snapshot.readyIds?.size) {
        const rank = new Map();
        let k = 0;
        for (const id of snapshot.readyIds) if (!rank.has(id)) rank.set(id, k++);
        const cands = vis.filter((r) => rank.has(r.id));
        if (cands.length) t = cands.sort((a, b) => rank.get(a.id) - rank.get(b.id))[0];
      }
      if (t) slide(t.id);
      return;
    }
    const i = idxOf(ui.selection);
    const t = vis[Math.max(0, Math.min(vis.length - 1, i + d))];
    if (t) slide(t.id);
  };
  // The rendered tab stop falls back to the first row while the model cursor
  // is null; a keyboard user who Tabs in is ON that row, so the first arrow
  // must move FROM it (not merely initialise the cursor onto it).
  const seedFromTabStop = (ev) => {
    if (ui.selection != null) return;
    const id = ev?.target?.getAttribute?.("data-tree-row");
    if (id && idxOf(id) !== -1) slide(id);
  };

  const press = (ev) => {
    const cmd = resolveKey(ev);
    if (cmd === null) return null;
    if (cmd === "exit-tree") { box.tabExit = true; return cmd; } // Tab exits natively
    ev.preventDefault?.();
    if (cmd.startsWith("cursor-")) seedFromTabStop(ev);
    const node = () => snapshot.nodes.get(ui.selection);
    const children = () => rowChildren(snapshot, ui, ui.selection);
    const exp = () => modelExpanded(snapshot, ui);
    switch (cmd) {
      case "cursor-up": stepBy(-1); break;
      case "cursor-down": stepBy(1); break;
      case "cursor-first": { const r = rows(); if (r.length) slide(r[0].id); break; }
      case "cursor-last": { const r = rows(); if (r.length) slide(r.at(-1).id); break; }
      case "expand-or-first-child": {
        const kids = children();
        if (kids.length && !node()?.cyclic) {
          if (!exp().has(ui.selection)) exp().add(ui.selection);
          else slide(kids[0]);
        }
        break;
      }
      case "collapse-or-parent": {
        if (children().length && !node()?.cyclic && exp().has(ui.selection)) exp().delete(ui.selection);
        else {
          const parent = rowParent(snapshot, ui, ui.selection);
          if (parent != null && rows().some((r) => r.id === parent)) slide(parent);
        }
        break;
      }
      case "focus-cursor": ui.enter(); break;
      case "history-back": ui.back(); break;
      case "history-forward": ui.forward(); break;
      case "toggle-help":
        if (!box.helpOpen) box.helpReturnFocus = ui.focus; // prior row, captured at open
        box.helpOpen = !box.helpOpen;
        break;
      case "close-help": // hbl-pnu.2.9: Esc closes help; focus returns to the prior row
        if (box.helpOpen) {
          box.helpOpen = false;
          const prior = box.helpReturnFocus;
          if (prior != null && ui.focus !== prior && rows().some((r) => r.id === prior)) ui.jump(prior);
        }
        break;
      default: return null;
    }
    return cmd;
  };
  return {
    press,
    get helpOpen() { return box.helpOpen; },
    get tabExit() { return box.tabExit; },
    get selection() { return ui.selection; },
    get focus() { return ui.focus; },
    visibleIds() { return rows().map((r) => r.id); },
  };
}

// ---- shortcut-help overlay: exactly the bound keys -------------------------
export function ShortcutHelp() {
  return jsx("div", {
    role: "dialog", "aria-label": "Keyboard shortcuts",
    children: KEYMAP.map((b) => jsx("div", {
      "data-shortcut": b.keys.join("+"),
      children: [jsx("kbd", { children: b.keys.join("+") }), jsx("span", { children: `${b.command} — ${b.desc}` })],
    }, b.keys.join("+"))),
  });
}

// ---- tabs: entry filters that reproduce native commands verbatim -----------
export function tabCommands({ assignee = DEFAULT_ASSIGNEE } = {}) {
  return {
    ready: {
      argv: ["ready", "--exclude-type=epic", "--json"],
      command: "bd ready --exclude-type=epic",
    },
    mine: {
      argv: ["list", "--assignee", String(assignee), "--json"],
      command: `bd list --assignee ${assignee}`,
    },
    browse: {
      argv: ["list", "--all", "--limit", "0", "--json"],
      command: "bd list --all --limit 0",
    },
  };
}

// ---- refresh budgets (moved H1 ownership) -----------------------------------
// One refresh = at most FOUR native queries, all bounded:
//   rows (tab command) + blocked read (positive evidence) + info (store id)
//   [+ one cacheable store-info call only when not supplied].
// Warm navigation NEVER calls this (see controller: pure model ops).
export function refreshOnce(provider, opts = {}) {
  const maxRows = opts.maxRows ?? 150;
  const tabs = tabCommands({ assignee: opts.assignee ?? DEFAULT_ASSIGNEE });
  const tab = opts.tab ?? "browse";
  const queries = [];
  let rows, tabIds = null;
  if (tab === "ready") {
    rows = provider.run(...tabs.ready.argv);
    queries.push(tabs.ready.command);
  } else if (tab === "mine") {
    rows = provider.run(...tabs.mine.argv);
    queries.push(tabs.mine.command);
  } else {
    rows = provider.run("list", "--all", "--limit", String(maxRows), "--json");
    queries.push(`bd list --all --limit ${maxRows}`);
  }
  rows = Array.isArray(rows) ? rows : (rows?.rows ?? []);
  tabIds = rows.map((r) => r.id);
  const blocked = opts.skipBlocked ? null
    : provider.run("blocked", "--limit", String(maxRows), "--json");
  queries.push(`bd blocked --limit ${maxRows}`);
  const storeInfo = opts.storeInfo ?? provider.storeInfo ?? provider.info?.();
  const snap = buildSnapshot({
    issues: rows,
    ready: tab === "ready" ? rows.map((r) => r.id) : undefined,
    blocked: Array.isArray(blocked) ? blocked.map((r) => (typeof r === "string" ? r : r.id)) : blocked ?? [],
    storeInfo,
  }, { fetchedAt: opts.fetchedAt ?? Date.now(), bound: maxRows, ttlMs: opts.ttlMs ?? 60_000 });
  const treeBox = buildTreeRows(snap, createWorkbenchState(snap), { maxRows });
  return { snapshot: snap, rows: treeBox.rows, tabIds, queries, partialScope: treeBox.partialScope };
}

// coalesced 2s auto-refresh with honest stale/error state
export function createRefreshScheduler({ provider, now = () => Date.now(), delayMs = 2000, ...refreshOpts }) {
  let state = "init", snapshot = null, timerAt = null, opts = { ...refreshOpts };
  const api = {
    start() { return api; },
    requestRefresh(more = {}) { opts = { ...opts, ...more }; if (timerAt === null) timerAt = now() + delayMs; },
    pending() { return timerAt !== null; },
    tick() {
      if (timerAt === null || now() < timerAt) return false;
      timerAt = null;
      try {
        const res = refreshOnce(provider, opts);
        snapshot = res.snapshot; state = "ok";
      } catch {
        if (snapshot) invalidateOnMutation(snapshot);
        state = "error";
      }
      return true;
    },
    state() { return state; },
    snapshot() { return snapshot; },
  };
  return api;
}

// ---- the ARIA tree component -------------------------------------------------
export function Tree({ snapshot, ui, scheduler }) {
  const rows = treeVisibleRows(snapshot, ui);
  const expanded = modelExpanded(snapshot, ui);
  const setsizeOf = (id) => siblingCount(snapshot, ui, id);
  // The roving tab stop must exist: if the model cursor sits on a row the
  // current expansion hides (e.g. initial.selection deep in a collapsed
  // subtree), fall back to the first rendered row.
  const renderedIds = new Set(rows.map((r) => r.id));
  const selId = (ui.selection != null && renderedIds.has(ui.selection))
    ? ui.selection : (rows[0]?.id ?? null);
  const focusId = ui.focus;
  const posByParent = new Map(); // 1-based ordinal among visible siblings
  const items = rows.map((r, i) => {
    const n = snapshot.nodes.get(r.id);
    const rec = snapshot.byId.get(r.id) ?? ui.revealed.get(r.id) ?? {};
    const status = n?.storedStatus ?? rec.status ?? null;
    const boundary = r.boundary ? "parent unknown — bounded search boundary" : null;
    let rowLabel = (rec.title ?? r.id) + " " + statusLabel(status);
    if (boundary) rowLabel += `, ${boundary}`;
    const prog = epicProgress(snapshot, r.id);
    if (prog) rowLabel += ` epic progress ${prog.closed}/${prog.total}`;
    const parent = rowParent(snapshot, ui, r.id);
    const pk = parent == null ? "" : parent;
    const posinset = (posByParent.get(pk) ?? 0) + 1;
    posByParent.set(pk, posinset);
    const kids = rowChildren(snapshot, ui, r.id);
    const props = {
      role: "treeitem",
      id: `row:${r.id}`,
      "data-tree-row": r.id,
      "aria-label": `${rowLabel}, level ${r.depth + 1}, ` +
        `${r.id === selId ? "selection cursor, " : ""}${r.id === focusId ? "keyboard focus" : ""}`,
      "aria-level": r.depth + 1,
      "aria-posinset": posinset,
      "aria-setsize": setsizeOf(r.id),
      "aria-selected": r.id === selId,
      "aria-expanded": kids.length ? expanded.has(r.id) : undefined,
      "data-parent-boundary": boundary ? "unknown" : undefined,
      "data-keyboard-focus": String(r.id === focusId),
      "data-tree-focusable": String(r.id === selId),
      tabIndex: r.id === selId ? 0 : -1, // roving tab stop
      style: { paddingInlineStart: `${r.depth * INDENT_PX}px` },
      children: [
        // hbl-pnu.2.10 (F4): every field is its OWN element — the gap and
        // borders come from the shipped stylesheet, so no field ever paints
        // fused against its neighbour ("title○ open" was the failure).
        jsx("span", { className: "row-title", "data-indent-px": r.depth * INDENT_PX,
          children: rec.title ?? r.id }, `title:${r.id}`),
        jsx("span", { className: "status-chip", "aria-label": `status: ${status ?? "unknown"}`,
          children: (() => {
            const lbl = statusLabel(status);
            const sp = lbl.indexOf(" ");
            return sp === -1
              ? [jsx("span", { children: lbl }, "w")]
              : [jsx("span", { className: "status-glyph", children: lbl.slice(0, sp) }, "g"),
                 jsx("span", { className: "status-word", children: lbl.slice(sp + 1) }, "w")];
          })() }, `status:${r.id}`),
        ...(boundary ? [jsx("span", { className: "boundary-chip", role: "status",
          children: boundary }, `boundary:${r.id}`)] : []),
        ...(prog ? [jsx("span", { className: "progress-chip",
          "aria-label": `epic progress ${prog.closed}/${prog.total}`,
          children: `epic progress ${prog.closed}/${prog.total}` }, `prog:${r.id}`)] : []),
        ...(snapshot.blockedIds?.has(r.id) ? [jsx("span", { className: "blocked-chip",
          "aria-label": "blocked in native blocked list", children: "blocked" },
          `blocked:${r.id}`)] : []),
      ],
    };
    if (snapshot.blockedIds?.has(r.id)) props["data-blocked-word"] = "blocked";
    return jsx("div", props, r.id);
  });
  const status = scheduler && scheduler.state() !== "ok"
    ? jsx("div", { role: "status", children: `stale: last-good data (refresh ${scheduler.state()})` })
    : null;
  return jsx("div", {
    role: "tree", "aria-label": "Beads workbench tree (cursor and keyboard focus are separate; Enter focuses)",
    "data-indent-px": INDENT_PX,
    children: [status, ...items].filter(Boolean),
  });
}
