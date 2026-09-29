// desktop/search.mjs — hbl-pnu.2.6: search reveals the resolved ancestor path
// and lands IN the tree (search is a navigation entry, not a parallel list world).
//
// Truth it stands on (hci.md FACTs 4–6): `bd search` hits are flat and
// hierarchy-blind — ids+title+status, no path. ID dots are creation-time
// provenance, NOT structure (a reparented dotted-ID-less bead keeps its true
// parent FIELD). So paths are resolved HERE from the H1 snapshot's
// parent-FIELD chain (model.mjs buildSnapshot, consumed UNCHANGED via
// node.path / node.pathStatus / snapshot.byId), with zero per-hit native
// calls on the warm path (owner ruling on N+1).
//
// Owner contract honoured:
//  - one direct native query with a bounded result count, injected as
//    searchRead(query, bound) — the read facade owns the fixed argv; this
//    module never builds bd commands;
//  - a hit outside the current Ready/Mine filtered snapshot is still revealed
//    (absence from a bounded/filtered read is UNKNOWN, never "deleted");
//  - missing ancestor / truncation / fallback exhaustion are explicit visible
//    states — never an unbounded `bd show` loop;
//  - Enter is a navigation entry: workbench.jump (selection+focus+push) plus
//    a history.mjs bundle carrying the query and the current filter, so Back
//    restores the full pre-search state including the search text.
//
// Purity: no I/O, no bd import, no second readiness engine; sole import is
// the jsx-runtime the app loader maps (same pattern as history.mjs).

import { jsx } from "react/jsx-runtime";

export const DEFAULT_SEARCH_LIMIT = 25;
export const HARD_SEARCH_LIMIT = 100; // explicit ceiling: larger requests clamp, visibly
export const MAX_FALLBACK_CALLS = 3;  // bounded ancestor fallback, never a show loop
const PER_LOOKUP_LIMIT = 10;

function missingMarker(missing, reason) {
  return { id: null, label: "\u2026", state: "missing", missing, reason, clickable: false };
}
function cycleMarker() {
  return { id: null, label: "\u27f2 cycle", state: "cycle",
    error: "parent cycle detected \u2014 chain truncated", clickable: false };
}

// parentage a bounded row DECLARES: explicit null = proven root (bd show of a
// root omits the field; facades normalize it to null); ABSENT = no parentage
// info at all (raw `bd search` rows — probed: never carry parent, even
// --long) — never silently crowned as a root.
function rowParent(row) {
  if (!row || typeof row !== "object") return undefined;
  return "parent" in row ? (row.parent ?? null) : undefined;
}

// ---- path resolution: parent FIELD chain over the snapshot -------------------
// In-snapshot hits reuse model.mjs's node.path (already parent-FIELD-only,
// cycle-terminated, missing-parent-aware) — no re-walk, no native call. The
// bounded fallback `lookup(id, queryText)` is consulted ONLY for ancestors
// (or the hit itself) absent from the snapshot. lookup is refused when asked
// with the query text verbatim: ID-spelling inference is an exclusion, so a
// query that looks like an ID earns no free ancestry.
export function resolveHitPath(snapshot, hit, opts = {}) {
  const id = typeof hit === "string" ? hit : hit?.id;
  if (!id) throw new Error("resolveHitPath: hit needs an id");
  const maxDepth = opts.maxDepth ?? snapshot?.maxDepth ?? 12;
  const lookup = typeof opts.lookup === "function" ? opts.lookup : null;
  const queryText = typeof opts.query === "string" ? opts.query.trim() : null;
  const unavailable = typeof opts.lookupUnavailable === "function"
    ? opts.lookupUnavailable
    : () => !!opts.lookupUnavailable;
  const labelIn = (nid, fallbackTitle) =>
    snapshot?.byId?.get(nid)?.title ?? fallbackTitle ?? nid;

  const chain = []; // [{ id, label, state }] root → hit
  const tail = [];  // trailing markers (missing/cycle)
  const flags = [];
  let status = "resolved";
  let missing = null;
  let fallbackUsed = false;
  const seen = new Set([id]);

  let parentId = null; // non-null only when a boundary needs upward continuation
  let needLookupForHitParent = false;

  const node = snapshot?.nodes?.get(id);
  if (node) {
    const ids = Array.isArray(node.path) && node.path.length ? node.path : [id];
    for (const pid of ids) chain.push({ id: pid, label: labelIn(pid), state: "snapshot" });
    if (node.pathStatus === "cycle") {
      status = "cycle";
      tail.push(cycleMarker());
    } else {
      // The model's chain ends either at a proven root (head.parent === null)
      // or at a boundary: head's parent fell outside the bounded read, or the
      // chain was depth-truncated. Only then does the walk continue upward —
      // the warm path makes ZERO further decisions and ZERO native calls.
      const head = snapshot.nodes.get(chain[0].id);
      if (node.pathStatus === "depth-truncated") {
        status = "depth-truncated";
        flags.push("depth-truncated");
      }
      if (head?.parent != null && (!head.parentObserved || node.pathStatus === "depth-truncated")) {
        parentId = head.parent;
      }
    }
  } else {
    // out-of-filter hit: the flat native row (or its id) is all we know of it
    flags.push("hit-outside-snapshot");
    chain.push({ id, label: labelIn(id, hit?.title), state: "unknown-hit" });
    status = "unknown-hit";
    needLookupForHitParent = true;
  }

  // continue upward from a boundary (missing/deleted parent, depth cap, or an
  // out-of-snapshot hit) until root proven / missing / cycle / depth bound
    while (parentId !== null || needLookupForHitParent) {
    if (chain.length - 1 > maxDepth) {
      if (status !== "unknown-hit") status = "depth-truncated";
      flags.push("depth-truncated");
      break;
    }
    if (needLookupForHitParent) {
      needLookupForHitParent = false;
      const row = lookup ? lookup(id, queryText) : null; // learn the hit's own parent
      if (!row || typeof row !== "object") {
        tail.push(missingMarker(null, lookup == null
          ? "parent unknown \u2014 hit outside the bounded snapshot (absent \u2260 deleted)"
          : (unavailable()
            ? "ancestor unavailable \u2014 bounded fallback refused/exhausted (no per-hit show loop)"
            : "parent unknown \u2014 hit outside the bounded snapshot (absent \u2260 deleted)")));
        flags.push("ancestor-missing");
        break;
      }
      fallbackUsed = true;
      chain[0] = { id, label: row.title ?? id, state: "fallback" };
      const rp = rowParent(row);
      if (rp === undefined) {
        // the bounded row carries NO parentage at all — honest boundary,
        // never silently crowned as a root
        tail.push(missingMarker(null, "fallback row carries no parent field — ancestry unavailable"));
        flags.push("ancestor-missing");
        break;
      }
      parentId = rp;
      if (parentId == null) status = "resolved";
      continue;
    }
    const want = parentId;
    if (seen.has(want)) {
      status = "cycle";
      tail.length = 0;
      tail.push(cycleMarker());
      break;
    }
    seen.add(want);
    const anc = snapshot?.nodes?.get(want);
    if (anc) {
      // reuse the model's chain above this ancestor — model truth, zero calls
      const upper = Array.isArray(anc.path) && anc.path.length ? anc.path : [want];
      const stopAt = upper.indexOf(chain[0].id); // drop what's already in chain
      const upperOnly = stopAt >= 0 ? upper.slice(0, stopAt) : upper;
      // prepend the block ROOT-FIRST: forward iteration unshifts the whole
      // chain one entry at a time and REVERSES it (root→hit becomes hit-side
      // first); reverse iteration lands upperOnly in root→hit order at the
      // head of the chain.
      for (let k = upperOnly.length - 1; k >= 0; k--) {
        const pid = upperOnly[k];
        chain.unshift({ id: pid, label: labelIn(pid), state: "snapshot" });
      }
      if (anc.pathStatus === "cycle") { status = "cycle"; tail.push(cycleMarker()); break; }
      const top = snapshot.nodes.get(upper[0]);
      if (top?.parent != null && !top.parentObserved) { parentId = top.parent; continue; }
      parentId = top?.parent ?? null;
      if (parentId == null && status === "unknown-hit") status = "resolved";
      continue;
    }
    if (!lookup) {
      status = "parent-missing";
      missing = want;
      tail.push(missingMarker(want, "parent absent from bounded read (absent \u2260 deleted)"));
      break;
    }
    const row = lookup(want, queryText);
    if (!row || typeof row !== "object") {
      status = "parent-missing";
      missing = want;
      tail.push(missingMarker(want, unavailable()
        ? "ancestor unavailable \u2014 bounded fallback refused/exhausted (no per-hit show loop)"
        : "parent absent from bounded read (absent \u2260 deleted)"));
      break;
    }
    fallbackUsed = true;
    chain.unshift({ id: want, label: row.title ?? want, state: "fallback" });
    const rp = rowParent(row);
    if (rp === undefined) {
      // row carries NO parentage: an honest boundary, never a crowned root
      status = "parent-missing";
      missing = null;
      tail.push(missingMarker(want, "fallback row carries no parent field — ancestry unavailable above it"));
      flags.push("ancestor-missing");
      break;
    }
    parentId = rp;
    if (parentId == null && status === "unknown-hit") status = "resolved";
  }

  if (fallbackUsed) flags.push("path-from-bounded-fallback");
  if (missing != null || tail.some((t) => t.state === "missing")) flags.push("ancestor-missing");
  if (unavailable && tail.some((t) => t.state === "missing")) flags.push("ancestor-unavailable");
  return { id, path: [...chain, ...tail], pathStatus: status, missing, flags };
}

// ---- search: one bounded native query + snapshot enrichment ------------------
// searchRead(query, limit) is the injected read facade (read_model.py shape):
// exactly one fixed-argv `bd search` per call, native rows returned verbatim.
// Every call is counted; the ancestor fallback reuses the same facade under an
// explicit budget — callers assert the total via res.nativeCalls.
//
// showRead(id) (optional) is the AUTHORITATIVE single-row fallback: `bd show`
// carries the real parent FIELD (probed: `bd search` rows never do, even
// --long), and a ROOT's show row OMITS parent — which is proven rootage, not
// ignorance. The facade side normalizes that to an explicit parent:null;
// a search row with no parent key stays an unknown boundary (rowParent →
// undefined), never a crowned root. Both channels share the ONE call budget.
export function searchIssues({ snapshot, query, searchRead, showRead, limit, maxDepth,
  fallback = true, fallbackBudget = MAX_FALLBACK_CALLS } = {}) {
  if (!snapshot || !snapshot.nodes) throw new Error("searchIssues requires an H1 snapshot (model.mjs buildSnapshot)");
  const q = typeof query === "string" ? query.trim() : "";
  if (!q) throw new Error("searchIssues requires a non-empty query");
  if (typeof searchRead !== "function") {
    throw new Error("searchIssues requires an injected searchRead(query, bound) — the read facade owns the native argv");
  }
  const requested = limit ?? DEFAULT_SEARCH_LIMIT;
  if (!Number.isInteger(requested) || requested <= 0) {
    throw new Error(`limit must be a positive int (unbounded search is refused); got ${limit}`);
  }
  const clamped = requested > HARD_SEARCH_LIMIT;
  const bound = Math.min(requested, HARD_SEARCH_LIMIT);

  // ONE native query. Request bound+1 so truncation is PROVEN, not guessed:
  // native `bd search` drops matches beyond --limit status-blind, so a
  // response that fills bound+1 means rows were dropped (banner, not silence).
  const rows = searchRead(q, bound + 1) ?? []; // query failure PROPAGATES (visible, never faked empty)
  if (!Array.isArray(rows)) throw new Error("searchRead must return the native row array verbatim");
  const rawCount = rows.length;
  const truncated = rawCount > bound || rows.truncated === true;
  const page = rows.slice(0, bound);

  const cache = new Map();
  const fallbackState = { enabled: !!fallback, calls: 0, budget: fallback ? fallbackBudget : 0,
    error: null, exhausted: false, showCalls: 0 };
  const lookup = fallback
    ? (id, queryText) => {
        if (id == null) return null;
        if (queryText != null && id === queryText) return null; // exclusion: no ID-spelling inference
        if (cache.has(id)) return cache.get(id);
        if (fallbackState.calls >= fallbackState.budget) { fallbackState.exhausted = true; return null; }
        // authoritative channel first: `bd show` carries the real parent FIELD
        // (search rows never do — probed FACT). A facade-normalized parent:null
        // from a root's show row proves rootage; the show channel is only
        // tried when a row is needed, so the warm path stays at zero calls.
        if (typeof showRead === "function") {
          fallbackState.calls += 1;
          let shown = null;
          try { shown = showRead(id); } catch (err) { fallbackState.error = String(err?.message ?? err); }
          if (shown && typeof shown === "object") {
            fallbackState.showCalls += 1;
            cache.set(id, shown);
            return shown;
          }
          // show miss (no row / deleted id): fall through to the search channel
          // only while budget remains — still one counted attempt per chain hop.
          if (fallbackState.calls >= fallbackState.budget) { fallbackState.exhausted = true; return null; }
        }
        fallbackState.calls += 1;
        let got;
        try {
          got = searchRead(id, Math.min(bound, PER_LOOKUP_LIMIT)) ?? [];
        } catch (err) {
          fallbackState.error = String(err?.message ?? err);
          return null;
        }
        for (const r of Array.isArray(got) ? got : []) {
          const rid = typeof r === "string" ? r : r?.id;
          if (rid != null && !cache.has(rid)) cache.set(rid, typeof r === "string" ? { id: r } : r);
        }
        const row = cache.get(id) ?? null;
        return row;
      }
    : null;

  const hits = [];
  for (const raw of page) {
    const row = typeof raw === "string" ? { id: raw } : raw;
    if (!row || row.id == null) continue;
    const enriched = resolveHitPath(snapshot, row, {
      lookup, query: q, maxDepth,
      lookupUnavailable: () => fallbackState.exhausted || fallbackState.error != null,
    });
    enriched.row = { id: row.id, title: row.title, status: row.status, issue_type: row.issue_type };
    hits.push(enriched);
  }
  return {
    storeKey: snapshot.storeKey ?? null,
    query: q,
    requested,
    bound,
    clamped,
    rawCount,
    truncated,
    hits,
    fallback: { ...fallbackState },
    // total native calls this query made: 1 query + bounded fallback lookups.
    nativeCalls: 1 + fallbackState.calls,
  };
}

// ---- the search panel (pure component; no separate world) -------------------
function pathLabel(hit) {
  return hit.path.map((p) => p.label ?? p.id ?? "?").join(" \u203A ");
}

// SearchPanel({ results, cursor, onActivate }) — one row per enriched hit;
// truncation and unavailable ancestry are explicit visible banners (owner:
// visible state, not a show loop). Selection (cursor) ≠ focus: activation is
// delegated to onActivate → enterSearchHit.
export function SearchPanel({ results, cursor = -1, onActivate }) {
  if (!results || !Array.isArray(results.hits)) {
    throw new Error("SearchPanel requires searchIssues() results");
  }
  const kids = [];
  if (results.truncated || results.clamped) {
    kids.push(jsx("p", {
      id: "search-truncated", role: "status", className: "search-truncated",
      children: `showing ${results.hits.length} of ${results.rawCount}+ hits \u2014 bounded at ${results.bound}, extra rows dropped status-blind (refine the query)`,
    }, "search-truncated"));
  }
  results.hits.forEach((h, i) => {
    const inner = [
      jsx("span", { className: "search-hit-id", children: h.id }, `id:${h.id}`),
      jsx("span", { className: "search-hit-title", children: h.row?.title ?? "" }, `t:${h.id}`),
      jsx("span", { className: "search-hit-path", "aria-label": `path: ${pathLabel(h)}`,
        children: pathLabel(h) }, `p:${h.id}`),
    ];
    if (h.pathStatus === "parent-missing" || h.pathStatus === "unknown-hit"
        || h.flags.includes("ancestor-unavailable")) {
      inner.push(jsx("span", {
        className: "search-path-unavailable", role: "status",
        children: `\u26a0 ${h.missing ?? h.id} unavailable \u2014 path incomplete (bounded reads; absent \u2260 deleted, no per-hit show loop)`,
      }, `unavail:${h.id}`));
    }
    if (h.pathStatus === "depth-truncated") {
      inner.push(jsx("span", { className: "search-path-truncated", role: "status",
        children: "\u26a0 path depth-truncated" }, `trunc:${h.id}`));
    }
    if (h.pathStatus === "cycle") {
      inner.push(jsx("span", { className: "search-path-cycle", role: "alert",
        children: "\u27f2 parent cycle \u2014 chain truncated" }, `cyc:${h.id}`));
    }
    kids.push(jsx("div", {
      role: "option", id: `search-hit-${i}`, "data-path-status": h.pathStatus,
      "aria-selected": i === cursor ? "true" : "false",
      // hbl-pnu.4.6: roving tabIndex + Enter/Space activate through the
      // SAME entry as click (onActivate -> enterSearchHit). Keyboard hits
      // are activatable, not click-only.
      tabIndex: i === cursor ? 0 : -1,
      onClick: () => onActivate?.(h, i),
      onKeyDown: (ev) => {
        if (ev.key === "Enter" || ev.key === " ") {
          ev.preventDefault();
          ev.stopPropagation();
          onActivate?.(h, i);
        }
      },
      children: inner,
    }, `search-hit-${i}`));
  });
  return jsx("section", { id: "search-panel", role: "listbox", "aria-label": "Search",
    children: kids }, "search-panel");
}

// ---- Enter: a navigation entry into the tree, not a parallel world ----------
// Enter records the model's pre-navigation bundle before adding native rows.
// The external history stack retains filter/query/scroll; model Back restores
// selection/focus/pane/expansion/revealed rows exactly, with no undo guesses.
export function enterSearchHit({ results, index, snapshot, workbench, history,
  pane = "tree" } = {}) {
  if (!results || !Array.isArray(results.hits)) throw new Error("enterSearchHit requires searchIssues() results");
  const hit = results.hits[index];
  if (!hit) throw new Error(`enterSearchHit: no hit at index ${index}`);
  if (!workbench || typeof workbench.jump !== "function"
      || typeof workbench.visibleRows !== "function"
      || typeof workbench.reveal !== "function" || typeof workbench.save !== "function"
      || typeof workbench.back !== "function") {
    throw new Error("enterSearchHit requires the model.mjs workbench — search never owns its own world");
  }
  if (!history || typeof history.push !== "function" || typeof history.back !== "function") {
    throw new Error("enterSearchHit requires a history.mjs stack — Enter must be a history entry");
  }
  const prev = history.current();
  workbench.save();
  const chain = hit.path.filter(p => p.id != null);
  for (let i = 0; i < chain.length; i++) {
    const row = chain[i];
    if (!snapshot.nodes.has(row.id)) {
      workbench.reveal(row.id, i > 0 ? chain[i - 1].id : null, {
        ...(row.id === hit.id ? hit.row : {}),
        title: row.id === hit.id ? hit.row?.title ?? row.label : row.label,
        rootKnown: i === 0 && hit.pathStatus === "resolved",
      });
    }
    if (i < chain.length - 1 && !snapshot.nodes.get(row.id)?.cyclic) {
      workbench.expanded.add(row.id);
    }
  }
  workbench.jump(hit.id, pane);

  const cur = workbench.history?.[workbench.history.length - 1] ?? {};
  const entry = history.push({
    storeKey: snapshot?.storeKey ?? null,
    beadId: hit.id,
    focus: hit.id,
    selection: hit.id,
    pane,
    tab: prev?.tab ?? null,
    filter: prev?.filter ?? null,   // search does NOT erase the Ready/Mine filter
    search: results.query,          // the query itself lives in the bundle
    scroll: prev?.scroll ?? 0,
    expanded: cur.expanded ?? [],
    draft: prev?.draft ?? null,
  });

  return {
    hit,
    entry,
    restore() {
      const b = history.back();
      workbench.back();
      return b;
    },
  };
}
