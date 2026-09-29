// tests/test_bot_action.mjs — hbl-pnu.3.5 (Ask/Refine half): the bot action
// panel surface.
//  - Ask renders read-only, carries the EXACT typed session-door refusal
//    (never ok:true);
//  - Refine routes the bot proposal ONLY into the human draft store
//    (createDraftStore().saveDraft) with provenance bot +
//    requires_human_accept; the plugin never writes the store itself —
//    the module performs zero I/O (purity source-audit pinned here);
//  - Return-to-draft: reopenBotDraft re-opens the stored bot draft for the
//    human and never discards it;
//  - Work control renders present-but-disabled with the typed hbl-pnu.3.3
//    reason; enabling requires the admitted runner door (presentation only
//    — this module spawns nothing).
// Run: node --test tests/test_bot_action.mjs   (Node built-in runner, no deps)
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { register } from "node:module";
import { fileURLToPath, pathToFileURL } from "node:url";
import path from "node:path";

const here = path.dirname(fileURLToPath(import.meta.url));
register(pathToFileURL(path.join(here, "__shims__", "jsx-loader.mjs")).href);

const shim = await import(pathToFileURL(path.join(here, "__shims__", "jsx-capture.mjs")).href);
const { createDraftStore } = await import(pathToFileURL(path.join(here, "..", "desktop", "drafts.mjs")).href);
const { askDecision, refineToDraft, reopenBotDraft, botActionPanel, workControl } =
  await import(pathToFileURL(path.join(here, "..", "desktop", "bot_action.mjs")).href);

const SI = { workspace: "/lab/storeA", db: "/lab/storeA/.beads/lab.db" };
const TYPED_REFUSAL = {
  ok: false, error: "session_door_unqualified", status: "unqualified",
  route: "hermes_session_door", execution_authority: false,
  read_only: true,
  delivery: false, no_dispatch: true,
  workspace: SI.workspace, bead: "hbl-pnu.3.5", intent: "ask",
};

// ---- Ask -------------------------------------------------------------------
test("askDecision reports read-only ask with the exact typed refusal", () => {
  const d = askDecision(TYPED_REFUSAL);
  assert.equal(d.readOnly, true);
  assert.equal(d.ok, false);
  assert.equal(d.error, "session_door_unqualified");
  assert.equal(d.executionAuthority, false);
  assert.equal(d.delivery, false);
  assert.equal(d.noDispatch, true);
  assert.equal(d.bead, "hbl-pnu.3.5");
});

test("askDecision refuses an ok:true ask as a fabricated delivery", () => {
  const d = askDecision({ ...TYPED_REFUSAL, ok: true });
  assert.equal(d.ok, false);
  assert.match(d.error, /fabricated/);
});

// ---- Refine -> human draft store --------------------------------------------
test("refineToDraft lands the bot proposal in the human draft store only", () => {
  const store = createDraftStore(); // memory-only adapter path
  const res = refineToDraft(store, SI, "hbl-pnu.3.5", {
    text: "improved text", baseText: "original text",
    provenance: "bot", requires_human_accept: true, neverWritesStore: true,
  });
  assert.equal(res.landed, true);
  assert.equal(res.botProvenance, true);
  const draft = store.getDraft(SI, "hbl-pnu.3.5");
  assert.equal(draft.text, "improved text");
  assert.equal(draft.baseText, "original text");
  // provenance rides the panel's own registry (drafts.mjs owns persistence
  // and has no provenance field): reopen proves it is the bot's draft.
  assert.equal(reopenBotDraft(store, SI, "hbl-pnu.3.5").found, true);
});

test("refineToDraft without human-accept requirement is refused", () => {
  const store = createDraftStore();
  const res = refineToDraft(store, SI, "hbl-pnu.3.5", {
    text: "x", baseText: "", provenance: "bot",
  });
  assert.equal(res.landed, false);
  assert.match(res.reason, /requires_human_accept/);
  assert.equal(store.getDraft(SI, "hbl-pnu.3.5"), null);
});

// ---- Return-to-draft ---------------------------------------------------------
test("reopenBotDraft returns the stored draft and preserves it", () => {
  const store = createDraftStore();
  refineToDraft(store, SI, "hbl-pnu.3.5", {
    text: "proposal", baseText: "base", provenance: "bot",
    requires_human_accept: true,
  });
  const view = reopenBotDraft(store, SI, "hbl-pnu.3.5");
  assert.equal(view.found, true);
  assert.equal(view.diff.text, "proposal");
  assert.equal(view.diff.baseText, "base");
  // re-opening never discards: still there, cancel preserves too
  assert.equal(store.getDraft(SI, "hbl-pnu.3.5") !== null, true);
});

test("reopenBotDraft on a missing draft is honest, never fabricated", () => {
  const store = createDraftStore();
  const view = reopenBotDraft(store, SI, "hbl-pnu.3.5");
  assert.equal(view.found, false);
  assert.equal(view.diff, null);
});

// ---- Work control --------------------------------------------------------------
const DISABLED = {
  present: true, enabled: false,
  disabledReason: "Work disabled: the runner binding (hbl-pnu.3.3) is not merged; no admitted runner door is bound via bot_handoff.bind_runner_door. Ask/Refine remain available; the door is never invoked while disabled.",
};

test("work control renders present-but-disabled with the typed reason", () => {
  const tree = botActionPanel({ ask: askDecision(TYPED_REFUSAL), work: DISABLED });
  const texts = [...shim.walk(tree)].map((n) => n.props?.children)
    .flat(9).filter((c) => typeof c === "string");
  assert.ok(texts.some((t) => t.includes("Ask")));
  assert.ok(texts.some((t) => t.includes("Refine")));
  assert.ok(texts.some((t) => t.includes("Work")));
  assert.ok(texts.some((t) => t.includes("hbl-pnu.3.3")));
  assert.ok(texts.some((t) => t.includes("session_door_unqualified")));
  const buttons = [...shim.walk(tree)].filter((n) => n.type === "button");
  const work = buttons.find((b) => JSON.stringify(b).includes("Work"));
  assert.ok(work, "work button rendered");
  assert.equal(work.props.disabled, true);
});

test("work control flips to enabled only when the runner door is bound", () => {
  const tree = botActionPanel({
    ask: askDecision(TYPED_REFUSAL),
    work: { present: true, enabled: true, disabledReason: null },
  });
  const buttons = [...shim.walk(tree)].filter((n) => n.type === "button");
  const work = buttons.find((b) => JSON.stringify(b).includes("Work"));
  assert.equal(work.props.disabled, false);
});

// ---- Purity law (CONTRACTS-v3 C1): zero I/O, zero imports ----------------------
test("bot_action.mjs is import-free and I/O-free (purity source audit)", () => {
  const src = readFileSync(
    path.join(here, "..", "desktop", "bot_action.mjs"), "utf8");
  assert.ok(!/^\s*import\s/m.test(src), "no import statements");
  for (const banned of ["node:", "fetch(", "child_process", "localStorage",
    "window.", "document.", "XMLHttpRequest", "WebSocket", "require("]) {
    assert.ok(!src.includes(banned), `banned token present: ${banned}`);
  }
});
