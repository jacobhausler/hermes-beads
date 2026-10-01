// tests/fixtures/shots/entry.jsx — browser entry for screenshots: mounts the
// SHIPPED WorkbenchApp (desktop/*.mjs, unmodified) over REAL captured native
// bd reads. window.__WB_STATE selects the scene. Evidence only — no product
// code lives here.
import React from "react";
import { createRoot } from "react-dom/client";
import world from "./out/world.json";
import { buildSnapshot, createWorkbenchState } from "../../../desktop/model.mjs";
import * as T from "../../../desktop/tree.mjs";
import * as B from "../../../desktop/blockers.mjs";
import { searchIssues } from "../../../desktop/search.mjs";
import { createHistoryStack } from "../../../desktop/history.mjs";
import { createDraftStore } from "../../../desktop/drafts.mjs";
import { askDecision } from "../../../desktop/bot_action.mjs";
import { WorkbenchApp } from "../../../desktop/workbench.mjs";

const IDS = world.ids;
const snapshot = buildSnapshot({ issues: world.list_all, ready: null,
  blocked: world.blocked, storeInfo: world.storeInfo }, { bound: 500 });
const ui = createWorkbenchState(snapshot);
const controller = T.createTreeController({ snapshot, ui });
const stack = createHistoryStack({ storeKey: snapshot.storeKey });
const provider = { storeInfo: world.storeInfo, run: (verb, ...rest) => {
  if (verb === "dep" && rest[0] === "tree") return world.deptrees[rest[1]];
  if (verb === "show") return world.shows[rest[0]];
  throw new Error(`unexpected provider query: ${verb}`);
} };
const mem = new Map();
const storage = { get: (k) => mem.get(k) ?? null, set: (k, v) => mem.set(k, v),
  remove: (k) => mem.delete(k), keys: () => [...mem.keys()] };
const session = { searchResults: null, card: null, draftStore: null, pane: "tree",
  showBot: false };

const state = window.__WB_STATE || "tree";
if (state === "search") {
  session.searchResults = searchIssues({ snapshot, query: "dashboard",
    searchRead: () => world.search.dashboard, showRead: (id) => world.shows[id],
    limit: 25 });
}
if (state === "blocker") {
  session.card = B.jumpToBlocker({ snapshot, ui, stack, state: {}, provider,
    targetId: IDS.taskB, pane: "tree" }).card;
}
if (state === "multiblocker") {
  session.card = B.jumpToBlocker({ snapshot, ui, stack, state: {}, provider,
    targetId: IDS.x10, pane: "tree" }).card;
}
if (state === "draft") {
  session.draftStore = createDraftStore({ storage });
  session.draftStore.saveDraft(world.storeInfo, IDS.conflict,
    "Support CSV alongside JSONL export; keep the current default.");
}
if (state === "bot") {
  session.showBot = true;
}

const app = () => React.createElement(WorkbenchApp, {
  snapshot, ui, controller, stack, session, storeInfo: world.storeInfo,
  reads: { searchRead: (q, lim) => world.search.dashboard,
           showRead: (id) => world.shows[id], provider },
  draftBeadId: state === "draft" ? IDS.conflict : null,
  botView: state === "bot" ? {
    ask: askDecision({ ok: false, error: "session_door_unqualified",
                       read_only: true, no_dispatch: true }),
    work: { present: true, enabled: false,
            disabledReason: "runner_unqualified: no host credential provisioned" },
  } : null,
  bindRerender: (fn) => { appRef.fn = fn; },
});
const appRef = { fn: null };
const root = createRoot(document.getElementById("root"));
root.render(app());
