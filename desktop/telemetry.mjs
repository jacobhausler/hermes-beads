// desktop/telemetry.mjs — council S3 step 2 (2026-09-30): pane-usage telemetry.
// WHY this exists: the deletion law (docs/hbi-boundary.md rule 4) forbids
// cutting a generic pane on maintenance-cost reasoning alone — a cut needs
// observed NON-USE at the real mount. Without counters, "nobody uses it" is
// unfalsifiable, so every future cut decision would stall. This is the minimal
// instrument that makes the evidence exist.
//
// Laws:
//  - NO I/O here (desktop modules keep I/O at zero; model.mjs law). The host
//    shell owns persistence via the injected slot, serialise() is pure.
//  - Whitelist-only event names (EVENTS): an untrusted caller can never grow
//    the key space (no unbounded-object, no prototype keys).
//  - Bounded FIFO ring: memory can never grow with uptime.
//  - Emit is fire-and-forget: a throwing sink must never break a user action.
//  - Counts are per storeKey: usage evidence is per real store, not global.

export const EVENTS = new Set([
  "search-open",       // SearchPanel shown (door opened)
  "search-activate",   // a search hit entered the tree
  "tree-jump",         // programmatic reveal/jump landed in the tree
  "blockers-card-open",// blocker card mounted (button or Ctrl+b)
  "blockers-jump",     // jumped to a blocker row from the card
  "drafts-save",       // a draft text saved to the store adapter
  "bot-action-click",  // claim/dispatch/ask button activated through the door
]);

const UNKNOWN = "unknown";

export function createTelemetry({ storeKey = null, max = 500 } = {}) {
  const key = typeof storeKey === "string" && storeKey ? storeKey : UNKNOWN;
  const events = [];             // bounded FIFO: {e, t}
  const counts = Object.create(null);
  return {
    events,
    emit(name, sink = null) {
      if (typeof name !== "string" || !EVENTS.has(name)) return false;
      events.push({ e: name, t: Date.now() });
      if (events.length > max) events.shift();
      (counts[key] ??= Object.create(null))[name] = (counts[key]?.[name] ?? 0) + 1;
      if (typeof sink === "function") { try { sink(name, key); } catch { /* never breaks the action */ } }
      return true;
    },
    uses() {
      const out = {};
      for (const [k, c] of Object.entries(counts)) {
        const clean = {};
        for (const [e, n] of Object.entries(c)) if (n > 0) clean[e] = n;
        if (Object.keys(clean).length) out[k] = clean;
      }
      return out;
    },
  };
}

// host-shell persistence helper (pure): drop zero/empty buckets before write.
export function serialise(uses) {
  const out = {};
  for (const [k, c] of Object.entries(uses ?? {})) {
    const clean = {};
    for (const [e, n] of Object.entries(c ?? {})) if (Number(n) > 0) clean[e] = n;
    if (Object.keys(clean).length) out[k] = clean;
  }
  return JSON.stringify(out);
}
