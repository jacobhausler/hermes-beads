// tests/test_drafts.mjs — draft store keyed by canonical store
// identity + bead ID, durable-storage-or-honest-warning, capability-proven
// guarded submission (CONTENT_CAS_SUPPORTED=False ⇒ Save disabled), and
// diff/reload/cancel never silently discard the draft.
// Run: node --test tests/test_drafts.mjs   (Node built-in runner, no deps)
//
// Purity rule: this module performs ZERO I/O.
// Durability comes ONLY through an injected storage adapter — the supported
// SDK storage surface provided by the host loader. Tests inject an in-memory
// fake; nothing here touches localStorage/window/DOM or spawns bd.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath, pathToFileURL } from "node:url";
import path from "node:path";

const here = path.dirname(fileURLToPath(import.meta.url));
const draftsMod = await import(pathToFileURL(path.join(here, "..", "desktop", "drafts.mjs")).href);
const {
  CONTENT_CAS_SUPPORTED, draftKey, createDraftStore, editDecision,
  draftLimitationNotice, runContentSave,
} = draftsMod;

// ---- helpers ----------------------------------------------------------------
const SI_A = { workspace: "/lab/storeA", db: "/lab/storeA/.beads/lab.db" };
const SI_B = { workspace: "/lab/storeB", db: "/lab/storeB/.beads/lab.db" };

function fakeStorage(opts = {}) {
  const map = new Map(opts.seed ?? []);
  const log = [];
  return {
    available: opts.available !== false,
    failures: opts.failures ?? null, // {method: Error} -> adapter throws
    map,
    log,
    get(key) {
      log.push(["get", key]);
      if (this.failures?.get) throw this.failures.get;
      return map.has(key) ? map.get(key) : null;
    },
    set(key, value) {
      log.push(["set", key]);
      if (this.failures?.set) throw this.failures.set;
      map.set(key, value);
    },
    remove(key) {
      log.push(["remove", key]);
      if (this.failures?.remove) throw this.failures.remove;
      map.delete(key);
    },
    keys() {
      log.push(["keys"]);
      return [...map.keys()];
    },
  };
}

const okSave = (beadId, text = "body") => ({ beadId, text, savedAt: "t0" });

// ---- capability truth ---------------------------------------------------------
test("capability truth: CONTENT_CAS_SUPPORTED is a hard false", () => {
  assert.strictEqual(CONTENT_CAS_SUPPORTED, false);
  const d = createDraftStore({ storage: fakeStorage() });
  d.saveDraft(SI_A, "abc", "draft body", { baseText: "orig" });
  const res = runContentSave(d, SI_A, "abc", {
    contentCasSupported: false,
    attemptBlindReplace: () => { throw new Error("blind replace must never be attempted"); },
    appendSuggestion: () => ({ ok: true }),
  });
  assert.equal(res.saved, false);
  assert.equal(res.reason, "unsupported:no-atomic-content-guard");
  assert.match(d.draftLimitationNotice().text, /content guard/i);
});

test("editDecision: Save disabled without proven guard; draft/copy/export/suggestion stay enabled; no fake success", () => {
  const dec = editDecision({ contentCasSupported: false });
  assert.equal(dec.saveContent.enabled, false);
  assert.match(dec.saveContent.disabledReason, /content guard/i);
  for (const f of ["draft", "copy", "export", "appendSuggestion"]) {
    assert.equal(dec[f].enabled, true, f);
  }
  // positive branch: proven atomic guard would enable Save (not claimed here)
  assert.equal(editDecision({ contentCasSupported: true }).saveContent.enabled, true);
});

test("draftLimitationNotice states the missing safe edit as a product limitation", () => {
  const n = draftLimitationNotice();
  assert.match(n.text, /NO ATOMIC CONTENT GUARD/i);
  assert.match(n.text, /append-only/i);
});

// ---- keying + isolation -------------------------------------------------------
test("draftKey is canonical store identity + bead ID and isolates every axis", () => {
  const k = draftKey(SI_A, "abc");
  assert.notEqual(k, draftKey({ ...SI_A, db: "/elsewhere/lab.db" }, "abc"), "db move must re-key");
  assert.notEqual(k, draftKey({ ...SI_A, workspace: "/other" }, "abc"), "workspace move must re-key");
  assert.notEqual(k, draftKey(SI_A, "abd"), "bead must re-key");
  // key does not depend on incidental storeInfo fields
  assert.equal(k, draftKey({ ...SI_A, extra: 1 }, "abc"));
});

test("cross-workspace/reload isolation: drafts never cross store boundaries", () => {
  const storage = fakeStorage();
  const d = createDraftStore({ storage });
  d.saveDraft(SI_A, "shared", "A-draft", { baseText: "a" });
  d.saveDraft(SI_B, "shared", "B-draft", { baseText: "b" });
  assert.equal(d.getDraft(SI_A, "shared").text, "A-draft");
  assert.equal(d.getDraft(SI_B, "shared").text, "B-draft");
  // clear one side, other untouched
  d.discardDraft(SI_A, "shared");
  assert.equal(d.getDraft(SI_A, "shared"), null);
  assert.equal(d.getDraft(SI_B, "shared").text, "B-draft");
  // same bead id in a store with same workspace-different-db stays isolated
  const d2 = createDraftStore({ storage: fakeStorage() });
  d2.saveDraft(SI_A, "x", "one", {});
  d2.saveDraft({ ...SI_A, db: "/lab/storeA/.beads/other.db" }, "x", "two", {});
  assert.equal(d2.getDraft(SI_A, "x").text, "one");
});

// ---- durability: supported storage OR honest warning, never silent loss --------
test("supported durable storage preserves drafts across simulated reload", () => {
  const storage = fakeStorage();
  const d1 = createDraftStore({ storage });
  d1.saveDraft(SI_A, "abc", "half written", { baseText: "orig" });
  const d2 = createDraftStore({ storage }); // simulated reload
  assert.equal(d2.durability(), "durable");
  const restored = d2.getDraft(SI_A, "abc");
  assert.equal(restored.text, "half written");
  assert.equal(restored.baseText, "orig");
  assert.ok(!Object.isFrozen(restored), "restored draft is a usable copy");
});

test("no durable adapter: honest memory-only warning, drafts still work in-session", () => {
  const d = createDraftStore({});
  assert.equal(d.durability(), "memory-only");
  assert.equal(d.warning().kind, "memory-only");
  assert.match(d.warning().text, /lost on reload/i);
  d.saveDraft(SI_A, "abc", "volatile", {});
  assert.equal(d.getDraft(SI_A, "abc").text, "volatile");
  const reloaded = createDraftStore({});
  assert.equal(reloaded.getDraft(SI_A, "abc"), null, "memory-only must not pretend durability");
});

test("adapter declared unavailable: honest warning, not fake success", () => {
  const d = createDraftStore({ storage: fakeStorage({ available: false }) });
  assert.equal(d.durability(), "memory-only");
  assert.equal(d.warning().kind, "memory-only");
});

test("adapter throws mid-write: honest storage-error warning + in-session draft survives", () => {
  const storage = fakeStorage({ failures: { set: new Error("quota deep-six") } });
  const d = createDraftStore({ storage });
  assert.equal(d.durability(), "memory-only");
  assert.equal(d.warning().kind, "storage-error");
  d.saveDraft(SI_A, "abc", "still here", {});
  assert.equal(d.getDraft(SI_A, "abc").text, "still here");
});

// ---- diff/reload/cancel never silently discard ---------------------------------
test("diff, reload and cancel preserve the draft; only explicit discard clears it", () => {
  const storage = fakeStorage();
  const d = createDraftStore({ storage });
  d.saveDraft(SI_A, "abc", "my draft", { baseText: "their base" });
  assert.deepEqual(d.diff(SI_A, "abc"), { baseText: "their base", text: "my draft" });
  const sess = d.openEdit(SI_A, "abc", "their base");
  sess.cancel();                      // cancel = close editor; keeps draft
  assert.equal(d.getDraft(SI_A, "abc").text, "my draft");
  const reloaded = createDraftStore({ storage }); // reload
  assert.equal(reloaded.getDraft(SI_A, "abc").text, "my draft");
  reloaded.cancelEdit(SI_A, "abc");   // top-level cancel path also preserves
  assert.equal(reloaded.getDraft(SI_A, "abc").text, "my draft");
  // explicit discard is the ONLY clear path (and save-success is the other)
  reloaded.discardDraft(SI_A, "abc");
  assert.equal(reloaded.getDraft(SI_A, "abc"), null);
});

test("Save disabled path never clears the draft (no silent discard on failed submission)", () => {
  const storage = fakeStorage();
  const d = createDraftStore({ storage });
  d.saveDraft(SI_A, "abc", "keep me", { baseText: "b" });
  const res = runContentSave(d, SI_A, "abc", {
    contentCasSupported: false,
    attemptBlindReplace: () => { throw new Error("must not run"); },
    appendSuggestion: () => ({ ok: true }),
  });
  assert.equal(res.saved, false);
  assert.equal(d.getDraft(SI_A, "abc").text, "keep me");
});

test("append-only suggestion remains usable while Save is disabled", () => {
  const d = createDraftStore({ storage: fakeStorage() });
  d.saveDraft(SI_A, "abc", "proposed content", { baseText: "b" });
  const res = runContentSave(d, SI_A, "abc", {
    contentCasSupported: false,
    attemptBlindReplace: () => { throw new Error("must not run"); },
    appendSuggestion: (draft) => {
      assert.equal(draft.text, "proposed content");
      return { ok: true, commentId: "c1" };
    },
  });
  assert.equal(res.saved, false);
  assert.equal(res.suggestion, "c1");
  assert.equal(res.channel, "append-only-comment");
});

test("supported save path clears the draft only after read-back proof", () => {
  const storage = fakeStorage();
  const d = createDraftStore({ storage });
  d.saveDraft(SI_A, "abc", "final", { baseText: "b" });
  const res = runContentSave(d, SI_A, "abc", {
    contentCasSupported: true,
    attemptBlindReplace: () => ({ ok: true, readBack: { description: "final" } }),
    appendSuggestion: () => { throw new Error("should not be needed"); },
  });
  assert.equal(res.saved, true);
  assert.equal(d.getDraft(SI_A, "abc"), null, "save+proof is the only auto-clear");
});

test("supported save path with missing/failing read-back keeps the draft", () => {
  const d = createDraftStore({ storage: fakeStorage() });
  d.saveDraft(SI_A, "abc", "final", { baseText: "b" });
  const res = runContentSave(d, SI_A, "abc", {
    contentCasSupported: true,
    attemptBlindReplace: () => ({ ok: true, readBack: { description: "different" } }),
    appendSuggestion: () => ({ ok: true }),
  });
  assert.equal(res.saved, false);
  assert.equal(res.reason, "readback-mismatch");
  assert.equal(d.getDraft(SI_A, "abc").text, "final");
});

// ---- copy/export + robustness --------------------------------------------------
test("copyText and exportText hand out content without touching the draft", () => {
  const d = createDraftStore({ storage: fakeStorage() });
  d.saveDraft(SI_A, "abc", "copy me\nplease", { baseText: "b" });
  assert.equal(d.copyText(SI_A, "abc"), "copy me\nplease");
  const x = JSON.parse(d.exportText(SI_A, "abc"));
  assert.equal(x.text, "copy me\nplease");
  assert.equal(x.beadId, "abc");
  assert.equal(d.getDraft(SI_A, "abc").text, "copy me\nplease");
});

test("corrupt stored draft is dropped and surfaces null, never a crash", () => {
  const badKey = draftKey(SI_A, "abc");
  const storage = fakeStorage({ seed: [[badKey, "{not json"]] });
  const d = createDraftStore({ storage });
  assert.equal(d.getDraft(SI_A, "abc"), null);
  const last = storage.log[storage.log.length - 1];
  assert.deepEqual(last, ["remove", badKey]);
});

test("capacity limit: saves beyond maxDrafts are refused, existing drafts intact", () => {
  const d = createDraftStore({ storage: fakeStorage(), maxDrafts: 2 });
  assert.ok(d.saveDraft(SI_A, "a", "1", {}));
  assert.ok(d.saveDraft(SI_A, "b", "2", {}));
  assert.equal(d.saveDraft(SI_A, "c", "3", {}), null);
  assert.equal(d.getDraft(SI_A, "a").text, "1");
  assert.equal(d.getDraft(SI_A, "c"), null);
});

// ---- adapter capability failures: get/remove/keys (parent repro 09-29) ---------
// An adapter exposing only {get,set} is INCOMPLETE storage. Incomplete storage
// must never be reported as durable, and a discarded draft must never come back.
test("adapter WITHOUT remove: honest session-only warning; discard stays discarded", () => {
  const storage = fakeStorage();
  delete storage.remove;                       // supported SDK surface: {get,set} only
  const d = createDraftStore({ storage });
  assert.equal(d.durability(), "memory-only",
    "an adapter lacking remove must not be reported durable");
  assert.equal(d.warning().kind, "memory-only");
  assert.match(d.warning().text, /cannot clear|incomplete/i);
  d.saveDraft(SI_A, "abc", "do not resurrect me", { baseText: "b" });
  assert.equal(d.discardDraft(SI_A, "abc"), true);
  assert.equal(d.getDraft(SI_A, "abc"), null,
    "discarded draft must NOT resurrect from uncleared adapter storage");
  // reload over the same adapter: the discarded draft stays gone, too
  const reloaded = createDraftStore({ storage });
  assert.equal(reloaded.getDraft(SI_A, "abc"), null);
});

test("incomplete adapter: pre-seeded draft from an earlier durable run stays gone after discard", () => {
  // The parent's repro shape: a draft exists in storage, but the adapter has
  // no clear primitive. Discarding must be final for this instance AND for a
  // new instance over the same adapter — never a silent resurrection.
  const storage = fakeStorage({ seed: [[draftKey(SI_A, "abc"),
    JSON.stringify({ storeKey: "k", beadId: "abc", text: "old draft", baseText: "b", updatedAt: null })]] });
  delete storage.remove;
  const d = createDraftStore({ storage });
  assert.equal(d.durability(), "memory-only");
  assert.equal(d.discardDraft(SI_A, "abc"), true);
  assert.equal(d.getDraft(SI_A, "abc"), null);
  assert.equal(createDraftStore({ storage }).getDraft(SI_A, "abc"), null,
    "an incomplete adapter must never be read back as durable storage");
});

test("throwing remove is terminal: durability never flips back to durable", () => {
  const storage = fakeStorage({ failures: { remove: new Error("remove unsupported") } });
  const d = createDraftStore({ storage });
  d.saveDraft(SI_A, "a1", "one", {});
  d.saveDraft(SI_A, "a2", "two", {});
  assert.equal(d.durability(), "memory-only",
    "one capability failure must not be re-trusted by later successful writes");
  assert.equal(d.getDraft(SI_A, "a1").text, "one");
});

test("adapter throwing on remove at setup: memory-only + storage-error; discard is honestly complete", () => {
  const storage = fakeStorage({ failures: { remove: new Error("remove unsupported") } });
  const d = createDraftStore({ storage });
  assert.equal(d.durability(), "memory-only",
    "a throwing remove is a capability failure — never claim durable");
  assert.equal(d.warning().kind, "storage-error");
  assert.match(d.warning().text, /session-only/i);
  d.saveDraft(SI_A, "abc", "keep visible", {});
  assert.equal(d.getDraft(SI_A, "abc").text, "keep visible");
  // nothing reached storage, so the local delete IS the whole clear
  assert.equal(d.discardDraft(SI_A, "abc"), true);
  assert.equal(d.getDraft(SI_A, "abc"), null);
});

test("adapter losing remove mid-session: discard returns false, durability drops, draft survives", () => {
  const storage = fakeStorage();
  const d = createDraftStore({ storage });
  d.saveDraft(SI_A, "abc", "survivor", {});      // written durably
  assert.equal(d.durability(), "durable");
  storage.remove = () => { throw new Error("remove revoked"); };
  assert.equal(d.discardDraft(SI_A, "abc"), false,
    "a discard that could not be committed to storage must not report true");
  assert.equal(d.durability(), "memory-only");
  assert.equal(d.warning().kind, "storage-error");
  assert.match(d.warning().text, /session-only/i);
  assert.equal(d.getDraft(SI_A, "abc").text, "survivor",
    "an un-committed discard keeps the content visible — no silent loss");
});

test("adapter throwing on get during setup probe: durable rejected, later discard cannot resurrect", () => {
  const storage = fakeStorage({ failures: { get: new Error("read wall") } });
  const d = createDraftStore({ storage });
  assert.equal(d.durability(), "memory-only");
  assert.equal(d.warning().kind, "storage-error");
  d.saveDraft(SI_A, "abc", "volatile only", {});
  assert.equal(d.getDraft(SI_A, "abc").text, "volatile only");
  assert.equal(d.discardDraft(SI_A, "abc"), true,
    "with durable writes already rejected, in-memory discard is complete");
  assert.equal(d.getDraft(SI_A, "abc"), null);
});

test("adapter throwing on keys: durability() must flip to memory-only with session-only warning", () => {
  const storage = fakeStorage();
  const d = createDraftStore({ storage });
  assert.equal(d.durability(), "durable");       // optimistic start is fine
  storage.keys = () => { throw new Error("listing denied"); };
  d.saveDraft(SI_A, "abc", "x", {});             // triggers capacity keyCount()
  assert.equal(d.durability(), "memory-only",
    "a throwing keys() is the same root capability failure — not durable");
  assert.equal(d.warning().kind, "storage-error");
  assert.match(d.warning().text, /session-only/i);
});

test("proven save where the draft clear FAILS reports the un-cleared draft honestly", () => {
  const storage = fakeStorage();
  const d = createDraftStore({ storage });
  d.saveDraft(SI_A, "abc", "final", { baseText: "b" });
  storage.remove = () => { throw new Error("remove revoked"); };
  const res = runContentSave(d, SI_A, "abc", {
    contentCasSupported: true,
    attemptBlindReplace: () => ({ ok: true, readBack: { description: "final" } }),
    appendSuggestion: () => ({ ok: true }),
  });
  assert.equal(res.saved, true, "the content write itself did succeed");
  assert.equal(res.draftCleared, false, "but the clear did not — say so");
  assert.equal(d.durability(), "memory-only");
  assert.equal(d.warning().kind, "storage-error");
});

test("proven save with a working clear reports draftCleared true", () => {
  const d = createDraftStore({ storage: fakeStorage() });
  d.saveDraft(SI_A, "abc", "final", { baseText: "b" });
  const res = runContentSave(d, SI_A, "abc", {
    contentCasSupported: true,
    attemptBlindReplace: () => ({ ok: true, readBack: { description: "final" } }),
    appendSuggestion: () => { throw new Error("not needed"); },
  });
  assert.equal(res.saved, true);
  assert.equal(res.draftCleared, true);
  assert.equal(d.getDraft(SI_A, "abc"), null);
});

// ---- reviewer probe (drive-hci review): clear-debt after a NON-remove failure --
// If the store wrote a draft durably and then degraded through keys()/get()
// (remove still functional), a later discard must still clear what it wrote.
// Returning true while leaving the storage entry behind resurrects the
// discarded draft on reload — the same bug class as the parent repro.
test("discard after non-remove capability failure clears storage; no reload resurrection", () => {
  const storage = fakeStorage();
  const d = createDraftStore({ storage });
  d.saveDraft(SI_A, "abc", "was durable", {});          // committed durably
  assert.equal(d.durability(), "durable");
  storage.keys = () => { throw new Error("listing denied once"); };
  d.saveDraft(SI_A, "other", "degrade me", {});         // keyCount() fault ⇒ terminal degrade
  assert.equal(d.durability(), "memory-only");
  assert.equal(d.discardDraft(SI_A, "abc"), true);
  assert.equal(storage.map.has(draftKey(SI_A, "abc")), false,
    "the entry this store wrote durably must be cleared, not left to resurrect");
  const reloaded = createDraftStore({ storage });       // fresh instance, remove healed
  assert.equal(reloaded.durability(), "durable");
  assert.equal(reloaded.getDraft(SI_A, "abc"), null,
    "a discarded draft must not come back after reload");
});

test("discard after degrade with a revoked remove reports false and keeps the draft visible", () => {
  const storage = fakeStorage();
  const d = createDraftStore({ storage });
  d.saveDraft(SI_A, "abc", "durable copy", {});
  storage.keys = () => { throw new Error("listing denied"); };
  d.saveDraft(SI_A, "x", "degrade", {});
  assert.equal(d.durability(), "memory-only");
  storage.remove = () => { throw new Error("remove revoked"); };
  assert.equal(d.discardDraft(SI_A, "abc"), false,
    "the store still owes a storage-side clear for what it wrote durably");
  assert.equal(d.getDraft(SI_A, "abc").text, "durable copy",
    "no silent loss: the un-cleared draft stays visible");
});

test("loaded draft is cleared after later storage degradation", () => {
  const storage = fakeStorage();
  createDraftStore({ storage }).saveDraft(SI_A, "abc", "prior session", {});
  const d = createDraftStore({ storage });
  assert.equal(d.getDraft(SI_A, "abc").text, "prior session");
  storage.keys = () => { throw new Error("listing failed"); };
  d.saveDraft(SI_A, "other", "session only", {});
  assert.equal(d.discardDraft(SI_A, "abc"), true);
  assert.equal(createDraftStore({ storage }).getDraft(SI_A, "abc"), null);
});

test("write that persists then throws still requires storage-side discard", () => {
  const storage = fakeStorage();
  const d = createDraftStore({ storage });
  const set = storage.set.bind(storage);
  storage.set = (key, value) => { set(key, value); throw new Error("uncertain write"); };
  d.saveDraft(SI_A, "abc", "persisted despite error", {});
  assert.equal(d.durability(), "memory-only");
  assert.equal(d.discardDraft(SI_A, "abc"), true);
  storage.set = set;
  assert.equal(createDraftStore({ storage }).getDraft(SI_A, "abc"), null);
});

// ---- purity audit ---------------------------------------------------------------
test("desktop/drafts.mjs is pure: no fs/child_process/localStorage/window/DOM/bd reach", () => {
  const src = readFileSync(path.join(here, "..", "desktop", "drafts.mjs"), "utf8");
  const banned = [
    "node:fs", "node:child_process", "require(", "localStorage", "sessionStorage",
    "indexedDB", "document.", "window.", "fetch(", "XMLHttpRequest",
    "BroadcastChannel", "child_process", "spawn(", "subprocess",
  ];
  for (const b of banned) {
    assert.ok(!src.includes(b), `forbidden reference in desktop/drafts.mjs: ${b}`);
  }
  // zero imports at all — the module imports nothing
  assert.ok(!/^\s*import\s/m.test(src.replace(/^\s*\/\/.*$/gm, "")),
    "drafts.mjs must import nothing");
});
