// desktop/compare.mjs — hbl-pnu.2.7: read-only SPLIT compare + structural
// churn refresh-diff (moved lines, confirmed tombstones).
//
// Laws honoured (CONTRACTS-v3 C1, bd-expert, bead OWNER CONTRACT):
//  - comparison is READ-ONLY: no second editor here (drafts belong to the
//    drafts lane; this module only never disturbs the other side);
//  - NO event bus: the CLI emits nothing to viewers (FACT F10). Churn becomes
//    visible only by diffing a newly materialized snapshot against the LAST
//    MATERIALIZED one;
//  - absence from a bounded snapshot is NEVER proof of deletion. A tombstone
//    row exists only after native confirmation: `bd show <id>` not-found AND
//    `bd history <id>` still records the issue (history survives deletion —
//    probed fact, bd 1.3.0);
//  - permission failure, backend outage and truncation are tracked SEPARATELY
//    from absence: none of them may produce a deletion claim;
//  - reparent renders an explicit moved line (parent FIELD), never a ghost
//    row; focused id whose chain moved keeps focus + re-resolved breadcrumb
//    banner — focus never drops to root;
//  - shared model/tree/history/record are untouched: this consumes snapshots.
import { jsx, Fragment } from "react/jsx-runtime";

export const SCHEMA_VERSION = 1;

// ---- split panes: independent per-side view state, read-only comparison ----
// Each side owns selection/focus/originPane/draft slots. No operation on one
// side can reach into the other; that isolation IS the S8 guarantee.
export function createSplitPanes({ left, right }) {
  const side = (name) => {
    const s = name === "left" ? left : name === "right" ? right : null;
    if (!s) throw new Error(`unknown side: ${name}`);
    return s;
  };
  const st = {};
  for (const name of ["left", "right"]) {
    const s = side(name);
    st[name] = {
      snapshot: s.snapshot,
      selection: s.selection ?? null,
      focus: s.focusable?.focus ?? null,
      originPane: s.focusable?.originPane ?? "list",
      draft: s.draft ?? s.focusable?.draft ?? null,
      focusGone: false,
      breadcrumb: s.breadcrumb ?? null,
    };
  }
  const get = (name) => {
    if (name !== "left" && name !== "right") throw new Error(`unknown side: ${name}`);
    return st[name];
  };
  const rowIds = (s) => [...s.snapshot.nodes.keys()];

  return {
    snapshot: (name) => get(name).snapshot,
    storeKey: (name) => get(name).snapshot.storeKey,
    selection: (name) => get(name).selection,
    focus: (name) => get(name).focus,
    originPane: (name) => get(name).originPane,
    focusGone: (name) => get(name).focusGone,
    draft: (name) => get(name).draft,
    side: (name) => ({ ...get(name) }),
    select(name, id) {
      const s = get(name);
      if (s.snapshot.byId.has(id)) s.selection = id; // foreign id: no-op
    },
    arrow(name, dir) {
      const s = get(name);
      const ids = rowIds(s);
      if (!ids.length) return;
      const i = ids.indexOf(s.selection);
      const j = i < 0 ? 0 : Math.max(0, Math.min(ids.length - 1, i + dir));
      s.selection = ids[j];
    },
    setDraft(name, draft) { get(name).draft = draft; },
    setSide(name, next) {
      // re-parents THIS side's view only; the other side is never touched.
      const s = get(name);
      s.snapshot = next.snapshot;
      s.selection = null;
      s.focus = next.focusable?.focus ?? null;
      s.originPane = next.focusable?.originPane ?? "list";
      s.draft = next.draft ?? next.focusable?.draft ?? null;
      if (next.breadcrumb) s.breadcrumb = next.breadcrumb;
    },
    // applyRefresh is the churn seam: a newly materialized snapshot (plus its
    // diff against the previous one) lands WITHOUT disturbing focus, origin
    // pane or draft. Confirmed tombstones may mark the focused id gone.
    applyRefresh(name, { snapshot, diff = null, confirmed = null } = {}) {
      const s = get(name);
      if (snapshot) s.snapshot = snapshot;
      s.focusGone = !!(confirmed?.tombstones ?? []).some((t) => t.id === s.focus);
      return { diff, confirmed };
    },
  };
}

// ---- refresh-diff against the LAST MATERIALIZED snapshot ------------------
// readError convention: buildSnapshot(reads, { readError: {kind, message} })
// where kind ∈ "permission" | "outage". A read that never completed is not a
// snapshot with holes in it — all absence claims are suppressed.
function sideHealth(snap) {
  if (!snap) return "missing";
  const err = snap.opts?.readError ?? snap._readError;
  if (err?.kind === "permission") return "permission-denied";
  if (err?.kind === "outage") return "outage";
  if (snap.truncated) return "truncated";
  return "ok";
}
const suppressAbsence = (snap) => sideHealth(snap) !== "ok";

export function diffSnapshots(before, after) {
  if (!before || !after) throw new Error("diff needs both materialized snapshots");
  const beforeIds = [...before.byId.keys()];
  const afterIds = new Set(after.byId.keys());
  const moved = [];
  const absent = [];
  const added = [];
  for (const id of beforeIds) {
    if (!afterIds.has(id)) {
      if (!suppressAbsence(after)) {
        absent.push({ id, confirmed: false,
          proof: "absent from bounded snapshot — not proof of deletion" });
      }
      continue;
    }
    const b = before.nodes.get(id).parent;
    const a = after.nodes.get(id).parent;
    if (b !== a) moved.push({ id, from: b ?? null, to: a ?? null });
  }
  for (const id of after.byId.keys()) if (!before.byId.has(id)) added.push({ id });

  const rows = [
    ...moved.map((m) => ({ id: m.id, kind: "moved", from: m.from, to: m.to })),
    ...added.map((a) => ({ id: a.id, kind: "added" })),
    ...absent.map((a) => ({ id: a.id, kind: "absent", confirmed: false })),
  ];

  const d = {
    schema: SCHEMA_VERSION,
    moved, added, absent, rows,
    // diff alone NEVER tombstones: only confirmDeletions (native show/history) may.
    tombstones: [],
    deletionsUnconfirmed: absent,
    movedLine: (id) => {
      const m = moved.find((x) => x.id === id);
      return m ? `moved: ${id} parent ${m.from ?? "\u2014"} \u2192 ${m.to ?? "\u2014"}` : null;
    },
    sideHealth: (which) => sideHealth(which === "before" ? before : after),
    absentNote: () =>
      after.truncated
        ? "snapshot truncated: absence suppressed — not proof of anything"
        : sideHealth(after) !== "ok"
          ? `side read ${sideHealth(after)}: absence suppressed — not proof of anything`
          : "bounded absence is not proof of deletion",
  };
  return d;
}

// ---- native tombstone confirmation ------------------------------------------
// reads.show(id) / reads.history(id) return the RAW parsed `bd show --json` /
// `bd history --json` results (arrays of records), an error object
// ({error:"…"} — what bd emits for an unknown id), null, or throw.
// Fail-closed everywhere: any shape we cannot interpret keeps the id in
// stillUnconfirmed.
const notFound = (r) =>
  (Array.isArray(r) && r.length === 0) ||
  (!!r && !Array.isArray(r) && typeof r.error === "string");

export function confirmDeletions(diff, { reads }) {
  const tombstones = [];
  const resurrected = [];
  const stillUnconfirmed = [];
  for (const a of diff.deletionsUnconfirmed) {
    const id = a.id;
    let verdict = "unconfirmed";
    let show;
    try { show = reads.show(id); } catch { verdict = "unconfirmed"; }
    if (show !== undefined) {
      if (Array.isArray(show) && show.some((r) => r && r.id === id)) verdict = "visible";
      else if (notFound(show)) {
        let hist = null;
        try { hist = reads.history(id); } catch { hist = null; }
        const hasEntry = Array.isArray(hist) &&
          hist.some((e) => e && (e.Issue?.id === id || e.id === id));
        verdict = hasEntry ? "deleted" : "unconfirmed";
      }
    }
    if (verdict === "visible") resurrected.push({ id });
    else if (verdict === "deleted") {
      tombstones.push({ id, citation: `bd history ${id}`, history_entry: true });
    } else stillUnconfirmed.push({ id });
  }
  return {
    tombstones, resurrected, stillUnconfirmed,
    rows: [...diff.rows, ...tombstones.map((t) => ({ id: t.id, kind: "tombstone" }))],
  };
}

// ---- presentation ----------------------------------------------------------
// SplitCompare({ panes, side, diff, confirmed, breadcrumb }) — pure jsx, the
// read-only comparison view for ONE side: moved lines, banner when the
// focused id's parent chain moved (breadcrumb re-resolved against the CURRENT
// snapshot), tombstone rows with a working `bd history <id>` link, and the
// honest caveat next to every unconfirmed absence.
export function SplitCompare({ panes, side, diff, confirmed = null, breadcrumb = undefined }) {
  const s = panes.side(side);
  const bc = breadcrumb ?? s.breadcrumb;
  const kids = [];

  for (const m of diff?.moved ?? []) {
    kids.push(jsx("div", { className: "diff-move", "data-kind": "moved",
      children: diff.movedLine(m.id) }, `mv:${m.id}`));
  }
  // the focused row's parent chain moved if it, OR any ancestor in the CURRENT
  // snapshot's chain, was reparented (a descendant's own parent field is unchanged).
  const curPath = s.snapshot.nodes.get(s.focus)?.path ?? null;
  if (s.focus != null && (diff?.moved ?? []).some((m) => m.id === s.focus || curPath?.includes(m.id))) {
    kids.push(jsx("div", { role: "alert", className: "churn-banner",
      children: `banner: focused ${s.focus} moved — breadcrumb re-resolved: ${bc ? bc(s.focus) : (curPath ?? []).join(" / ")}` },
      `banner:${s.focus}`));
  }
  if (s.focus != null && s.focusGone) {
    kids.push(jsx("div", { role: "alert", className: "churn-banner",
      children: `banner: focused ${s.focus} confirmed deleted (history survives)` },
      `banner-gone:${s.focus}`));
  }
  for (const t of confirmed?.tombstones ?? []) {
    kids.push(jsx("div", { className: "diff-tombstone", "data-kind": "tombstone", children:
      jsx(Fragment, { children: [
        `deleted: ${t.id} `,
        jsx("a", { href: t.citation, className: "history-link",
          children: t.citation }, `hl:${t.id}`),
      ] }) }, `tb:${t.id}`));
  }
  for (const a of diff?.deletionsUnconfirmed ?? []) {
    kids.push(jsx("div", { className: "diff-absent", "data-kind": "absent", children:
      `unconfirmed: ${a.id} — ${a.proof}` }, `ab:${a.id}`));
  }
  return jsx("section", { "aria-label": "Split compare", className: "split-compare",
    "data-side": side, "data-focus": String(s.focus), children: kids }, `sc:${side}`);
}
