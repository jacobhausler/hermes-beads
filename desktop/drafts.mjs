// desktop/drafts.mjs — hbl-pnu.2.5: draft store + capability-proven guarded
// submission for the beads workbench.
//
// Replacement contract (pinned by tests/test_drafts.mjs):
//  - drafts are keyed by canonical store identity (workspace+db, the same
//    JSON form model.mjs storeIdentityKey computes — inlined here so this
//    module imports NOTHING) + bead ID; different stores never share drafts;
//  - persistence flows ONLY through an injected storage adapter, i.e. the
//    supported SDK storage surface supplied by the app loader. If none is
//    available (or it throws), the store declares honest memory-only
//    durability with an explicit warning — never a fake durable success;
//  - Save (replacement content write) is gated on a PROVEN atomic content
//    guard. For bd 1.3.0 that capability does not exist
//    (docs/content-guard-contract.md owns the truth; CONTENT_CAS_SUPPORTED
//    below is a hard false), so Save is DISABLED and the UI must show the
//    limitation; draft/copy/export and the append-only suggestion/comment
//    remain usable;
//  - diff/reload/cancel never silently discard a draft. The only clears are
//    explicit discard, or save-success backed by a read-back proof.
//
// Purity law (CONTRACTS-v3 C1, bd-expert): zero I/O, zero imports; host
// browsers, DOM globals, node built-ins and bd spawns are all banned (pinned
// by the source-audit tests). Adapters are injected by the app loader.

// docs/content-guard-contract.md owns capability truth: bd 1.3.0 (f45b249ce)
// exposes no atomic expected-content/revision mutation — assignee/status
// guards pass over stale content, `revision` is telemetry, not a token.
// Any editor built on this module MUST NOT offer blind replacement as Save.
export const CONTENT_CAS_SUPPORTED = false;

const CAPABILITY_KEY_PREFIX = "hbl.draft.v1:";
const PROBE_KEY = "hbl.draft.v1.__probe__";

// Same canonical form as model.mjs storeIdentityKey: JSON of {v, workspace,
// db}, tolerant of bd info / bd info-equivalent field spellings. Inlined
// (import-free module law); the Python wrapper pins byte equality with the
// model's version.
export function draftKey(storeInfo, beadId) {
  const ws = storeInfo?.workspace ?? storeInfo?.workspace_path;
  const db = storeInfo?.db ?? storeInfo?.database ?? storeInfo?.database_path;
  if (!ws || !db) {
    throw new Error(
      `draft key needs workspace+db (bd info equivalent); got workspace=${ws ? "ok" : "missing"} db=${db ? "ok" : "missing"}`,
    );
  }
  if (!beadId) throw new Error("draft key needs a bead ID");
  return `${CAPABILITY_KEY_PREFIX}${JSON.stringify({ v: 1, workspace: ws, db: db })}|${beadId}`;
}

// Pure capability gate. Consumers render Save disabled with `disabledReason`
// whenever content CAS is unproven; everything else stays enabled.
export function editDecision({ contentCasSupported = CONTENT_CAS_SUPPORTED } = {}) {
  return {
    draft: { enabled: true },
    copy: { enabled: true },
    export: { enabled: true },
    appendSuggestion: { enabled: true },
    saveContent: contentCasSupported === true
      ? { enabled: true }
      : {
          enabled: false,
          disabledReason:
            "Save disabled: no N6-proven atomic content guard (docs/content-guard-contract.md). " +
            "Native assignee/status guards cannot detect a description changed under unchanged owner/status.",
        },
  };
}

// The honest product-limitation notice shown wherever Save would be.
// Missing safe edit is a PRODUCT LIMITATION: no collaborative-edit claim.
export function draftLimitationNotice() {
  return {
    saveDisabled: !CONTENT_CAS_SUPPORTED,
    collaborativeEditClaimed: false,
    text:
      "NO ATOMIC CONTENT GUARD (bd 1.3.0 — see docs/content-guard-contract.md): " +
      "replacement Save is disabled; two actors can silently lose one description edit. " +
      "Draft, copy and export stay available; the safe channel is the append-only suggestion/comment.",
  };
}

// Submission decider. It NEVER fakes success: with an unproven guard the
// blind-replace callback is not invoked at all; the draft survives until a
// save is both attempted (proven guard) AND read-back-verified.
export function runContentSave(draftStore, storeInfo, beadId, opts = {}) {
  const draft = draftStore.getDraft(storeInfo, beadId);
  if (!draft) return { saved: false, reason: "no-draft", channel: null };
  const casProven = opts.contentCasSupported === true; // strict: truthy junk won't enable
  if (!casProven) {
    return {
      saved: false,
      reason: "unsupported:no-atomic-content-guard",
      channel: "append-only-comment",
      suggestion: opts.appendSuggestion ? opts.appendSuggestion(draft).commentId ?? null : null,
    };
  }
  const res = opts.attemptBlindReplace ? opts.attemptBlindReplace(draft) : {};
  if (!res || !res.ok) return { saved: false, reason: res?.reason ?? "write-failed", channel: null };
  const readBack = res.readBack?.description ?? res.readBack?.title;
  if (readBack !== draft.text) {
    return { saved: false, reason: "readback-mismatch", channel: null };
  }
  draftStore.discardDraft(storeInfo, beadId); // proof-backed save is the only auto-clear
  return { saved: true, reason: null, channel: "guarded-update" };
}

// Draft store over an INJECTED storage adapter — the supported SDK storage
// surface (get/set/remove[/keys]). No adapter, an adapter that declares
// itself unavailable, or one that throws during the probe => honest
// memory-only durability + explicit warning. In-session drafts always work.
export function createDraftStore(opts = {}) {
  const adapter = opts.storage;
  const maxDrafts = opts.maxDrafts ?? 200;
  const local = new Map(); // session fallback + hot cache
  const sessions = new Map(); // key -> open editor handle
  let warning = null;
  let durable = false;

  const usable = !!adapter && adapter.available !== false &&
    typeof adapter.get === "function" && typeof adapter.set === "function";
  if (usable) {
    durable = true; // optimistic until proven otherwise
    try {
      adapter.set(PROBE_KEY, "1");
      if (typeof adapter.remove === "function") adapter.remove(PROBE_KEY);
    } catch {
      durable = false;
      warning = { kind: "storage-error", text: "Durable SDK storage raised during setup — drafts are session-only and will be lost on reload." };
    }
  } else {
    warning = { kind: "memory-only", text: "No supported durable storage adapter was provided — drafts are session-only and will be lost on reload." };
  }

  const keyCount = () => {
    const seen = new Set(local.keys());
    if (durable && typeof adapter.keys === "function") {
      try {
        for (const k of adapter.keys()) if (k.startsWith(CAPABILITY_KEY_PREFIX)) seen.add(k);
      } catch {
        warning = { kind: "storage-error", text: "Durable SDK storage raised while listing drafts — treating writes as session-only." };
      }
    }
    seen.delete(PROBE_KEY);
    return seen.size;
  };

  const put = (key, rec) => {
    local.set(key, rec);
    if (durable) {
      try { adapter.set(key, JSON.stringify(rec)); } catch {
        durable = false;
        warning = { kind: "storage-error", text: "Durable SDK storage raised during a write — later drafts are session-only and will be lost on reload." };
      }
    }
  };

  const get = (key) => {
    if (local.has(key)) return local.get(key);
    if (!durable) return null;
    let raw = null;
    try { raw = adapter.get(key); } catch {
      warning = { kind: "storage-error", text: "Durable SDK storage raised during a read — later drafts are session-only." };
      return null;
    }
    if (raw == null) return null;
    let rec;
    try { rec = JSON.parse(raw); } catch {
      try { if (typeof adapter.remove === "function") adapter.remove(key); } catch { /* drop best-effort */ }
      return null; // corrupt entry: dropped, never surfaced as a draft
    }
    if (!rec || typeof rec.text !== "string" || typeof rec.beadId !== "string") return null;
    local.set(key, rec);
    return rec;
  };

  const api = {
    durability: () => (durable ? "durable" : "memory-only"),
    warning: () => warning,
    draftLimitationNotice,

    saveDraft(storeInfo, beadId, text, meta = {}) {
      const key = draftKey(storeInfo, beadId);
      if (!local.has(key) && !get_raw_exists(key) && keyCount() >= maxDrafts) return null;
      put(key, {
        storeKey: key.slice(CAPABILITY_KEY_PREFIX.length, key.lastIndexOf("|")),
        beadId,
        text: String(text ?? ""),
        baseText: meta.baseText ?? "",
        updatedAt: meta.updatedAt ?? null,
      });
      return api.getDraft(storeInfo, beadId);
    },

    getDraft(storeInfo, beadId) {
      const rec = get(draftKey(storeInfo, beadId));
      return rec ? { ...rec } : null; // caller-owned copy
    },

    // Edit session handle: cancel() closes the editor and PRESERVES the
    // draft. Discarding is only ever the explicit discardDraft path.
    openEdit(storeInfo, beadId, baseText = "") {
      const key = draftKey(storeInfo, beadId);
      const handle = {
        beadId,
        diff: () => api.diff(storeInfo, beadId),
        cancel: () => { sessions.delete(key); },
      };
      sessions.set(key, handle);
      if (baseText && !get(key)?.baseText) {
        put(key, { ...(get(key) ?? { storeKey: key.slice(CAPABILITY_KEY_PREFIX.length, key.lastIndexOf("|")), beadId, text: "" }), baseText });
      }
      return handle;
    },
    cancelEdit(storeInfo, beadId) {
      sessions.delete(draftKey(storeInfo, beadId)); // draft intentionally survives
    },

    diff(storeInfo, beadId) {
      const rec = get(draftKey(storeInfo, beadId));
      return rec ? { baseText: rec.baseText, text: rec.text } : null;
    },

    copyText(storeInfo, beadId) {
      const rec = get(draftKey(storeInfo, beadId));
      return rec ? rec.text : null;
    },
    exportText(storeInfo, beadId) {
      const rec = get(draftKey(storeInfo, beadId));
      return rec ? JSON.stringify({ v: 1, beadId: rec.beadId, text: rec.text, baseText: rec.baseText }) : null;
    },

    discardDraft(storeInfo, beadId) {
      const key = draftKey(storeInfo, beadId);
      local.delete(key);
      sessions.delete(key);
      if (durable) {
        try { if (typeof adapter.remove === "function") adapter.remove(key); } catch { /* already gone */ }
      }
      return true;
    },
  };

  // existence check that does NOT warm the local cache (capacity accounting)
  function get_raw_exists(key) {
    if (local.has(key)) return true;
    if (!durable) return false;
    try { return adapter.get(key) != null; } catch { return false; }
  }

  return api;
}
