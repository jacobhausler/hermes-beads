// desktop/workbench.mjs — hbl-pnu.4.6: THE shipped workbench root.
//
// Before this bead no desktop/*.mjs component attached the keyboard
// contract: the mounted smoke's WorkbenchApp was test-authored, so a human
// had nothing real to press keys in (mounted-smoke review
// defect 1). This module composes the REAL panels — Tree, RecordCard,
// BlockerCard, SearchPanel, SplitCompare, DraftPanel (drafts_view.mjs),
// botActionPanel — and binds the existing keymap/controller (tree.mjs
// resolveKey + createTreeController, reused unchanged) on the product
// root's onKeyDown. Typing in the description field steals letter keys:
// resolveKey's text-target swallow is the single gate (reimplemented
// nowhere).
//
// Laws: zero I/O (snapshot/ui/stack/session arrive injected; search
// activation delegates to enterSearchHit, the model-owned navigation
// entry — search never owns a parallel world); selection ≠ focus stays
// model.mjs's law; the bot panel mounts UNCONVERTED real jsx.
import { jsx } from "react/jsx-runtime";
import { useEffect, useReducer, useRef } from "react";
import { WORKBENCH_CSS } from "./workbench_css.mjs";
import { Tree, ShortcutHelp } from "./tree.mjs";
import { RecordCard } from "./record.mjs";
import { BlockerCard } from "./blockers.mjs";
import { SearchPanel, enterSearchHit } from "./search.mjs";
import { SplitCompare } from "./compare.mjs";
import { DraftPanel } from "./drafts_view.mjs";
import { botActionPanel, askDecision } from "./bot_action.mjs";

// WorkbenchApp({ snapshot, ui, controller, stack, session, storeInfo,
//   draftBeadId, botView, bindRerender })
//  - controller: tree.mjs createTreeController over THIS ui/snapshot;
//  - session: the app loader's live view box ({searchResults, card,
//    compare, draftStore, showBot}) — mutation is always followed by
//    rerender() (the root owns the only bump);
//  - bindRerender(rerender): hands the live re-render handle to the host
//    loader (optional; tests embed through it like the app shell does).
export function WorkbenchApp({ snapshot, ui, controller, stack, session,
  storeInfo, draftBeadId, botView = null, bindRerender }) {
  const [, bump] = useReducer((x) => x + 1, 0);
  const rootRef = useRef(null);
  const rerender = () => bump();
  if (typeof bindRerender === "function") bindRerender(rerender);
  const focusId = ui.focus ?? ui.selection;
  const reduced = typeof window !== "undefined" && window.matchMedia
    ? window.matchMedia("(prefers-reduced-motion: reduce)").matches
    : false;
  // hbl-pnu.2.10 (F3): the keyboard-focus MARKER and document focus must
  // never diverge — whenever the controller moves the marker, the row takes
  // real document focus (WCAG 2.4.7: the ring paints on activeElement).
  // A text target keeps focus (same swallow law the keymap enforces).
  useEffect(() => {
    const rootEl = rootRef.current;
    if (!rootEl || typeof document === "undefined") return;
    // Roving tabindex law: document focus follows the tab stop (the row
    // the cursor is on), so the ring paints where the arrows went. Only
    // when focus is ALREADY inside the tree — never steal it from elsewhere.
    const tree = rootEl.querySelector('[role="tree"]');
    const marker = rootEl.querySelector('[role="treeitem"][tabindex="0"]')
      ?? rootEl.querySelector('[data-keyboard-focus="true"]');
    if (!marker) return;
    if (document.activeElement === marker) return;
    if (!(tree && tree.contains(document.activeElement))
        && rootEl.querySelector('[data-keyboard-focus="true"]') !== marker) return;
    const ae = document.activeElement;
    if (ae && (ae.tagName === "TEXTAREA" || ae.tagName === "INPUT"
        || ae.isContentEditable)) return;
    marker.focus({ preventScroll: false });
  });
  return jsx("div", {
    id: "workbench",
    ref: rootRef,
    "data-motion": reduced ? "reduce" : "no-preference",
    onKeyDown: (ev) => {
      // ONE binding point for the whole keymap: arrows/Enter/arrow-left/
      // arrow-right/Home/End/Alt+arrows/? resolve through the shared
      // controller; text targets are swallowed inside resolveKey itself.
      const cmd = controller.press(ev);
      if (cmd) rerender();
    },
    children: [
      // hbl-pnu.2.10 (F1): THE shipped stylesheet, rendered exactly once so
      // every host that mounts this root gets the visual layer for free.
      jsx("style", { children: WORKBENCH_CSS }, "workbench-css"),
      jsx(Tree, { snapshot, ui, scheduler: null }, "tree"),
      focusId != null && snapshot.nodes.has(focusId)
        ? jsx(RecordCard, { snapshot, id: focusId }, "record") : null,
      session.card
        ? jsx(BlockerCard, { card: session.card, onReturn: rerender, snapshot }, "blocker") : null,
      session.searchResults
        ? jsx(SearchPanel, {
            results: session.searchResults, cursor: 0,
            onActivate: (hit, i) => {
              enterSearchHit({ results: session.searchResults, index: i,
                snapshot, workbench: ui, history: stack });
              session.searchResults = null;
              rerender();
            },
          }, "search") : null,
      session.compare
        ? jsx(SplitCompare, session.compare, "compare") : null,
      session.draftStore
        ? jsx(DraftPanel, { store: session.draftStore, storeInfo,
            beadId: draftBeadId }, "draft") : null,
      jsx("div", { id: "bot-panel-slot", children: session.showBot
        ? botActionPanel(botView ?? {
            ask: askDecision({ ok: false, error: "session_door_unqualified",
              read_only: true, no_dispatch: true }),
            work: { present: true, enabled: false,
              disabledReason: "runner door (hbl-pnu.3.3) not bound" },
          }) : null }, "bot"),
      controller.helpOpen ? jsx(ShortcutHelp, {}, "help") : null,
    ].filter(Boolean),
  }, "workbench");
}
