// desktop/bot_action.mjs — hbl-pnu.3.5 (Ask/Refine half): bot action panel
// for the beads workbench.
//
//  - Ask: read-only question scoped to the selected bead. The panel reports
//    the EXACT typed routing/refusal produced by the plugin (bot_handoff.ask
//    -> interop.submit_request); an unqualified session door is shown as its
//    typed refusal, and an ok:true ask is refused as a fabricated delivery.
//  - Refine: a bot proposal lands ONLY in the human draft store
//    (desktop/drafts.mjs, injected) carrying provenance=bot and
//    requires_human_accept=true. This panel never writes the bead store:
//    zero I/O, zero imports; the draft store and every decision/answer is
//    injected by the app loader.
//  - Return-to-draft: reopenBotDraft re-opens a stored bot draft (diff via
//    the draft store) and never discards it — the only clears stay the
//    human's explicit discard or proof-backed save (drafts.mjs law).
//  - Work control: rendered present-but-disabled with the typed
//    hbl-pnu.3.3 reason; the enabled flag comes verbatim from the plugin's
//    work_status() (single injection point bot_handoff.bind_runner_door).
//
// Purity law (CONTRACTS-v3 C1): the ONLY import is react/jsx-runtime (the
// same specifier the app loader maps, as in every other desktop component;
// the source audit in tests/test_bot_action.mjs pins that and bans node
// built-ins, DOM, fetch/spawn). hbl-pnu.4.6: elements are built via jsx()
// so real React can mount the panel UNCONVERTED — the old hand-rolled
// {type, props, key} records lacked $$typeof and were unmountable. Tests
// keep walking the output (the capture shim implements the same jsx shape).
import { jsx } from "react/jsx-runtime";

const el = (type, props, key) => jsx(type, props ?? {}, key ?? null);

// Ask decision view. The plugin verdict is the ONLY authority: ok:true on
// an ask would be a fabricated delivery claim and is refused to a typed
// local refusal instead of rendered as success.
export function askDecision(askResult) {
  if (!askResult || typeof askResult !== "object") {
    return { ok: false, error: "invalid_ask_result", readOnly: true };
  }
  if (askResult.ok === true) {
    return {
      ok: false,
      error: "fabricated_delivery_refused: ask must carry the typed session-door verdict",
      readOnly: true,
      bead: askResult.bead ?? null,
    };
  }
  return {
    ok: false,
    error: askResult.error ?? "unknown",
    status: askResult.status ?? null,
    route: askResult.route ?? null,
    readOnly: askResult.read_only === true,
    executionAuthority: askResult.execution_authority === true,
    delivery: askResult.delivery === true,
    noDispatch: askResult.no_dispatch === true,
    bead: askResult.bead ?? null,
    workspace: askResult.workspace ?? null,
    context: askResult.context ?? null,
  };
}

// Refine: route the bot proposal into the HUMAN draft store, never the bead
// store. The proposal must self-declare requires_human_accept — a draft that
// could land without the human is refused here (drafts.mjs owns persistence,
// this function owns the gate). drafts.mjs's record schema has no
// provenance field and this panel must not fork the accepted store, so the
// bot provenance rides a module-local registry keyed by (store object,
// beadId).
// ponytail: registry is per-panel-instance in-memory; if provenance must
// survive reload, add a provenance field to drafts.mjs's record schema in
// the 2.x lane that owns it.
const botDrafts = new WeakMap(); // draftStore -> Set("beadId")

export function refineToDraft(draftStore, storeInfo, beadId, proposal = {}) {
  if (!draftStore || typeof draftStore.saveDraft !== "function") {
    return { landed: false, reason: "no-draft-store-injected" };
  }
  if (proposal.requires_human_accept !== true) {
    return { landed: false, reason: "refused: bot draft must require human accept (requires_human_accept !== true)" };
  }
  if (typeof proposal.text !== "string" || !proposal.text.trim()) {
    return { landed: false, reason: "empty-proposal" };
  }
  const rec = draftStore.saveDraft(storeInfo, beadId, proposal.text, {
    baseText: proposal.baseText ?? "",
  });
  if (!rec) return { landed: false, reason: "draft-store-rejected-save" };
  if (!botDrafts.has(draftStore)) botDrafts.set(draftStore, new Set());
  botDrafts.get(draftStore).add(beadId);
  return { landed: true, reason: null, draft: rec, botProvenance: true };
}

// Return-to-draft path: re-open the stored bot draft for the human editor.
// diff()/openEdit preserve the draft (drafts.mjs law); nothing here clears
// anything.
export function reopenBotDraft(draftStore, storeInfo, beadId) {
  if (!draftStore || typeof draftStore.getDraft !== "function") {
    return { found: false, diff: null, reason: "no-draft-store-injected" };
  }
  const draft = draftStore.getDraft(storeInfo, beadId);
  const isBot = botDrafts.get(draftStore)?.has(beadId) === true;
  if (!draft || !isBot) {
    return { found: false, diff: null, reason: draft ? "not-a-bot-draft" : "no-draft" };
  }
  const session = typeof draftStore.openEdit === "function"
    ? draftStore.openEdit(storeInfo, beadId, draft.baseText ?? "")
    : null;
  return {
    found: true,
    diff: typeof draftStore.diff === "function"
      ? draftStore.diff(storeInfo, beadId)
      : { baseText: draft.baseText ?? "", text: draft.text },
    draft,
    session,
  };
}

// Work control presentation. `work` is the plugin's work_status() verbatim:
// present is always true; enabled flips only when the admitted runner door
// (hbl-pnu.3.3 binding) is bound. The button is presentation-only — this
// module spawns nothing.
//
// hbl-pnu.3.7 truthful run state: `runState` is bot_handoff.work_run_state()
// verbatim ({state: admitted|running|succeeded|failed|uncertain|
// cancel_requested|cancelled|unknown|unavailable, ...}) and renders VERBATIM
// in a role=status element — never 'delivered', never 'done', and no success
// text unless the state itself is 'succeeded'. The Cancel button is enabled
// ONLY in admitted/running; a click routes to the injected onCancel (this
// module keeps spawning nothing and holding no state — the human's request
// is latched by the host via `cancelRequested:true`, which keeps the
// display on cancel_requested through a lagging door poll until a LATER
// state says cancelled; the shipped WorkbenchApp owns that latch).
// hbl-pnu.3.7 (mounted smoke): a Work click routes to the injected
// host `onWork(bead)` — same law as onCancel: the component calls, the
// host owns all door I/O (work_bridge.py); this module spawns nothing.
const CANCELABLE = new Set(["admitted", "running"]);
const CANCEL_PHASE = new Set(["admitted", "running", "cancel_requested"]);

export function botActionPanel({ ask, work, refineLanded = false,
  runState = null, cancelRequested = false, onCancel = null,
  onWork = null, bead = null } = {}) {
  const raw = runState == null ? null
    : (typeof runState === "string" ? runState : runState.state) ?? null;
  // truth precedence: an explicit terminal state always wins; the host's
  // latched request shows cancel_requested through a lagging door read.
  const shown = raw != null
    ? (cancelRequested && CANCEL_PHASE.has(raw) ? "cancel_requested" : raw)
    : (cancelRequested ? "cancel_requested" : null);
  const askText = ask
    ? `Ask: ${ask.readOnly ? "read-only" : "?"} — ${ask.error ?? "ok"}`
    : "Ask: unavailable";
  const workLabel = work && work.enabled ? "Work (runner door bound)" : "Work";
  return el("div", {
    className: "bot-action-panel",
    // hbl-pnu.2.10 (F7): a labelled group so the controls announce as one
    // cluster with gaps (stylesheet), not fused inline text.
    role: "group", "aria-label": "Bot actions",
    children: [
      el("span", { children: askText }, "ask"),
      el("button", { children: "Refine" + (refineLanded ? " (draft saved)" : ""), disabled: false }, "refine"),
      el("button", {
        children: workLabel,
        disabled: !(work && work.enabled),
        title: work && work.enabled ? "runner door bound" : (work?.disabledReason ?? "work state unavailable"),
        onClick: () => {
          // presentation-only: the host's onWork owns the door call; this
          // module mutates nothing and spawns nothing.
          if (work && work.enabled && typeof onWork === "function") onWork(bead);
        },
      }, "work"),
      ...(work && !work.enabled && work.disabledReason
        ? [el("span", { className: "work-disabled-reason", children: work.disabledReason }, "work-reason")]
        : []),
      // run state: verbatim, live-region semantics, never an invented word
      ...(shown != null
        ? [el("span", {
            role: "status", "data-run-state": shown,
            className: "work-run-state", "aria-live": "polite",
            children: shown }, "run-state")]
        : []),
      el("button", {
        children: shown === "cancel_requested" ? "Cancel (requested)" : "Cancel",
        disabled: !(shown != null && CANCELABLE.has(shown)),
        title: shown == null ? "no run state to cancel"
          : CANCELABLE.has(shown) ? "request cancel of the admitted run"
          : "cancel is only available while the run is admitted or running",
        onClick: () => {
          // presentation-only: the host's onCancel owns the request AND the
          // latch (cancelRequested prop); this module mutates nothing.
          if (shown != null && CANCELABLE.has(shown)
              && typeof onCancel === "function") onCancel();
        },
      }, "cancel"),
    ],
  }, "bot-action-panel");
}
