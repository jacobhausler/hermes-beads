// desktop/record.mjs — record-tolerance card for the beads
// workbench. Renders ONE native record from an injected snapshot so that:
//  - unknown statuses, issue types and edge types appear verbatim (the
//    snapshot's byId raw records are the only source; model.mjs is reused
//    unchanged and already preserves them);
//  - the STORED status and the NATIVE DERIVED readiness render as two
//    separate values; a disagreement surfaces model.mjs's divergence warning;
//  - a dep-tree [READY]-style badge on the raw record is display-only and
//    never used as a readiness source (frontier reads are the only truth);
//  - re-emit hands back a deep clone of the raw record — byte-identical
//    semantics, no normalization, and the source record is never mutated;
//  - everything renders as plain-text children (React escapes text nodes;
//    no dangerouslySetInnerHTML, no markup injection).
import { jsx } from "react/jsx-runtime";

export function reEmitRecord(snapshot, id) {
  const rec = snapshot?.byId?.get(id);
  if (!rec) throw new Error(`reEmit: bead ${id} not in snapshot`);
  return structuredClone(rec);
}

function Row({ label, value }) {
  return jsx("div", {
    className: "record-row",
    children: `${label}: ${value === null || value === undefined ? "unknown" : value}`,
  }, `row:${label}`);
}

export function RecordCard({ snapshot, id }) {
  const rec = snapshot?.byId?.get(id);
  if (!rec) throw new Error(`record card: bead ${id} not in snapshot`);
  const node = snapshot.nodes.get(id);
  const edges = (node?.edges ?? []).map((e) => e.edgeType).filter((t) => t != null);
  // Native derived readiness: model.mjs's positive-evidence field only
  // (ready/blocked frontier presence). Raw-record badges like
  // dep_tree_status are display decoration, never a readiness source.
  const derived = node?.derivedBlocked === null || node?.derivedBlocked === undefined
    ? "unknown"
    : node.derivedBlocked ? "blocked" : "ready";
  const kids = [
    Row({ label: "id", value: rec.id }),
    Row({ label: "stored status", value: node?.statusKnown ? node.storedStatus : null }),
    Row({ label: "derived readiness", value: derived }),
    Row({ label: "issue type", value: rec.issue_type ?? null }),
    Row({ label: "edge types", value: edges.join(", ") || "none" }),
  ];
  if (node?.divergence) {
    kids.push(jsx("div", {
      className: "record-divergence", role: "alert",
      children: `⚠ ${node.divergence.warning} (stored=${node.divergence.stored}, derived=${node.divergence.derived})`,
    }, "divergence"));
  }
  return jsx("section", { "aria-label": `Record ${id}`, id: `record:${id}`, children: kids },
    `record:${id}`);
}
