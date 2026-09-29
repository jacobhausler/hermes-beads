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
// hbl-pnu.2.11 (user doors): a human — not only the host — reaches search
// and the blocker card. The root owns the ONLY new state: the search query
// input, the listbox roving cursor, and where document focus must go next
// (focusReq/originReq). Everything else stays the same single truth:
//  - `reads` ({ searchRead, showRead, provider }) is the injected read
//    facade; absent => the search door and the blockers door render
//    PRESENT-BUT-DISABLED with a visible reason (never a throw, never an
//    invisible affordance). Zero I/O in this module is preserved: it only
//    calls the injected facade (searchIssues) / provider (jumpToBlocker).
//  - Return (button) and Alt+ArrowLeft while a card is open resolve to
//    blockers.mjs returnFromCard (ONE stack step) + session.card = null +
//    document focus back to the origin row — the old onReturn=rerender made
//    the card immortal.
//  - listbox arrows drive the roving cursor AND document focus together;
//    letters in the search input never reach the tree keymap (the single
//    resolveKey/isTextTarget swallow).
//
// Laws: zero I/O (snapshot/ui/stack/session/reads arrive injected; search
// activation delegates to enterSearchHit, the model-owned navigation
// entry — search never owns a parallel world); selection ≠ focus stays
// model.mjs's law; the bot panel mounts UNCONVERTED real jsx.
import { jsx } from "react/jsx-runtime";
import { useEffect, useReducer, useRef, useState } from "react";
import { WORKBENCH_CSS } from "./workbench_css.mjs";
import { Tree, ShortcutHelp, resolveKey } from "./tree.mjs";
import { RecordCard } from "./record.mjs";
import { BlockerCard, BlockersDoor, blockersFor, jumpToBlocker, returnFromCard } from "./blockers.mjs";
import { SearchPanel, searchIssues, enterSearchHit } from "./search.mjs";
import { SplitCompare } from "./compare.mjs";
import { DraftPanel } from "./drafts_view.mjs";
import { botActionPanel, askDecision } from "./bot_action.mjs";

// WorkbenchApp({ snapshot, ui, controller, stack, session, storeInfo,
//   draftBeadId, botView, bindRerender, reads })
//  - controller: tree.mjs createTreeController over THIS ui/snapshot;
//  - session: the app loader's live view box ({searchResults, card,
//    compare, draftStore, showBot}) — mutation is always followed by
//    rerender() (the root owns the only bump);
//  - bindRerender(rerender): hands the live re-render handle to the host
//    loader (optional; tests embed through it like the app shell does);
//  - reads: { searchRead, showRead, provider } (hbl-pnu.2.11) — the read
//    facade the search input and the blockers door run through. Missing
//    member => that door is present-but-disabled with a visible reason.
export function WorkbenchApp({ snapshot, ui, controller, stack, session,
  storeInfo, draftBeadId, botView = null, bindRerender, reads = null }) {
  const [, bump] = useReducer((x) => x + 1, 0);
  const rootRef = useRef(null);
  const rerender = () => bump();
  if (typeof bindRerender === "function") bindRerender(rerender);
  const focusId = ui.focus ?? ui.selection;
  const reduced = typeof window !== "undefined" && window.matchMedia
    ? window.matchMedia("(prefers-reduced-motion: reduce)").matches
    : false;
  // ---- hbl-pnu.2.11 root-owned UI state (the ONLY new state) ---------------
  // roving cursor of the search listbox + where document focus must land on
  // the next render (a search hit, or the origin row after a card return).
  const [searchCursor, setSearchCursor] = useState(0);
  const focusReq = useRef(null);   // number: focus #search-hit-<i>
  const inputReq = useRef(false);  // true: focus #search-input
  const originReq = useRef(null);  // string bead id: focus that tree row
  const searchReady = typeof reads?.searchRead === "function";
  const providerReady = reads?.provider?.run != null;
  // One decision point for document focus: a pending request always beats
  // the tree marker law (the two must never fight over the ring).
  useEffect(() => {
    const rootEl = rootRef.current;
    if (!rootEl || typeof document === "undefined") return;
    if (inputReq.current) {
      inputReq.current = false;
      const input = rootEl.querySelector("#search-input");
      if (input && !input.disabled) input.focus({ preventScroll: false });
      return;
    }
    if (focusReq.current != null) {
      const i = focusReq.current;
      focusReq.current = null;
      const hit = rootEl.querySelector(`#search-hit-${i}`);
      if (hit) hit.focus({ preventScroll: false });
      return; // the listbox owns document focus until the door closes
    }
    if (originReq.current != null) {
      const id = originReq.current;
      originReq.current = null;
      const row = rootEl.querySelector(`[data-tree-row="${id}"]`);
      if (row) row.focus({ preventScroll: false });
      return;
    }
    // hbl-pnu.2.10 (F3): the keyboard-focus MARKER and document focus must
    // never diverge — whenever the controller moves the marker, the row takes
    // real document focus (WCAG 2.4.7: the ring paints on activeElement).
    // A text target keeps focus (same swallow law the keymap enforces).
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
    // hbl-pnu.2.11: a roving listbox cursor keeps document focus while the
    // search door is open (the same keep-focus law the input/draft field
    // obey) — the tree marker must never steal it back mid-walk.
    const activePanel = rootEl.querySelector("#search-panel");
    if (session.searchResults && activePanel && activePanel.contains(ae)) return;
    marker.focus({ preventScroll: true });
  });

  // ---- the doors -------------------------------------------------------------
  // Enter in the input: ONE bounded searchIssues through the injected facade;
  // the listbox appears with its roving cursor at hit 1 and focus follows.
  const runSearch = (query) => {
    const q = String(query ?? "").trim();
    if (!q || !searchReady) return; // disabled door: honest no-op, never a throw
    session.searchResults = searchIssues({
      snapshot, query: q, searchRead: reads.searchRead,
      showRead: reads.showRead, limit: 25,
    });
    setSearchCursor(0);
    focusReq.current = 0;
    rerender();
  };
  const activateHit = (hit, i) => {
    enterSearchHit({ results: session.searchResults, index: i,
      snapshot, workbench: ui, history: stack });
    session.searchResults = null;
    focusReq.current = null;
    rerender();
  };
  const openCardFromRow = (id) => {
    if (!providerReady || id == null) return; // disabled: nothing invisible fires
    let blocked = false;
    try { blocked = blockersFor(snapshot, id).length > 0; } catch { blocked = false; }
    if (!blocked) return; // honest no-op on an unblocked row
    // The return needs somewhere to return TO: push ONE base bundle per
    // open (model save + external-stack base), so ONE Return is exactly one
    // stack step onto the state captured HERE. jumpToBlocker pushes the
    // matching forward entry itself.
    if (typeof ui.save === "function") ui.save();
    stack.push({ storeKey: ui.storeKey, beadId: ui.selection ?? ui.focus,
      pane: ui.pane, selection: ui.selection, focus: ui.focus,
      expanded: [...ui.expanded].sort(), scroll: 0, filter: null,
      search: null, tab: null });
    const { card } = jumpToBlocker({ snapshot, ui, stack, state: {},
      provider: reads.provider, targetId: id, pane: ui.pane });
    session.card = card;
    rerender();
  };
  const openBlockersForSelection = () => openCardFromRow(ui.selection);
  const focusSearch = () => {
    if (!searchReady) return; // disabled input cannot take focus — no-op
    inputReq.current = true;
    rerender();
  };
  // Return from an open card: ONE stack step through blockers.mjs, clear the
  // session card, and put document focus back on the origin row (defect 1:
  // the old onReturn=rerender did none of this).
  const doReturn = () => {
    returnFromCard({ stack, ui, state: {} });
    session.card = null;
    // Document focus lands on the ROVING TAB STOP (the cursor row the card
    // was opened from). Focusing the model's parked focus row instead left
    // the ring on one row while the next arrow moved from another (2.11
    // real-Chrome finding).
    const origin = ui.selection ?? ui.focus;
    if (origin != null) originReq.current = origin;
    rerender();
  };

  // plain app gestures ('/' and 'b'): the bare-letter law keeps them OUT of
  // the data KEYMAP (test_tree's binding audit), so the root resolves them
  // itself — under the SAME swallow: we ask resolveKey (isTextTarget's single
  // gate) whether a plain ArrowDown would resolve for this event's target;
  // in a text target or during IME composition it returns null, and so do we.
  const editorOrComposing = (ev) =>
    resolveKey({ key: "ArrowDown", altKey: false, ctrlKey: false,
      metaKey: false, isComposing: ev.isComposing === true,
      target: ev.target }) === null;

  return jsx("div", {
    id: "workbench",
    ref: rootRef,
    "data-motion": reduced ? "reduce" : "no-preference",
    onKeyDown: (ev) => {
      // ONE binding point for the whole keymap. The app-level doors (card
      // return, focus-search, open-blockers) resolve first; everything else
      // (arrows/Enter/arrow-left/arrow-right/Home/End/Alt+arrows/?) resolves
      // through the shared controller; text targets are swallowed inside
      // resolveKey itself.
      const cmd = resolveKey(ev);
      if (session.card && cmd === "history-back") {
        // a card open makes the app-back gesture a card return, not a bare
        // model-stack step (returnFromCard walks ui.back() in lockstep).
        ev.preventDefault?.();
        doReturn();
        return;
      }
      if (cmd === "focus-search") { ev.preventDefault?.(); focusSearch(); return; }
      if (cmd === "open-blockers") { ev.preventDefault?.(); openBlockersForSelection(); return; }
      if (cmd === null && !ev.altKey && !ev.ctrlKey && !ev.metaKey
          && !editorOrComposing(ev)) {
        if (ev.key === "/") { ev.preventDefault?.(); focusSearch(); return; }
        if (ev.key === "b") { ev.preventDefault?.(); openBlockersForSelection(); return; }
      }
      const c = controller.press(ev);
      if (c) rerender();
    },
    children: [
      // hbl-pnu.2.10 (F1): THE shipped stylesheet, rendered exactly once so
      // every host that mounts this root gets the visual layer for free.
      jsx("style", { children: WORKBENCH_CSS }, "workbench-css"),
      // hbl-pnu.2.11 search door: a labelled input + the results listbox.
      // Enter runs searchIssues through the facade; the panel's own option
      // handlers drive the roving cursor (onCursor) and document focus
      // (focusHit) together. Missing facade => disabled + visible reason.
      jsx("div", { id: "search-door", role: "group",
        "aria-label": "Search",
        children: [
          jsx("input", {
            id: "search-input", type: "text",
            "aria-label": "Search beads", role: "searchbox",
            disabled: !searchReady,
            placeholder: searchReady ? "Search beads — Enter to run" : "search unavailable",
            onKeyDown: (ev) => {
              if (ev.key === "Enter") { ev.preventDefault?.(); runSearch(ev.target.value); }
            },
          }, "si"),
          !searchReady
            ? jsx("p", { id: "search-disabled-reason",
                children: "search needs an injected search read (reads.searchRead) — door disabled, nothing hidden" }, "sdr")
            : null,
        ].filter(Boolean),
      }, "search-door"),
      jsx(Tree, { snapshot, ui, scheduler: null }, "tree"),
      // hbl-pnu.2.11: the visible blockers door for the selected row (the
      // ONLY user-reachable way to open a card besides Ctrl+b, besides
      // host injection).
      jsx(BlockersDoor, { snapshot, id: ui.selection, providerReady,
        onOpen: openCardFromRow }, "blockers-door"),
      focusId != null && snapshot.nodes.has(focusId)
        ? jsx(RecordCard, { snapshot, id: focusId }, "record") : null,
      session.card
        ? jsx(BlockerCard, { card: session.card, onReturn: doReturn, snapshot }, "blocker") : null,
      session.searchResults
        ? jsx(SearchPanel, {
            results: session.searchResults,
            cursor: searchCursor,
            onCursor: (i) => { setSearchCursor(i); },
            focusHit: (i) => { focusReq.current = i; rerender(); },
            onActivate: activateHit,
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
