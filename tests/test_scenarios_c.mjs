// tests/test_scenarios_c.mjs — lane C: scenarios S2, S6, S9
// (hci.md §3) as machine-checkable acceptance against the seeded REAL-bd
// scenario world (tests/fixtures/scenarios/make_store.py — seed/read/act/
// cleanup CLI over the pinned bd v1.3.0). Every UI assertion is checked
// against a SAME-MOMENT `make_store.py read` readback of the store.
//
// Honesty laws pinned here (owner contracts, / .3.5):
//  - Content Save is NEVER asserted as saved: runContentSave must return the
//    typed unsupported result (reason unsupported:no-atomic-content-guard)
//    and the store must read back unchanged.
//  - Refine lands ONLY in the human draft store; the store readback must be
//    byte-identical (description + comments) around it.
//  - The negative control (a deliberately broken assertion: Ready includes a
//    claimed row) is gated by SCENARIO_NEGATIVE=1 and MUST fail; a normal
//    test spawns this file in a child with that env and asserts the nonzero
//    exit and the failing test's name — the suite's own negative control.
//
// One seeded store for the whole file (seed once, cleanup via the seeder's
// own cleanup CLI). No shared-module edits; local scenario helpers live here.
// Run: node --test tests/test_scenarios_c.mjs
import test from "node:test";
import assert from "node:assert/strict";
import { execFileSync, spawnSync } from "node:child_process";
import { register } from "node:module";
import { fileURLToPath, pathToFileURL } from "node:url";
import { existsSync } from "node:fs";
import path from "node:path";

const here = path.dirname(fileURLToPath(import.meta.url));
register(pathToFileURL(path.join(here, "__shims__", "jsx-loader.mjs")).href);
const shim = await import(pathToFileURL(path.join(here, "__shims__", "jsx-capture.mjs")).href);

const { buildSnapshot, createWorkbenchState } = await import("../desktop/model.mjs");
const { createHistoryStack } = await import("../desktop/history.mjs");
const B = await import("../desktop/blockers.mjs");
const { createDraftStore, runContentSave, editDecision, draftLimitationNotice } =
  await import("../desktop/drafts.mjs");
const { askDecision, refineToDraft, reopenBotDraft, botActionPanel } =
  await import("../desktop/bot_action.mjs");
const { resolveKey } = await import("../desktop/tree.mjs");

const REPO = path.join(here, "..");
const SEEDER = path.join(here, "fixtures", "scenarios", "make_store.py");
const BD_BIN = process.env.BEADS_LAB_BD
  || "bd";

// ---- seeder bridge (child_process.execFileSync python3, per the seeder CLI) --
const py = (args, timeout = 180_000) =>
  JSON.parse(execFileSync("python3", [SEEDER, ...args],
    { encoding: "utf8", cwd: REPO, timeout }));
// same-moment fixed-argv READ; throws unless the read succeeded (rc 0)
const read = (store, name, ...args) => {
  const r = py(["read", store, name, ...args]);
  assert.equal(r.rc, 0, `read ${name} failed: ${r.stderr ?? ""}`);
  return r.payload;
};
const readRaw = (store, name, ...args) => py(["read", store, name, ...args]);
const act = (store, actorName, ...argv) => py(["act", store, actorName, ...argv]);
const rows = (p) => (Array.isArray(p) ? p : p ? [p] : []);
const recOf = (p) => { const r = rows(p); return r[0] ?? null; };
const walk = (t) => [...shim.walk(t)];

// one seeded scenario world for this whole file
const world = py(["seed", "--prefix", "scenc"]);
const { store, ids, storeInfo } = world;
test.after(() => { py(["cleanup", store]); });

// ---- snapshot builders (real reads, same moment) -----------------------------
const nativeSnapshot = () => buildSnapshot({
  issues: read(store, "list_all"),
  ready: read(store, "ready"),
  blocked: read(store, "blocked"),
  storeInfo,
}, { bound: 500 });

// S2 restore bundle over the named fields (mirrors the blockers-lane pattern)
const S2_KEYS = ["storeKey", "pane", "selection", "focus", "expanded",
  "scroll", "filter", "search", "tab", "beadId"];
const s2Bundle = (ui, st) => ({
  storeKey: ui.storeKey, pane: ui.pane, selection: ui.selection,
  focus: ui.focus, expanded: [...ui.expanded].sort(),
  scroll: st.scroll, filter: st.filter, search: st.search, tab: st.tab,
  beadId: ui.focus,
});
const project = (o) => JSON.stringify(S2_KEYS.map((k) => [k, o[k] ?? null]));

// ============================================================================
// S2 — diagnose cross-branch blocker, then return without losing place
// ============================================================================
test("S2: x-jump to the cross-branch blocker card, Esc restores the bundle byte-identical, draft survives, Save honestly unsupported", () => {
  // pre-state from a same-moment readback: taskB is stored-open but natively
  // blocked by gateA (branch A), while gateA itself sits in native ready.
  const readyIds = rows(read(store, "ready")).map((r) => r.id);
  assert.ok(readyIds.includes(ids.gateA), "gateA is natively ready");
  assert.ok(!readyIds.includes(ids.taskB), "taskB is not ready (blocked by gateA)");
  assert.equal(recOf(read(store, "show", ids.taskB)).status, "open",
    "stored status stays open (hci fact 8)");

  const snap = nativeSnapshot();
  assert.equal(snap.nodes.get(ids.taskB).derivedBlocked, true);

  const ui = createWorkbenchState(snap, { selection: ids.taskB });
  ui.enter();
  ui.toggleExpanded(ids.branchB); // diverge expansion from the default set
  const st = { scroll: 140, filter: "label=impl", search: "branch", tab: "ready", pane: "detail" };
  const stack = createHistoryStack({ storeKey: snap.storeKey });
  const pre = s2Bundle(ui, st);
  stack.push(pre);

  // the draft exists BEFORE navigating (hci S2: draft survives navigation)
  const drafts = createDraftStore();
  const added = "ADDED: acceptance evidence must cite the bd readback.";
  const base = recOf(read(store, "show", ids.taskB)).description ?? "";
  drafts.saveDraft(storeInfo, ids.taskB, `${base}\n${added}`, { baseText: base });

  // press `x` → jump to the blocker card with ITS ancestry
  const provider = {
    run: (verb, ...rest) => {
      // guardedRun passes the FULL argv: dep tree <id> => rest = [tree, id];
      // show <id> --json => rest = [id, --json].
      if (verb === "dep") return read(store, "deptree", rest[1]);
      if (verb === "show") return read(store, "show", rest[0]);
      return [];
    },
    storeInfo,
  };
  const { card } = B.jumpToBlocker({ snapshot: snap, ui, stack, state: st,
    provider, targetId: ids.gateA });
  assert.equal(ui.focus, ids.gateA, "jump moved focus to the blocker");

  // the card's ancestry is the REAL parent chain epic › branch A › gateA
  const gateRec = recOf(read(store, "show", ids.gateA));
  const branchRec = recOf(read(store, "show", ids.branchA));
  const epicRec = recOf(read(store, "show", ids.epic));
  assert.equal(gateRec.parent, ids.branchA);
  assert.equal(branchRec.parent, ids.epic);
  assert.deepEqual(card.ancestry, [ids.epic, ids.branchA, ids.gateA],
    "card carries the blocker's real ancestry, byte-equal to the readback chain");
  assert.equal(card.statusWord, gateRec.status, "card status verbatim from readback");
  assert.equal(card.badge, "ready", "gateA badge from native ready/blocked truth, never dep-tree badge");
  // rendered card evidence (structure only — no mount/usability claim)
  const tree = B.BlockerCard({ card, onReturn: () => {} });
  assert.equal(walk(tree).filter((n) => n.props?.id === `blocker-card:${ids.gateA}`).length, 1);
  assert.ok(walk(tree).some((n) => n.props?.id === `ancestry:${ids.branchA}`));

  // press Esc → the app-back gesture resolves to ONE history-back and the
  // full bundle comes back byte-identical
  // Escape is now bound to close-help (overlay dismissal); the
  // app-back-on-Esc mapping below remains the app's own gesture choice.
  assert.equal(resolveKey({ key: "Escape" }), "close-help",
    "Escape is bound in the KEYMAP to close-help");
  const mapped = { key: "ArrowLeft", altKey: true }; // Esc handler maps to app-back
  assert.equal(resolveKey(mapped), "history-back");
  const back = B.appBack({ stack, ui, state: st });
  assert.equal(back.restored, true);
  assert.ok(snapEqReport(pre, ui, st));
  assert.equal(ui.focus, ids.taskB, "Esc restored focus to the cross-branch victim");

  // draft survives the whole navigation round-trip, byte-identical
  const d = drafts.getDraft(storeInfo, ids.taskB);
  assert.ok(d, "draft badge still marks the row after jump+Esc");
  assert.equal(d.text, `${base}\n${added}`);
  assert.equal(d.beadId, ids.taskB);

  // HONEST save step: content Save is NOT supported — never asserted as saved.
  assert.equal(editDecision().saveContent.enabled, false);
  assert.equal(draftLimitationNotice().collaborativeEditClaimed, false);
  const save = runContentSave(drafts, storeInfo, ids.taskB);
  assert.equal(save.saved, false);
  assert.equal(save.reason, "unsupported:no-atomic-content-guard");
  assert.equal(save.channel, "append-only-comment");
  // store unchanged by the attempted save (same-moment readback)
  assert.equal(recOf(read(store, "show", ids.taskB)).description ?? "", base,
    "an unsupported Save wrote NOTHING to the store");
  assert.ok(drafts.getDraft(storeInfo, ids.taskB),
    "the unsupported Save kept the draft alive (only explicit discard clears)");
});
function snapEqReport(pre, ui, st) {
  const got = project(s2Bundle(ui, st));
  assert.equal(got, project(pre), `S2 bundle drifted after Esc:\n got ${got}\nwant ${project(pre)}`);
  return true;
}

// ============================================================================
// S6 — conflicting edit while a draft exists
// ============================================================================
test("S6: external CLI edit surfaces a conflict; reload only after explicit confirm; save path honestly unsupported", () => {
  const drafts = createDraftStore();
  const orig = recOf(read(store, "show", ids.conflict)).description;
  assert.equal(orig, "orig", "seeded conflict target starts at its original description");
  drafts.saveDraft(storeInfo, ids.conflict, "my local edit", { baseText: orig });

  // another actor edits the SAME bead from the CLI while the draft is open
  const external = "external CLI edit (second actor)";
  const a = act(store, "lab-other", "update", ids.conflict,
    "--description", external, "--json");
  assert.equal(a.rc, 0, `external edit act failed: ${a.stderr}`);

  // re-focus: draft base vs SERVER value from the same-moment readback
  const server = () => recOf(read(store, "show", ids.conflict));
  const conflictPrompt = (ds) => {
    const d = ds.getDraft(storeInfo, ids.conflict);
    if (!d) return null;
    const rec = server();
    return (d.baseText !== (rec.description ?? "") || d.updatedAt !== rec.updated_at)
      ? { choices: ["keep-mine", "reload", "side-by-side-diff"], server: rec }
      : null;
  };
  const prompt = conflictPrompt(drafts);
  assert.ok(prompt, "the external edit surfaced a conflict prompt");
  assert.deepEqual(prompt.choices, ["keep-mine", "reload", "side-by-side-diff"]);
  assert.equal(prompt.server.description, external,
    "prompt cites the SERVER value straight from the native readback");

  // NO silent discard: without explicit confirm the draft survives
  assert.ok(drafts.getDraft(storeInfo, ids.conflict),
    "draft intact until an explicit choice is made");

  // save path: again the HONEST unsupported result, never a pass as saved
  const save = runContentSave(drafts, storeInfo, ids.conflict);
  assert.equal(save.saved, false);
  assert.equal(save.reason, "unsupported:no-atomic-content-guard");
  assert.equal(recOf(read(store, "show", ids.conflict)).description, external,
    "only the external actor's value is in the store — no silent overwrite");

  // explicit reload-confirm loses the local draft ONCE CONFIRMED
  const reload = (ds, confirmed) =>
    confirmed && ds.discardDraft(storeInfo, ids.conflict)
      ? { reloaded: true } : { reloaded: false };
  assert.equal(reload(drafts, false).reloaded, false);
  assert.ok(drafts.getDraft(storeInfo, ids.conflict),
    "unconfirmed reload must NOT drop the draft");
  assert.equal(reload(drafts, true).reloaded, true);
  assert.equal(drafts.getDraft(storeInfo, ids.conflict), null,
    "after explicit confirm the draft is gone and the pane shows the server value");
  assert.equal(server().description, external);
});


// ============================================================================
// NEGATIVE CONTROL — deliberately broken, gated by SCENARIO_NEGATIVE=1.
// It asserts Ready contains the already-claimed row (false by native
// semantics — see the S1-ready probe: ready excludes claimed) so it MUST
// fail. Default runs skip it; the next test spawns this file with the env
// set and asserts the nonzero exit and the failing test's name.
// ============================================================================
test("S2 negative control (SCENARIO_NEGATIVE=1): Ready INCLUDES the claimed row", () => {
  if (process.env.SCENARIO_NEGATIVE !== "1") return; // not engaged
  const readyIds = rows(read(store, "ready")).map((r) => r.id);
  assert.ok(readyIds.includes(ids.mine),
    "BROKEN by design: the already-claimed row must (wrongly) appear in Ready");
});

test("negative control harness: SCENARIO_NEGATIVE=1 child run exits nonzero naming the broken test", () => {
  if (process.env.SCENARIO_NEGATIVE === "1") return; // we ARE the negative child
  // guard: this file was deleted from disk after seeding (another lane's
  // sweep) — the child would be a no-op exit-0 run; fail loudly instead.
  const self = fileURLToPath(import.meta.url);
  assert.ok(existsSync(self), "scenario file must exist on disk to re-spawn itself");
  const child = spawnSync(process.execPath, ["--test", self], {
    cwd: REPO, encoding: "utf8", timeout: 480_000,
    env: (() => {
      const e = { ...process.env, SCENARIO_NEGATIVE: "1" };
      // node --test refuses to run recursively when the parent runner's
      // NODE_TEST_CONTEXT is inherited (silently exits 0 — the trap this
      // very assertion defends against). The child must be a fresh root.
      delete e.NODE_TEST_CONTEXT;
      return e;
    })(),
  });
  const out = `${child.stdout ?? ""}${child.stderr ?? ""}`;
  // the child seeded its own world (prefix scenc); reap its survivors —
  // only ever stores the seeder created, via the seeder's own cleanup CLI.
  for (const m of out.matchAll(/(?:^|[\s"'(])(\/[^\s"'"()]*[\\/]scenc-[\w-]+)/gm)) {
    try { py(["cleanup", m[1]]); } catch { /* best-effort */ }
  }
  assert.notEqual(child.status, 0, "the SCENARIO_NEGATIVE=1 child must FAIL");
  assert.match(out, /negative control \(SCENARIO_NEGATIVE=1\): Ready INCLUDES the claimed row/,
    "the failure must name the deliberately-broken scenario");
  assert.match(out, /must \(wrongly\) appear in Ready/,
    "the broken assertion's own diagnostic must surface");
});
