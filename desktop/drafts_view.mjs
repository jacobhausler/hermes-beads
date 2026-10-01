// desktop/drafts_view.mjs — the SHIPPED draft panel component.
//
// drafts.mjs owns the draft store + capability gates and is pinned
// import-free by its source audit, so the React view lives here: a thin
// presentation of the real store (createDraftStore/editDecision/
// draftLimitationNotice are imported UNCHANGED — no parallel logic).
// The description field is a real <textarea>: while it owns focus, bare
// keys (letters, '?', the plain arrows) must reach the widget, never the
// tree keymap (desktop/tree.mjs resolveKey text-target swallow — this
// component simply does not stop propagation and does not re-implement it).
//
// Purity: no I/O; the draft store + storeInfo are injected by the app
// loader; the only import is the jsx-runtime the app loader maps.
import { jsx } from "react/jsx-runtime";
import { useState } from "react";
import { editDecision, draftLimitationNotice } from "./drafts.mjs";

export function DraftPanel({ store, storeInfo, beadId, onTrack = null }) {
  const [, bump] = useState(0);
  const d = store.getDraft(storeInfo, beadId);
  const dec = editDecision();
  const notice = draftLimitationNotice();
  const warn = store.warning();
  return jsx("section", { role: "group", "aria-label": "Draft", children: [
    jsx("div", { role: "status", "data-durability": store.durability(),
      children: `durability: ${store.durability()}` }, "dur"),
    warn ? jsx("div", { role: "alert", "data-warning-kind": warn.kind,
      children: warn.text }, "warn") : null,
    jsx("div", { id: "draft-text", "data-bead": beadId,
      children: d ? d.text : "\u2014 no draft \u2014" }, "text"),
    // The description editor: a real textarea — typing steals letter keys
    // from the tree keymap via resolveKey's text-target swallow.
    jsx("textarea", {
      id: "draft-input",
      "aria-label": "Draft description",
      defaultValue: d ? d.text : "",
      onChange: (ev) => {
        store.saveDraft(storeInfo, beadId, ev.target.value);
        if (onTrack) { try { onTrack(); } catch { /* telemetry never breaks a save */ } }
        bump((x) => x + 1);
      },
    }, "input"),
    dec.saveContent.enabled
      ? jsx("button", { type: "button", id: "draft-save",
          children: "Save" }, "save")
      : jsx("button", { type: "button", id: "draft-save", disabled: true,
          "aria-disabled": "true", title: dec.saveContent.disabledReason,
          children: "Save (disabled)" }, "save"),
    notice.saveDisabled
      ? jsx("div", { id: "save-limitation", role: "note",
          children: notice.text }, "limit") : null,
  ].filter(Boolean) }, "draft-panel");
}
