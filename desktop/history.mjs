// desktop/history.mjs — clickable breadcrumb (parent-field chain
// over an injected snapshot) + INDEPENDENT history-stack model and panel.
//
// Purity rules:
//  - breadcrumb ancestry comes from the snapshot's parent-FIELD chain only,
//    re-resolved live on every render; ID spelling is never ancestry;
//  - the breadcrumb widget and the history stack share NO state: breadcrumb
//    consumes snapshot+id, the stack consumes caller-pushed bundles;
//  - this module performs zero I/O and imports nothing but react/jsx-runtime
//    (the app's loader maps it; no core stores, no DOM, no backend RPC);
//  - restored bundles are deep clones, frozen — the stack can never be
//    poisoned through a returned or caller-held reference;
//  - bundles are keyed by canonical store identity (storeIdentityKey of the
//    snapshot's storeInfo, computed by the caller) + bead ID; stacks for
//    different stores never share entries, drafts, queries or context.
import { jsx, Fragment } from "react/jsx-runtime";

// ---- history stack ----------------------------------------------------------
const BUNDLE_FIELDS = [
  "storeKey", "beadId", "filter", "search", "tab", "pane",
  "selection", "focus", "scroll", "expanded", "draft",
];
const DEFAULTS = {
  beadId: () => null, filter: () => null, search: () => null, tab: () => null,
  pane: () => null, selection: () => null, focus: () => null, scroll: () => 0,
  expanded: () => [], draft: () => null,
};

function deepFreeze(v) {
  if (v && typeof v === "object" && !Object.isFrozen(v)) {
    Object.freeze(v);
    for (const k of Object.keys(v)) deepFreeze(v[k]);
  }
  return v;
}

function cloneBundle(raw) {
  const out = { storeKey: raw.storeKey };
  for (const f of BUNDLE_FIELDS) {
    if (f === "storeKey") continue;
    out[f] = raw[f] !== undefined ? raw[f] : DEFAULTS[f]();
  }
  if (out.expanded instanceof Set) out.expanded = [...out.expanded];
  return deepFreeze(JSON.parse(JSON.stringify(out)));
}

// createHistoryStack({ storeKey, capacity, initial }) — one instance per
// canonical store identity. push() normalizes + deep-clones the bundle,
// truncates the forward tail, returns the frozen stored entry.
export function createHistoryStack(opts = {}) {
  const storeKey = opts.storeKey != null ? String(opts.storeKey) : null;
  const capacity = opts.capacity ?? 50;
  if (!Number.isInteger(capacity) || capacity < 1) {
    throw new Error(`history capacity must be a positive integer, got ${opts.capacity}`);
  }
  const st = { entries: [], idx: -1 };

  const normalize = (raw) => {
    if (raw == null || typeof raw !== "object") {
      throw new Error("history bundle must be an object");
    }
    if (raw.storeKey != null && storeKey != null && raw.storeKey !== storeKey) {
      throw new Error(
        `history storeKey mismatch: stack=${storeKey} bundle=${raw.storeKey} — ` +
        "workspace switch must not leak history into another store",
      );
    }
    return cloneBundle({ ...raw, storeKey: raw.storeKey ?? storeKey });
  };

  st.entries = (opts.initial ?? []).map(normalize).slice(-capacity);
  st.idx = st.entries.length - 1;

  return {
    get storeKey() { return storeKey; },
    get index() { return st.idx; },
    entries() { return st.entries.slice(); },
    current() { return st.idx >= 0 ? st.entries[st.idx] : null; },
    canBack() { return st.idx > 0; },
    canForward() { return st.idx >= 0 && st.idx < st.entries.length - 1; },
    push(raw) {
      const entry = normalize(raw);
      st.entries = st.entries.slice(0, st.idx + 1); // drop forward tail
      st.entries.push(entry);
      if (st.entries.length > capacity) st.entries.splice(0, st.entries.length - capacity);
      st.idx = st.entries.length - 1;
      return entry;
    },
    back() {
      if (!this.canBack()) return null;
      st.idx -= 1;
      return st.entries[st.idx];
    },
    forward() {
      if (!this.canForward()) return null;
      st.idx += 1;
      return st.entries[st.idx];
    },
  };
}

// ---- breadcrumb: parent-field chain, re-resolved live ------------------------
function crumb(id) {
  return { id, label: id, clickable: true, href: `/bead/${encodeURIComponent(id)}` };
}

// trailFromSnapshot(snapshot, id) — re-walks the snapshot's model truth on
// every call (snapshot.nodes is built from parent FIELD chains only). The
// component derives the trail through this function each render, so a
// reparent in a fresh snapshot re-resolves the path.
export function trailFromSnapshot(snapshot, id) {
  const node = snapshot?.nodes?.get(id);
  if (!node) throw new Error(`trail: bead ${id} not in snapshot`);
  const chain = Array.isArray(node.path) ? node.path : [id];
  const out = chain.map(crumb);
  if (node.pathStatus === "cycle") {
    out.push({ id: null, label: "\u27f2 cycle", error: "parent cycle detected \u2014 chain truncated",
      clickable: false, href: null });
    return out;
  }
  const boundary = chain.map(id => snapshot.nodes.get(id))
    .find(n => n?.parent != null && !n.parentObserved);
  if (boundary) {
    const filt = snapshot?._reads?.issues?.filter;
    const reason = filt != null && typeof filt !== "function"
      ? "filtered-out of bounded read (absent \u2260 deleted)"
      : "parent absent from bounded read (absent \u2260 deleted)";
    out.push({ id: null, missing: boundary.parent, label: "\u2026", reason,
      clickable: false, href: null });
  }
  return out;
}

function CrumbDot({ crumb, onNavigate }) {
  if (crumb.clickable) {
    return jsx("button", {
      type: "button", id: `dot:${crumb.id}`, className: "crumb",
      onClick: () => onNavigate?.(crumb.id), children: crumb.label,
    }, `dot:${crumb.id}`);
  }
  if (crumb.error) {
    return jsx("span", { className: "crumb-error", children: `${crumb.label} ${crumb.error}` },
      `err:${crumb.error}`);
  }
  return jsx(Fragment, {
    children: [
      jsx("span", { className: "crumb-unknown", children: "\u2026" }, "unknown"),
      jsx("span", { className: "crumb-missing-parent", children: crumb.missing }, `mp:${crumb.missing}`),
      jsx("span", { className: "crumb-reason", children: crumb.reason }, `rs:${crumb.missing}`),
    ],
  }, `miss:${crumb.missing}`);
}

// Breadcrumb({ snapshot, id, onNavigate }) — pure component export (jsx()
// only). Rendered as its own element tree, visibly separate from the history
// panel; it never reads history state.
export function Breadcrumb({ snapshot, id, onNavigate }) {
  const trail = trailFromSnapshot(snapshot, id);
  const kids = [];
  trail.forEach((c, i) => {
    if (i > 0) kids.push(jsx("span", { className: "sep", children: "\u203A" }, `sep:${i}`));
    kids.push(jsx(CrumbDot, { crumb: c, onNavigate }, `crumb:${i}:${c.id ?? c.missing ?? c.error}`));
  });
  return jsx("nav", { "aria-label": "Breadcrumb", id: `breadcrumb:${id}`, children: kids },
    `breadcrumb:${id}`);
}

// ---- independent history presentation ----------------------------------------
function rowLabel(e) {
  const seg = [
    e.tab != null || e.pane != null
      ? `${e.tab ?? "\u2014"} \u203A ${e.pane ?? "\u2014"}` : null,
    e.beadId,
    e.focus != null ? `focus ${e.focus}` : null,
    e.search ? `\u2315 ${e.search}` : null,
    e.filter ? `\u2630 ${e.filter}` : null,
  ].filter(Boolean);
  return e.label ?? seg.join(" \u00B7 ");
}

// HistoryPanel({ entries, index, onRestore }) — its OWN widget, fed only the
// stack's entries + index; shares no state with the breadcrumb.
export function HistoryPanel({ entries, index, onRestore }) {
  const rows = entries.map((e, i) =>
    jsx("button", {
      type: "button", "aria-current": i === index ? "true" : undefined,
      id: `history-row-${i}`,
      className: i === index ? "history-row active" : "history-row",
      onClick: () => onRestore?.(e),
      children: rowLabel(e),
    }, `history-row-${i}`));
  return jsx("aside", { "aria-label": "History", id: "history-panel", children: rows },
    "history-panel");
}
