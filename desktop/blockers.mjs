// desktop/blockers.mjs — hbl-pnu.2.4: cross-branch blocker jump with a
// read-only blocker CARD and one-press return.
//
// Sole-state law (CONTRACTS-v3 C1, bd-expert): the ONLY state touched is the
// existing model (model.mjs createWorkbenchState: selection/focus/pane/
// expansion, its push-based back stack), the independent history stack
// (history.mjs createHistoryStack) and the tree renderers. This module keeps
// NO navigation state of its own and creates no parallel copy.
//
// Purity: zero I/O — native data arrives through an injected fixed-argv
// provider ({run(...argv), storeInfo}); every call this module makes passes
// assertReadArgv, so the card surface CANNOT mutate (closing a blocker is an
// authorized action elsewhere). Readiness truth is the native ready/blocked
// reads (FACT P6d); the dep-tree READY badge is kept only as visible
// provenance, never trusted.
import { jsx } from "react/jsx-runtime";
import { resolveKey } from "./tree.mjs";

// ---- read-only boundary -----------------------------------------------------
const READ_VERBS = new Set(["list", "ready", "blocked", "info", "show", "children", "dep"]);
const FORBIDDEN_FLAGS = ["--force", "--claim", "-s", "--status", "-a", "--assignee",
  "--if-assignee", "--if-status"];

export function assertReadArgv(argv) {
  const flat = argv.map(String);
  const verb = flat[0];
  if (!READ_VERBS.has(verb)
      || (verb === "dep" && flat[1] !== "tree")
      || flat.some((a) => FORBIDDEN_FLAGS.includes(a))) {
    throw new Error(`mutation attempted from blocker card: bd ${flat.join(" ")} ` +
      "— the card is read-only; closing a blocker is an authorized action elsewhere");
  }
  return flat;
}

function guardedRun(provider, ...argv) {
  assertReadArgv(argv);
  return provider?.run?.(...argv);
}

// ---- blocker discovery (model-derived; no second engine) --------------------
// ALL open blockers are offered — direct typed `blocks` edges AND inherited
// ones carrying the source path to the blocked ancestor (model decorate
// computed both from the bounded snapshot; closed deps already dropped out).
export function blockersFor(snapshot, id) {
  const node = snapshot?.nodes?.get(id);
  if (!node) throw new Error(`blockersFor: bead ${id} not in snapshot`);
  return [...node.typedBlockers, ...node.inheritedBlockers];
}

// Distinguish incoming blockers from outgoing dependents and parent-child
// edges (owner contract). Edges kept verbatim, unknown types surfaced as-is.
export function blockerEdges(snapshot, id) {
  const node = snapshot?.nodes?.get(id);
  const rec = snapshot?.byId?.get(id);
  if (!node || !rec) throw new Error(`blockerEdges: bead ${id} not in snapshot`);
  const incomingBlockers = [];
  const outgoingDependents = [];
  const unknownEdges = [];
  for (const e of node.edges ?? []) {
    if (e.edgeType === "blocks") incomingBlockers.push({ id: e.id, edgeType: e.edgeType });
    else if (e.edgeType !== "parent-child") unknownEdges.push({ id: e.id, edgeType: e.edgeType });
  }
  const incomingDependents = [];
  for (const [otherId, other] of snapshot.nodes) {
    if (otherId === id) continue;
    for (const e of other.edges ?? []) {
      if (e.id !== id) continue;
      outgoingDependents.push({ id: otherId, edgeType: e.edgeType });
      if (e.edgeType === "blocks") incomingDependents.push({ id: otherId, edgeType: e.edgeType });
    }
  }
  return {
    incomingBlockers,
    outgoingDependents,
    incomingDependents,
    unknownEdges,
    parentChild: { parent: node.parent, children: [...node.childIds] },
  };
}

// ---- the card (pure view model, no mutation affordance) ----------------------
export function buildBlockerCard({ snapshot, targetId, depTree = null, records = {} } = {}) {
  const node = snapshot?.nodes?.get(targetId);
  if (!node) throw new Error(`card: bead ${targetId} not in snapshot`);
  const rec = records[targetId] ?? snapshot.byId.get(targetId) ?? null;
  const claimed = rec?.assignee != null && rec.assignee !== "";
  const treeBox = depTree?.tree ?? depTree ?? null;
  const liesReady = treeBox != null && (treeBox.ready === true
    || String(treeBox.badge ?? "").toUpperCase() === "READY");
  return {
    targetId,
    ancestry: Array.isArray(node.path) ? [...node.path] : [targetId], // parent FIELD chain
    statusWord: node.storedStatus ?? "unknown",
    derivedBlocked: node.derivedBlocked, // native ready/blocked truth only
    badge: node.derivedBlocked === true ? "blocked"
      : node.derivedBlocked === false ? "ready" : null, // no read => unknown
    depTreeClaimedReady: liesReady, // visible provenance, never truth (P6d)
    blockers: blockersFor(snapshot, targetId),
    edges: blockerEdges(snapshot, targetId),
    lease: claimed
      ? { holder: rec.assignee, leaseExpiresAt: rec.lease_expires_at ?? null,
          heartbeatAt: rec.heartbeat_at ?? null }
      : null,
  };
}

// the card is data; it must never carry callable mutation affordances
export function cardMutationSurface(card) {
  return Object.values(card ?? {}).some((v) => typeof v === "function");
}

// ---- jump + one-press return (rides the EXISTING stacks only) ----------------
function normalizeShow(r) {
  if (Array.isArray(r)) return r[0] && typeof r[0] === "object" ? r[0] : null;
  if (r && typeof r === "object" && typeof r.id === "string") return r;
  return null;
}

export function jumpToBlocker({ snapshot, ui, stack, state = {}, provider, targetId, pane }) {
  if (!snapshot || !ui || !stack) throw new Error("jumpToBlocker needs snapshot+ui+stack");
  const depTree = guardedRun(provider, "dep", "tree", targetId);
  const shown = normalizeShow(guardedRun(provider, "show", targetId, "--json"));
  const card = buildBlockerCard({ snapshot, targetId, depTree,
    records: { [targetId]: shown ?? snapshot.byId.get(targetId) } });
  ui.jump(targetId, pane); // model is the reveal + history push truth
  stack.push({ // the independent stack records the SAME transition
    storeKey: ui.storeKey, beadId: targetId, pane: ui.pane,
    selection: ui.selection, focus: ui.focus, expanded: [...ui.expanded].sort(),
    scroll: state.scroll ?? 0, filter: state.filter ?? null,
    search: state.search ?? null, tab: state.tab ?? null,
  });
  return { card, stack, targetId };
}

export function returnFromCard({ stack, ui, state = {} }) {
  const entry = stack.back(); // ONE press = one stack step
  if (entry == null) return { restored: false, entry: null };
  ui.back(); // model walks its own stack in lockstep
  // the frozen stack bundle is the restore truth: reconcile the model's live
  // expansion set (tree renders it; no copy exists) and hand caller-owned
  // view fields back.
  ui.expanded.clear();
  for (const id of entry.expanded) ui.expanded.add(id);
  for (const k of ["scroll", "filter", "search", "tab"]) {
    if (entry[k] !== undefined) state[k] = entry[k];
  }
  return { restored: true, entry };
}

// the app-back gesture: one key event resolves (via the shared tree keymap)
// to exactly one stack return.
export function resolveReturnKey(ev) {
  return resolveKey(ev) === "history-back" ? "history-back" : null;
}
export function appBack(opts) { return returnFromCard(opts); }

// ---- component (evidence: rendered structure only — no mount/usability claim)
function BlockerRow({ b }) {
  return jsx("li", {
    id: `blocker-dep:${b.id}`,
    children: `${b.id}${b.inherited ? ` (inherited via ${b.source} from ${b.via?.join("/") ?? "?"})` : ""}`,
  }, `blocker-dep:${b.id}`);
}

export function BlockerCard({ card, onReturn }) {
  return jsx("section", {
    role: "complementary", "aria-label": "Blocker card",
    id: `blocker-card:${card.targetId}`,
    children: [
      jsx("header", { children: card.targetId }, "hdr"),
      jsx("nav", { "aria-label": "Ancestry",
        children: card.ancestry.map((a, i) => jsx("span", { id: `ancestry:${a}`, children: a }, `anc:${i}:${a}`)) },
        "anc"),
      jsx("div", { id: "blocker-status",
        children: `${card.statusWord}${card.badge ? ` · ${card.badge}` : " · readiness unknown"}` }, "st"),
      card.depTreeClaimedReady
        ? jsx("div", { id: "dep-tree-note",
            children: "dep-tree badge says READY — not trusted; native reads are truth" }, "note")
        : null,
      jsx("ul", { "aria-label": "Blockers",
        children: card.blockers.map((b) => jsx(BlockerRow, { b }, `b:${b.id}`)) }, "deps"),
      card.lease
        ? jsx("div", { id: "blocker-lease",
            children: `held by ${card.lease.holder} · lease ${card.lease.leaseExpiresAt ?? "?"} · heartbeat ${card.lease.heartbeatAt ?? "?"}` },
            "lease")
        : null,
      // read-only surface: exactly ONE affordance — the return. No close/reopen.
      jsx("button", { type: "button", id: "blocker-return",
        onClick: () => onReturn?.(), children: "Return" }, "ret"),
    ],
  }, `blocker-card:${card.targetId}`);
}
