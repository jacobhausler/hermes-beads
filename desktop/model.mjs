// desktop/model.mjs — hbl-pnu.2.1 (H1): bounded navigation/state core for the
// beads workbench. Pure Node ESM: no I/O, no bd import, no UI. Later H leaves
// (tree UX, breadcrumb/history widget, blocker jump, search) consume this API.
//
// Laws honoured here (CONTRACTS-v3 C1, bd-expert):
//  - parentage comes from the `parent` FIELD only, never from ID-dot spelling;
//  - all reads are bounded; absence from a bounded/filtered snapshot is
//    UNKNOWN, never "deleted" (deleted needs an explicit tombstone signal);
//  - parent cycles terminate with an explicit marker, no infinite walk;
//  - selection (cursor) and keyboard focus (Enter-confirmed) are distinct;
//  - unknown statuses/types/edge types are preserved verbatim;
//  - no second readiness engine: blockers come from injected native reads.

export const SCHEMA_VERSION = 1;

export function storeIdentityKey(info) {
  const ws = info?.workspace ?? info?.workspace_path;
  const db = info?.db ?? info?.database ?? info?.database_path;
  if (!ws || !db) {
    throw new Error(
      `store identity needs workspace+db (bd info equivalent); got workspace=${ws ? "ok" : "missing"} db=${db ? "ok" : "missing"}`,
    );
  }
  return JSON.stringify({ v: 1, workspace: ws, db: db });
}

// rows: raw bd issue records (dependencies[] may carry dependency_type | type
// | depends_on_type; unknown values are preserved). opts.bound caps parent/
// ancestor walks; snapshot.opts.maxDepth caps breadcrumb depth.
// Each reads.* member may be a value or a zero-arg read-fn; a fn is invoked at
// most once, so callers can assert the native-query budget of a refresh.
export function buildSnapshot(readsIn, opts = {}) {
  const calls = [];
  const cache = new Map();
  const once = (v, key) => {
    if (typeof v !== "function") return v;
    if (cache.has(key)) return cache.get(key);
    calls.push(key);
    const r = v();
    cache.set(key, r);
    return r;
  };
  const reads = {};
  for (const k of ["issues", "ready", "blocked", "tombstones", "storeInfo"]) {
    Object.defineProperty(reads, k, { get: () => once(readsIn?.[k], k), enumerable: true });
  }
  reads._calls = calls;
  const bound = opts.bound ?? 200;
  const issues = clip(reads.issues, bound);
  const readyIds = reads.ready?.truncated ? null : idSet(reads.ready?.ids ?? reads.ready);
  const blockedIds = idSet(reads.blocked?.ids ?? reads.blocked);
  const tombstoneIds = idSet(reads.tombstones?.ids ?? reads.tombstones);

  const byId = new Map();
  for (const r of issues) if (r && r.id != null) byId.set(r.id, r);

  const nodes = new Map();
  for (const id of byId.keys()) nodes.set(id, makeNode(id));

  // children sets derived from parent FIELD — dotted-ID siblings get no edges.
  for (const [id, rec] of byId) {
    const n = nodes.get(id);
    const pid = rec.parent ?? null;
    n.parent = pid;
    n.parentObserved = pid != null ? byId.has(pid) : true;
    if (pid != null && byId.has(pid)) nodes.get(pid).childIds.push(id);
  }
  for (const n of nodes.values()) n.childIds.sort();

  const storeKey = reads?.storeInfo ? storeIdentityKey(reads.storeInfo) : null;
  const snap = {
    schema: SCHEMA_VERSION,
    storeKey,
    fetchedAt: opts.fetchedAt ?? null,
    ttlMs: opts.ttlMs ?? 60_000,
    stale: false,
    truncated: !!(reads?.issues?.truncated),
    bound,
    maxDepth: opts.maxDepth ?? 12,
    opts,
    _reads: reads ?? {},
    byId,
    nodes,
    readyIds,
    blockedIds,
    tombstoneIds,
  };
  for (const n of nodes.values()) decorate(snap, n);
  return snap;
}

function clip(box, bound) {
  const rows = Array.isArray(box) ? box : (box?.rows ?? box?.issues ?? []);
  return rows.slice(0, bound);
}
function idSet(box) {
  if (box == null) return null; // read not performed => unknown
  const arr = Array.isArray(box) ? box : box.ids;
  return new Set((arr ?? []).map(row => typeof row === 'object' && row !== null ? row.id : row));
}

function makeNode(id) {
  return {
    id, parent: null, parentObserved: true, childIds: [],
    typedBlockers: [], inheritedBlockers: [], cyclic: false,
    depth: 0, path: null, pathStatus: "ok",
    statusKnown: false, storedStatus: null, derivedBlocked: null, divergence: null,
  };
}

function activeBlockerRefs(rec, byId) {
  // dependency_type|type|depends_on_type; preserved verbatim; closed deps drop out.
  // `bd list --json` edge rows carry no status: fall back to the referenced
  // record in the same snapshot. Out-of-snapshot refs stay active (absent != closed).
  const out = [];
  for (const d of rec?.dependencies ?? []) {
    const t = d.dependency_type ?? d.type ?? d.depends_on_type ?? null;
    if (t !== "blocks") continue;
    const ref = depRef(d);
    if (ref == null) continue;
    const st = d.status ?? byId?.get(ref)?.status ?? null; // show embeds status; list does not
    if (typeof st === "string" && /^(closed|done)$/i.test(st)) continue;
    out.push({ id: ref, edgeType: t });
  }
  return out;
}
function depRef(d) { return d.depends_on_id ?? d.id ?? d.issue_id ?? null; }
function verbatimEdges(rec) {
  return (rec?.dependencies ?? []).map((d) => ({
    id: depRef(d),
    edgeType: d.dependency_type ?? d.type ?? d.depends_on_type ?? null, // verbatim, incl. unknown
  })).filter((e) => e.id != null);
}

function decorate(snap, node) {
  const rec = snap.byId.get(node.id);
  node.statusKnown = typeof rec?.status === "string";
  node.storedStatus = rec?.status ?? null; // unknown values preserved verbatim
  const ready = snap.readyIds ? snap.readyIds.has(node.id) : null;
  const blocked = snap.blockedIds ? snap.blockedIds.has(node.id) : null;
  // Positive native evidence only: ready-presence => ready, blocked-presence
  // => blocked. Absence from either list is UNKNOWN (a claimed/deferred or
  // out-of-filter record is absent without being blocked), never inferred.
  node.derivedBlocked = ready === true ? false : blocked === true ? true : null;
  if (node.derivedBlocked !== null && node.statusKnown) {
    const expectsBlocked = node.storedStatus === "blocked";
    if (expectsBlocked !== (node.derivedBlocked === true)) {
      node.divergence = {
        stored: node.storedStatus,
        derived: node.derivedBlocked === true ? "blocked" : "ready",
        warning: "stored status disagrees with native derived readiness",
      };
    }
  }
  node.typedBlockers = activeBlockerRefs(rec, snap.byId).map((b) => ({ ...b, inherited: false }));
  node.edges = verbatimEdges(rec);
  // inherited blockers: ancestor walk, cycle- and depth-bounded.
  const seen = new Set([node.id]);
  let cur = node.parent;
  let depth = 0;
  while (cur != null && depth < snap.maxDepth) {
    if (seen.has(cur)) { node.cyclic = true; node.pathStatus = "cycle"; break; }
    seen.add(cur);
    const anc = snap.byId.get(cur);
    for (const b of activeBlockerRefs(anc, snap.byId)) {
      node.inheritedBlockers.push({ ...b, inherited: true, source: cur, via: [...seen].slice(0, depth + 2) });
    }
    cur = anc?.parent ?? null;
    depth += 1;
  }
  if (cur != null && depth >= snap.maxDepth) { node.pathStatus = "depth-truncated"; }
  // breadcrumb chain + depth from parent FIELD only; visited-set makes the
  // walk safe against parent cycles and stops at the first repeat.
  const chain = [];
  const seenChain = new Set();
  let p = node;
  let d = 0;
  while (p && d <= snap.maxDepth && !seenChain.has(p.id)) {
    seenChain.add(p.id);
    chain.unshift(p.id);
    p = p.parent != null ? snap.byId.get(p.parent) : null;
    d += 1;
  }
  node.depth = chain.length - 1;
  node.path = chain;
  if (node.parent != null && !node.parentObserved && node.pathStatus === "ok") {
    node.pathStatus = "parent-missing"; // absent from bounded read != deleted
  }
}

// ---- absence vs deletion (C1: bounded absence is unknown) ----
export function absenceReason(snap, id) {
  // tombstone wins even if a stale row lingers: a deletion claim is proven,
  // presence-in-a-stale-read is not.
  if (snap.tombstoneIds?.has(id)) return { kind: "deleted", deleted: true, proof: "explicit tombstone" };
  if (snap.byId.has(id)) return null;
  if (snap.truncated) return { kind: "unknown-truncated", deleted: false };
  // Explicit query-filter metadata only; Array.prototype.filter is a method,
  // not a filter — an unfiltered plain-array read must not be mislabeled.
  const filt = snap._reads?.issues?.filter;
  if (filt != null && typeof filt !== "function") return { kind: "unknown-filtered", deleted: false };
  return { kind: "unknown-not-in-scope", deleted: false };
}

// ---- selection vs keyboard focus + history ----
export function createWorkbenchState(snapshot, initial = {}) {
  if (!snapshot) throw new Error("snapshot required");
  let exp = initial.expanded ?? new Set();
  if (exp.size === 0) for (const [id, n] of snapshot.nodes) if (n.childIds.length && !n.cyclic) exp.add(id);
  const st = {
    storeKey: snapshot.storeKey,
    selection: initial.selection ?? null,
    focus: initial.focus ?? null,
    pane: initial.pane ?? "list",
    expanded: exp,
    history: [],
    hIdx: -1,
    // Native search/show rows outside the bounded snapshot, kept only in
    // workbench navigation state. Parentage is supplied by resolved FIELD
    // paths; absent parentage remains an explicit boundary, not a root.
    revealed: new Map(),
  };
  const visible = () => {
    const out = [];
    // A row counts as reachable if some root's DESCENDANT CLOSURE covers it,
    // regardless of expansion: collapsing a parent hides its subtree, it never
    // re-emits those descendants as depth-0 tail rows. Only rows unreachable
    // from any root (missing-parent orphans, cycle members, revealed hits
    // with unknown parentage) get the honest single tail row.
    const reached = new Set();
    // revealed children hang under their resolved parent like real kids —
    // transient workbench state, never snapshot nodes (no invented rows).
    const revealedKids = (id) => {
      const out2 = [];
      for (const [rid, r] of st.revealed) if (r.parent === id) out2.push(rid);
      return out2;
    };
    const mark = (id) => {
      if (reached.has(id)) return;
      reached.add(id);
      const n = snapshot.nodes.get(id);
      if (n) for (const c of n.childIds) mark(c);
      for (const c of revealedKids(id)) mark(c);
    };
    const walk = (id, depth) => {
      if (walked.has(id)) return;
      if (!snapshot.nodes.has(id) && !st.revealed.has(id)) return; // unknown id
      walked.add(id);
      const r = st.revealed.get(id);
      out.push({ id, depth, parentId: r?.parent ?? snapshot.nodes.get(id)?.parent ?? null,
        boundary: !!r && r.parent == null && !r.rootKnown });
      const n = snapshot.nodes.get(id);
      if (st.expanded.has(id) && !(n?.cyclic)) {
        const kids = n ? [...n.childIds, ...revealedKids(id)] : revealedKids(id);
        for (const c of kids) walk(c, depth + 1);
      }
    };
    const walked = new Set();
    const roots = [...snapshot.nodes].filter(([, n]) => n.parent === null).map(([id]) => id);
    const rank = new Map([...(snapshot.readyIds ?? [])].map((id, i) => [id, i]));
    roots.sort((a, b) => (rank.get(a) ?? Infinity) - (rank.get(b) ?? Infinity));
    for (const id of roots) mark(id);
    for (const id of roots) walk(id, 0);
    // Snapshot orphans/cycles are honest boundary rows. Walk their children
    // once, so a revealed child of an orphan is not silently lost.
    for (const id of snapshot.nodes.keys()) if (!reached.has(id)) walk(id, 0);
    for (const [id, r] of st.revealed) {
      const parentKnown = r.parent != null && (snapshot.nodes.has(r.parent) || st.revealed.has(r.parent));
      if (!parentKnown) walk(id, 0);
    }
    return out;
  };
  const idxOf = (rows, id) => rows.findIndex((r) => r.id === id);
  const bundle = () => ({
    storeKey: st.storeKey, pane: st.pane, selection: st.selection,
    focus: st.focus, expanded: [...st.expanded].sort(),
    revealed: [...st.revealed].map(([id, r]) => [id, { ...r }]),
  });
  const restore = (b) => {
    if (b.storeKey !== st.storeKey) throw new Error("workbench storeKey mismatch");
    st.selection = b.selection; st.focus = b.focus; st.pane = b.pane;
    st.expanded = new Set(b.expanded);
    st.revealed = new Map((b.revealed ?? []).map(([id, r]) => [id, { ...r }]));
  };
  const push = () => {
    st.history = st.history.slice(0, st.hIdx + 1);
    st.history.push(bundle());
    st.hIdx = st.history.length - 1;
  };

  return {
    get selection() { return st.selection; },
    get focus() { return st.focus; },
    get pane() { return st.pane; },
    get history() { return st.history; },
    // THE expansion truth: the live set behind visibleRows/toggleExpanded/
    // jump/history-restore. The tree renders this set; it keeps no copy.
    get expanded() { return st.expanded; },
    get revealed() { return st.revealed; },
    // Save the full pre-navigation bundle before adding temporary search rows.
    // Back/Forward then restore the same model expansion + reveal state.
    save() { if (JSON.stringify(st.history[st.hIdx]) !== JSON.stringify(bundle())) push(); },
    visibleRows: visible,
    arrow(dir) {
      const rows = visible();
      if (!rows.length) return;
      const i = idxOf(rows, st.selection);
      const j = i < 0 ? 0 : Math.max(0, Math.min(rows.length - 1, i + dir));
      st.selection = rows[j].id;
    },
    toggleExpanded(id) {
      const n = snapshot.nodes.get(id);
      if (!n || n.cyclic) return;
      if (st.expanded.has(id)) st.expanded.delete(id); else st.expanded.add(id);
    },
    // Only parent-FIELD evidence supplied by the caller may attach a row.
    // Metadata comes from the native search/show result, never snapshot edits.
    reveal(id, parent, row = {}) {
      if (id == null || snapshot.nodes.has(id) || parent === id) return false;
      st.revealed.set(id, { parent: parent ?? null, rootKnown: row.rootKnown === true,
        title: typeof row.title === "string" ? row.title : id,
        status: typeof row.status === "string" ? row.status : null });
      if (parent != null && (snapshot.nodes.has(parent) || st.revealed.has(parent))) st.expanded.add(parent);
      return true;
    },
    unreveal(id) {
      return st.revealed.delete(id);
    },
    enter() { if (st.selection != null) { st.focus = st.selection; push(); } },
    jump(id, pane) {
      if (pane && pane !== st.pane) { st.pane = pane; }
      // jump is the reveal primitive (search-lane Enter path): expand the
      // target's ancestor chain so the row is rendered, cycle-bounded.
      const n = snapshot.nodes.get(id);
      if (n) {
        const seen = new Set([id]);
        let p = n.parent;
        let d = 0;
        while (p != null && d < snapshot.maxDepth) {
          if (seen.has(p)) break;
          seen.add(p);
          const pn = snapshot.nodes.get(p);
          if (pn && !pn.cyclic) st.expanded.add(p);
          p = pn?.parent ?? null;
          d += 1;
        }
      }
      st.selection = id; st.focus = id; push();
    },
    back() { if (st.hIdx > 0) { st.hIdx -= 1; restore(st.history[st.hIdx]); } },
    forward() { if (st.hIdx < st.history.length - 1) { st.hIdx += 1; restore(st.history[st.hIdx]); } },
  };
}

// ---- freshness ----
export function isStale(snap, now) {
  if (snap.stale) return true;
  if (snap.fetchedAt == null || now == null) return false;
  return now - snap.fetchedAt > snap.ttlMs;
}
export function invalidateOnMutation(snap) { snap.stale = true; return snap; }
