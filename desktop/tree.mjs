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
export function buildTreeRows(snapshot, ui, opts = {}) {
  const maxRows = opts.maxRows ?? Infinity;
  const rows = ui.visibleRows();
  const partial = rows.length > maxRows;
  const kept = partial ? rows.slice(0, maxRows) : rows;
  // setsize = observed siblings in the LOADED set (consistent with what is
  // rendered), keyed by the parent FIELD value.
  const byParent = new Map();
  for (const n of snapshot.nodes.values()) {
    const k = n.parent == null ? "" : n.parent;
    byParent.set(k, (byParent.get(k) ?? 0) + 1);
  }
  const out = kept.map(({ id, depth }, i) => {
    const node = snapshot.nodes.get(id);
    const rec = snapshot.byId.get(id) ?? {};
    const pk = node.parent == null ? "" : node.parent;
    const row = {
      kind: "row", id,
      parentId: node.parent ?? null, // byte-match of the snapshot parent field
      depth,
      indentPx: depth * INDENT_PX,
      statusWord: node.storedStatus ?? "unknown",
      statusGlyph: statusLabel(node.storedStatus).split(" ")[0],
      posinset: i + 1,
      setsize: byParent.get(pk) ?? 1,
      title: typeof rec.title === "string" ? rec.title : id, // verbatim
    };
    if (snapshot.blockedIds?.has(id)) row.blockedWord = "blocked";
    if (node.derivedBlocked === true && !row.blockedWord) row.blockedWord = "blocked (derived)";
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
    const parts = b.keys.split("+");
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

// ---- navigation controller ---------------------------------------------------
// Cursor state lives in model.mjs's createWorkbenchState (reused unchanged):
// arrow() moves the SELECTION cursor only; enter() is the sole focus promoter;
// jump()/back()/forward() are the app-level history used by Alt+Arrow.
export function createTreeController({ snapshot, ui }) {
  const box = { helpOpen: false, tabExit: false };
  const rows = () => ui.visibleRows();
  const idxOf = (id) => rows().findIndex((r) => r.id === id);
  // Cursor moves go through model.mjs arrow()/toggleExpanded() ONLY —
  // ui.jump() would promote focus and push app history, which arrows must
  // never do (selection cursor is separated from keyboard focus).
  const slide = (toId) => {
    let guard = rows().length + 1;
    while (ui.selection !== toId && guard-- > 0) ui.arrow(idxOf(toId) - idxOf(ui.selection));
  };

  const press = (ev) => {
    const cmd = resolveKey(ev);
    if (cmd === null) return null;
    if (cmd === "exit-tree") { box.tabExit = true; return cmd; } // Tab exits natively
    ev.preventDefault?.();
    const node = () => snapshot.nodes.get(ui.selection);
    switch (cmd) {
      case "cursor-up": ui.arrow(-1); break;
      case "cursor-down": ui.arrow(1); break;
      case "cursor-first": { const r = rows(); if (r.length) slide(r[0].id); break; }
      case "cursor-last": { const r = rows(); if (r.length) slide(r.at(-1).id); break; }
      case "expand-or-first-child": {
        const n = node();
        if (n && n.childIds.length) {
          if (!ui.expanded.has(ui.selection)) ui.toggleExpanded(ui.selection);
          else slide(n.childIds[0]); // first child is the next visible row
        }
        break;
      }
      case "collapse-or-parent": {
        const n = node();
        if (n && n.childIds.length && ui.expanded.has(ui.selection)) ui.toggleExpanded(ui.selection);
        else if (n && n.parent != null) slide(n.parent);
        break;
      }
      case "focus-cursor": ui.enter(); break;
      case "history-back": ui.back(); break;
      case "history-forward": ui.forward(); break;
      case "toggle-help": box.helpOpen = !box.helpOpen; break;
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
  const rows = ui.visibleRows();
  const setsizeOf = (id) => {
    const n = snapshot.nodes.get(id);
    let k = 0;
    for (const m of snapshot.nodes.values()) if ((m.parent ?? null) === (n?.parent ?? null)) k += 1;
    return Math.max(1, k);
  };
  const selId = ui.selection ?? (rows[0]?.id ?? null);
  const focusId = ui.focus;
  const items = rows.map((r, i) => {
    const n = snapshot.nodes.get(r.id);
    const rec = snapshot.byId.get(r.id) ?? {};
    const rowLabel = (rec.title ?? r.id) + " " + statusLabel(n?.storedStatus ?? null);
    const prog = epicProgress(snapshot, r.id);
    if (prog) rowLabel += ` epic progress ${prog.closed}/${prog.total}`;
    const props = {
      role: "treeitem",
      id: `row:${r.id}`,
      "data-tree-row": r.id,
      "aria-label": `${rowLabel}, level ${(n?.depth ?? 0) + 1}, ` +
        `${r.id === selId ? "selection cursor, " : ""}${r.id === focusId ? "keyboard focus" : ""}`,
      "aria-level": (n?.depth ?? 0) + 1,
      "aria-posinset": i + 1,
      "aria-setsize": setsizeOf(r.id),
      "aria-selected": r.id === selId,
      "aria-expanded": n && n.childIds.length ? ui.expanded.has(r.id) : undefined,
      "data-keyboard-focus": String(r.id === focusId),
      "data-tree-focusable": String(r.id === selId),
      tabIndex: r.id === selId ? 0 : -1, // roving tab stop
      style: { paddingInlineStart: `${(n?.depth ?? 0) * INDENT_PX}px` },
      // glyph AND word as visible text nodes — never glyph-only, never color
      children: [
        jsx("span", { "data-indent-px": (n?.depth ?? 0) * INDENT_PX, children: rowBox.title }),
        statusLabel(n?.storedStatus ?? null),
        ...(prog ? [`epic progress ${prog.closed}/${prog.total}`] : []),
        ...(snapshot.blockedIds?.has(r.id) ? ["blocked"] : []),
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
