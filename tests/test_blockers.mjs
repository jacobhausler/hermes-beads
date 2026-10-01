// tests/test_blockers.mjs — cross-branch blocker jump with
// blocker CARD + one-press return.
//
// Evidence layers (kept honest, the boundary contract):
//  - SYNTHETIC fixtures: S2 full-context restore equality, card composition,
//    read-only refusal, truth-vs-badge (dep-tree READY badge never trusted).
//  - GENUINE native (disposable store, real bd): external close flips the
//    derived-blocked badge on refresh with NO auto-jump.
//  - JSX shim: component structure evidence ONLY — no mounting, no usability
//    claim.
// Run: node --test tests/test_blockers.mjs
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync, mkdtempSync, rmSync } from "node:fs";
import { execFileSync } from "node:child_process";
import { register } from "node:module";
import { pathToFileURL } from "node:url";
import path from "node:path";

const here = path.dirname(pathToFileURL(process.argv[1] ?? import.meta.url).pathname)
  || new URL(".", import.meta.url).pathname;
register(new URL("./__shims__/jsx-loader.mjs", import.meta.url).href);
const shim = await import(new URL("./__shims__/jsx-capture.mjs", import.meta.url).href);

const { buildSnapshot, createWorkbenchState } = await import("../desktop/model.mjs");
const { createHistoryStack } = await import("../desktop/history.mjs");
const B = await import("../desktop/blockers.mjs");

const BD_BIN = process.env.BEADS_LAB_BD
  || "bd";
const FIXTURE_ROOT = new URL("./fixtures/blockers-runtime", import.meta.url).pathname;

// ---- helpers ---------------------------------------------------------------
const fixture = (name) =>
  JSON.parse(readFileSync(new URL(`./fixtures/blockers/${name}`, import.meta.url), "utf8"));

const baseReads = (over = {}) => ({
  issues: [], ready: [], blocked: [],
  storeInfo: { workspace: "/lab/store", db: "/lab/store/.beads/lab.db" },
  ...over,
});

const walk = (t) => [...shim.walk(t)];
const findById = (tree, id) =>
  walk(tree).filter((n) => typeof n === "object" && n.props?.id === id);
const textIn = (tree, s) =>
  walk(tree).some((n) => typeof n === "string" && n.includes(s));

// full S2 bundle: selection/focus/expansion/filter/search/tab/pane/scroll
const s2Bundle = (ui, st, over = {}) => ({
  storeKey: ui.storeKey, pane: ui.pane,
  selection: ui.selection, focus: ui.focus,
  expanded: [...ui.expanded].sort(),
  scroll: st.scroll, filter: st.filter, search: st.search, tab: st.tab,
  beadId: ui.focus, ...over,
});
// order-insensitive-of-extra-fields compare over the named S2 fields only
const S2_KEYS = ["storeKey", "pane", "selection", "focus", "expanded",
  "scroll", "filter", "search", "tab", "beadId"];
const project = (o) => JSON.stringify(S2_KEYS.map((k) => [k, o[k] ?? null]));
const snapEq = (a, b) => project(a) === project(b);

// ---- disposable native store (real bd, never the planning store) -----------
// The embedded-dolt home binds to the nearest git root: each store owns a
// private `git init` so it cannot share the parent repo's dolt databases.
function makeNativeStore() {
  const d = mkdtempSync(path.join(FIXTURE_ROOT, "store-"));
  execFileSync("git", ["init", "-q", "."], { cwd: d });
  execFileSync(BD_BIN, ["init", "--prefix", "blj"], { cwd: d });
  return d;
}
function bd(store, ...args) {
  const out = execFileSync(BD_BIN, [...args, "--json"],
    { cwd: store, encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] });
  return out.trim() ? JSON.parse(out) : null;
}
const idOf = (rows) => (Array.isArray(rows) ? rows[0] : rows)["id"];

function seedCrossBranch(store) {
  // cross-branch shape: blockers live under a DIFFERENT epic than the victim.
  // epicA > mid > victim ; epicB side holds the blockers; epicA itself is
  // blocked (inheritance); one CLOSED dep must drop out of the card.
  const mk = (title, extra = []) => idOf(bd(store, "create", title, "--json", ...extra));
  const epicA = mk("EPIC A");
  const epicB = mk("EPIC B");
  const mid = mk("A mid", ["--parent", epicA]);
  const victim = mk("cross-branch victim", ["--parent", mid]);
  const other = mk("A sibling (no blockers)", ["--parent", epicA]);
  const b1 = mk("B-side live blocker", ["--parent", epicB]);
  const b2 = mk("B-side closed blocker", ["--parent", epicB]);
  const epicDep = mk("epic-level blocker");
  bd(store, "dep", "add", victim, b1);   // direct blocks edge
  bd(store, "dep", "add", victim, b2);   // closed below — must not block
  bd(store, "dep", "add", epicA, epicDep); // inherited via parent FIELD
  bd(store, "close", b2, "--reason", "already done", "--json");
  return { epicA, epicB, mid, victim, other, b1, b2, epicDep };
}

// ============================================================================
// 1. blocker discovery: typed + inherited + multiple, edges distinguished
// ============================================================================
test("blockersFor: direct typed, inherited with source path, closed dropped", () => {
  const f = fixture("cross-branch.json");
  const snap = buildSnapshot(baseReads(f.reads));
  const bs = B.blockersFor(snap, "victim");
  assert.deepEqual(bs.filter((b) => !b.inherited).map((b) => b.id).sort(),
    ["liveB1", "liveB2"], "multiple direct open blockers ALL offered, not just first");
  const inh = bs.find((b) => b.inherited);
  assert.equal(inh.id, "epicBlocker");
  assert.equal(inh.source, "epicA", "inherited blocker names the blocked ancestor");
  assert.ok(Array.isArray(inh.via) && inh.via[0] === "victim" && inh.via.includes("epicA"),
    "inherited blocker carries the source path to the blocked ancestor");
  assert.ok(!bs.some((b) => b.id === "closedB"), "closed dependency drops out");
});

test("edges distinguished: incoming blockers vs outgoing dependents vs parent-child", () => {
  const f = fixture("cross-branch.json");
  const snap = buildSnapshot(baseReads(f.reads));
  const v = B.blockerEdges(snap, "victim");
  assert.deepEqual(v.incomingBlockers.map((x) => x.id).sort(), ["closedB", "liveB1", "liveB2"]);
  assert.ok(v.incomingBlockers.every((x) => x.edgeType === "blocks"));
  assert.deepEqual(v.incomingDependents, [], "victim blocks nothing itself");
  assert.deepEqual(v.outgoingDependents, []);
  assert.equal(v.parentChild.parent, "mid");

  const l = B.blockerEdges(snap, "liveB1");
  assert.deepEqual(l.outgoingDependents.map((x) => x.id), ["victim"],
    "victim's dependency on liveB1 is an OUTGOING dependent edge, not liveB1's blocker");
  assert.deepEqual(l.incomingDependents.map((x) => x.id), ["victim"]);

  const d = B.blockerEdges(snap, "bDependent");
  assert.deepEqual(d.incomingBlockers, [], "bDependent blocks nothing, is blocked by nothing");
  assert.equal(d.parentChild.parent, "epicB");
  assert.deepEqual(d.parentChild.children, ["bDependent2"],
    "parent-child comes from the parent FIELD, not from edge spelling");
});

test("unknown edge types preserved verbatim, never classified as blockers", () => {
  const f = fixture("cross-branch.json");
  const snap = buildSnapshot(baseReads(f.reads));
  const e = B.blockerEdges(snap, "liveB1");
  assert.deepEqual(e.unknownEdges, [{ id: "weirdRef", edgeType: "related" }]);
  assert.ok(!B.blockersFor(snap, "liveB1").some((b) => b.id === "weirdRef"));
});

// ============================================================================
// 2. read-only card surface: mutation attempts are refused at the boundary
// ============================================================================
test("assertReadArgv: reads pass, every mutation verb/flag refused by name", () => {
  B.assertReadArgv(["list", "--all", "--limit", "0", "--json"]);
  B.assertReadArgv(["ready", "--exclude-type=epic", "--json"]);
  B.assertReadArgv(["dep", "tree", "liveB1"]);
  B.assertReadArgv(["show", "x", "--json"]);
  for (const argv of [["close", "x", "--reason", "y"], ["dep", "add", "a", "b"],
    ["update", "x", "--claim"], ["update", "x", "-a", "bot"], ["update", "x", "-s", "closed"],
    ["list", "--force"], ["reclaim", "x"], ["delete", "x"]]) {
    assert.throws(() => B.assertReadArgv(argv), /read-only/,
      `must refuse: bd ${argv.join(" ")}`);
  }
});

test("jumpToBlocker only issues read commands through the provider", () => {
  const f = fixture("cross-branch.json");
  const snap = buildSnapshot(baseReads(f.reads));
  const ui = createWorkbenchState(snap, { selection: "victim" });
  ui.enter();
  const st = { scroll: 40, filter: "label=impl", search: "q", tab: "ready", pane: "list" };
  const stack = createHistoryStack({ storeKey: snap.storeKey });
  const seen = [];
  const provider = {
    run: (...argv) => { seen.push(argv.join(" "));
      if (argv[0] === "dep") return { tree: { id: argv[2], children: [] } };
      return []; },
    storeInfo: { workspace: "/lab/store", db: "/lab/store/.beads/lab.db" },
  };
  const res = B.jumpToBlocker({ snapshot: snap, ui, stack, state: st, provider, targetId: "liveB1" });
  assert.equal(res.card.targetId, "liveB1");
  assert.deepEqual(seen, ["dep tree liveB1", "show liveB1 --json"]);
  assert.ok(seen.every((c) => !/\b(close|update|create|delete|reclaim|dep add)\b/.test(c)));
});

// ============================================================================
// 3. S2 full-context restore: one press returns the full bundle, no drift
// ============================================================================
test("S2 restore: jump then ONE back restores selection/focus/expansion/filter/search/tab/pane/scroll", () => {
  const f = fixture("cross-branch.json");
  const snap = buildSnapshot(baseReads(f.reads));
  const ui = createWorkbenchState(snap, { selection: "victim" });
  ui.enter();
  ui.toggleExpanded("mid"); // diverge expansion from default
  const st = { scroll: 120, filter: "label=impl", search: "block", tab: "browse", pane: "detail" };
  const stack = createHistoryStack({ storeKey: snap.storeKey });
  const pre = s2Bundle(ui, st);
  stack.push(pre);
  const provider = {
    run: (_v, ...rest) => (_v === "dep" ? { tree: { id: rest[0], children: [] } } : []),
    storeInfo: { workspace: "/lab/store", db: "/lab/store/.beads/lab.db" },
  };
  const { card } = B.jumpToBlocker({ snapshot: snap, ui, stack, state: st, provider, targetId: "liveB1" });
  assert.equal(ui.focus, "liveB1", "jump moved focus to the blocker");
  assert.ok(card.blockers !== undefined && card.ancestry.length > 0);
  const back = B.returnFromCard({ stack, ui, state: st }); // ONE press
  assert.equal(back.restored, true);
  assert.ok(snapEq(s2Bundle(ui, st), pre),
    `bundle drifted:\n got ${project(s2Bundle(ui, st))}\nwant ${project(pre)}`);
  // deeper chase: a TRUE back stack, one press per step
  B.jumpToBlocker({ snapshot: snap, ui, stack, state: st, provider, targetId: "liveB2" });
  B.jumpToBlocker({ snapshot: snap, ui, stack, state: st, provider, targetId: "epicBlocker" });
  assert.equal(B.returnFromCard({ stack, ui, state: st }).restored, true);
  assert.equal(ui.focus, "liveB2");
  assert.equal(B.returnFromCard({ stack, ui, state: st }).restored, true);
  assert.ok(snapEq(s2Bundle(ui, st), pre), "two presses return to the pre-chase bundle exactly");
  assert.equal(B.returnFromCard({ stack, ui, state: st }).restored, false,
    "bottom of the stack: honest no-op, no phantom state");
});

test("one-press return via the app-back gesture (Alt+ArrowLeft → history-back)", () => {
  const f = fixture("cross-branch.json");
  const snap = buildSnapshot(baseReads(f.reads));
  const ui = createWorkbenchState(snap, { selection: "victim" });
  ui.enter();
  const st = { scroll: 7, filter: null, search: null, tab: "ready", pane: "list" };
  const stack = createHistoryStack({ storeKey: snap.storeKey });
  const pre = s2Bundle(ui, st);
  stack.push(pre);
  const provider = { run: () => ({ tree: { id: "x", children: [] } }),
    storeInfo: { workspace: "/lab/store", db: "/lab/store/.beads/lab.db" } };
  B.jumpToBlocker({ snapshot: snap, ui, stack, state: st, provider, targetId: "liveB1" });
  assert.notEqual(ui.focus, pre.focus);
  assert.equal(B.resolveReturnKey({ key: "ArrowLeft", altKey: true }), "history-back");
  assert.equal(B.resolveReturnKey({ key: "ArrowLeft" }), null);
  const r = B.appBack({ stack, ui, state: st });
  assert.equal(r.restored, true);
  assert.ok(snapEq(s2Bundle(ui, st), pre));
});

// ============================================================================
// 4. card composition: ancestry, lease, badge truth over dep-tree badge
// ============================================================================
test("buildBlockerCard: ancestry carried, lease shown, no mutation surface", () => {
  const f = fixture("cross-branch.json");
  const snap = buildSnapshot(baseReads(f.reads));
  const card = B.buildBlockerCard({ snapshot: snap, targetId: "liveB1",
    depTree: fixture("dep-tree.json"), records: {} });
  assert.deepEqual(card.ancestry, ["epicB", "liveB1"], "ancestry from the parent FIELD chain");
  assert.equal(card.statusWord, "open", "stored status verbatim");
  assert.equal(card.derivedBlocked, true, "native blocked read is the badge truth");
  assert.equal(card.badge, "blocked");
  assert.equal(card.depTreeClaimedReady, true,
    "dep-tree READY lie kept as visible provenance, never trusted (FACT P6d)");
  assert.equal(card.lease, null, "unclaimed blocker shows no lease");
  assert.equal(B.cardMutationSurface(card), false, "card exposes no callable affordance");

  const claimed = B.buildBlockerCard({ snapshot: snap, targetId: "claimedB",
    depTree: { tree: { id: "claimedB", children: [] } }, records: {} });
  assert.equal(claimed.lease.holder, "stuck-bot",
    "operators see WHO holds the stuck blocker");
  assert.equal(claimed.lease.leaseExpiresAt, "2026-09-28T20:10:00Z");
  assert.equal(claimed.lease.heartbeatAt, "2026-09-28T20:05:00Z");
});

test("badge honesty: no blocked read => unknown, never inferred", () => {
  const f = fixture("cross-branch.json");
  const noRead = buildSnapshot({ issues: f.reads.issues, ready: [], blocked: null,
    storeInfo: { workspace: "/lab/store", db: "/lab/store/.beads/lab.db" } });
  const c = B.buildBlockerCard({ snapshot: noRead, targetId: "liveB1", depTree: null, records: {} });
  assert.equal(c.badge, null);
  assert.equal(c.derivedBlocked, null);
});

// ============================================================================
// 5. component evidence ONLY (JSX shim): structure — no mount, no usability
// ============================================================================
test("BlockerCard renders target, ancestors, blockers, lease; NO mutation affordance", () => {
  const f = fixture("cross-branch.json");
  const snap = buildSnapshot(baseReads(f.reads));
  const card = B.buildBlockerCard({ snapshot: snap, targetId: "claimedB",
    depTree: { tree: { id: "claimedB", children: [] } }, records: {} });
  const tree = B.BlockerCard({ card, onReturn: () => {} });
  assert.equal(tree.props["aria-label"], "Blocker card");
  assert.equal(findById(tree, "blocker-card:claimedB").length, 1);
  assert.ok(findById(tree, "ancestry:epicB").length === 1, "ancestry rendered");
  assert.ok(textIn(tree, "stuck-bot"), "lease holder rendered");
  assert.ok(textIn(tree, "in_progress"), "status WORD rendered (never glyph-only)");
  assert.ok(textIn(tree, "blocked"), "derived badge rendered");
  assert.equal(findById(tree, "blocker-close").length, 0, "NO close affordance on the card");
  assert.equal(findById(tree, "blocker-reopen").length, 0);
  assert.equal(findById(tree, "blocker-return").length, 1, "exactly ONE action: return");
});

// ============================================================================
// 6. one-nav-state rule: jump rides THE existing stacks; no parallel copy
// ============================================================================
test("jump pushes onto the single history stack in lockstep with the model", () => {
  const f = fixture("cross-branch.json");
  const snap = buildSnapshot(baseReads(f.reads));
  const ui = createWorkbenchState(snap, { selection: "victim" });
  ui.enter();
  const st = { scroll: 0, filter: null, search: null, tab: null, pane: "list" };
  const stack = createHistoryStack({ storeKey: snap.storeKey });
  stack.push(s2Bundle(ui, st));
  const provider = { run: () => ({ tree: { id: "x", children: [] } }),
    storeInfo: { workspace: "/lab/store", db: "/lab/store/.beads/lab.db" } };
  const res = B.jumpToBlocker({ snapshot: snap, ui, stack, state: st, provider, targetId: "liveB1" });
  assert.equal(res.stack, stack, "same stack instance returned — never a copy");
  const entries = stack.entries();
  assert.equal(entries.length, 2, "jump pushed exactly one bundle");
  assert.equal(entries[1].focus, "liveB1");
  assert.equal(entries[1].expanded.join(","),
    [...ui.expanded].sort().join(","), "stack entry mirrors THE model expansion set");
  assert.equal(ui.history.length, 2,
    "model stack pushes in lockstep: enter + jump, nothing extra");
  assert.ok(ui.history[ui.history.length - 1].focus === "liveB1");
});

// ============================================================================
// 7. GENUINE native: external close flips the badge on refresh, no auto-jump
// ============================================================================
test("native external close: refresh flips derived badge; focus never auto-jumps", () => {
  const store = makeNativeStore();
  try {
    const ids = seedCrossBranch(store);
    const info = bd(store, "info");
    const storeInfo = { workspace: store, db: info.database_path };
    const readSnap = () => buildSnapshot({
      issues: bd(store, "list", "--all", "--limit", "0"),
      ready: bd(store, "ready", "--limit", "0"),
      blocked: bd(store, "blocked"),
      storeInfo,
    }, { bound: 500 });

    let snap = readSnap();
    assert.equal(snap.nodes.get(ids.victim).derivedBlocked, true,
      "pre: victim derived-blocked by live b1 (real bd reads)");
    assert.ok(snap.nodes.get(ids.victim).typedBlockers.some((b) => b.id === ids.b1),
      "closed b2 dropped out of active blockers");
    assert.ok(snap.nodes.get(ids.victim).inheritedBlockers.some((b) =>
      b.id === ids.epicDep && b.source === ids.epicA), "inherited from the blocked ancestor");

    const ui = createWorkbenchState(snap, { selection: ids.victim });
    ui.enter();
    const st = { scroll: 33, filter: "label=impl", search: "x", tab: "browse", pane: "list" };
    const stack = createHistoryStack({ storeKey: snap.storeKey });
    const pre = s2Bundle(ui, st);
    stack.push(pre);
    // provider.run receives the FULL argv (guardedRun passes verb first);
    // replay it through the real bd helper, which appends --json itself.
    const provider = {
      run: (verb, ...rest) => (verb === "dep" || verb === "show")
        ? bd(store, verb, ...rest) : [],
      storeInfo,
    };
    const { card } = B.jumpToBlocker({ snapshot: snap, ui, stack, state: st,
      provider, targetId: ids.b1 });
    // CARD BADGE TRUTH: b1 is itself open and unblocked (the one live edge it
    // had, b2, is closed; victim being blocked does NOT block b1). Native
    // ready/blocked is the only badge truth — so the card must say "ready",
    // whatever any dep-tree badge claims (P6d lie covered in synthetic layer).
    assert.equal(card.badge, "ready");
    assert.equal(ui.focus, ids.b1);

    // GENUINE external close by a different actor, straight through real bd:
    execFileSync(BD_BIN, ["close", ids.b1, "--reason", "externally closed by other actor",
      "--json"], { cwd: store, stdio: ["ignore", "pipe", "pipe"] });
    execFileSync(BD_BIN, ["close", ids.epicDep, "--reason", "external",
      "--json"], { cwd: store, stdio: ["ignore", "pipe", "pipe"] });

    // Baseline for NO AUTO-JUMP is the POST-jump bundle (the jump legitimately
    // moved focus; the refresh must not move the operator from WHERE IT PUT THEM).
    const post = s2Bundle(ui, st);
    snap = readSnap(); // REFRESH
    assert.equal(snap.nodes.get(ids.victim).derivedBlocked, false,
      "after external closes clear the live blockers, refresh flips the badge to ready");
    // NO AUTO-JUMP: the badge flip left the whole context bundle untouched.
    assert.ok(snapEq(s2Bundle(ui, st), post),
      `refresh must not move the operator:\n got ${project(s2Bundle(ui, st))}\nwant ${project(post)}`);

    const card2 = B.buildBlockerCard({ snapshot: snap, targetId: ids.b1,
      depTree: null, records: {} });
    assert.equal(card2.statusWord, "closed", "card rebuilt from fresh snapshot shows the flip");
    assert.notEqual(card2.derivedBlocked, true);
  } finally {
    rmSync(store, { recursive: true, force: true });
  }
});

// ============================================================================
// the user-reachable door (JSX shim: structure only). The card
// was host-injectable only; a human now opens it via the row door, and the
// Return button must actually invoke onReturn (the shipped bug wired
// onReturn=rerender at the root, which never cleared the card).
// ============================================================================
test("BlockersDoor: renders on blocked rows, absent on clean rows, disabled-with-reason without the provider, and clicks reach onOpen", () => {
  const f = fixture("cross-branch.json");
  const snap = buildSnapshot(baseReads(f.reads));
  const opened = [];
  const door = (id, providerReady) =>
    walk(B.BlockersDoor({ snapshot: snap, id, providerReady,
      onOpen: (x) => opened.push(x) })).filter((n) => typeof n === "object");
  // blocked row => a visible 'blockers' button (victim: direct liveB1/liveB2)
  const blockedNodes = door("victim", true);
  const btn = blockedNodes.find((n) => n.props?.id === "blockers-open:victim");
  assert.ok(btn && btn.type === "button", "blocked row renders a real button");
  assert.equal(btn.props.disabled, false, "enabled when the provider facade is present");
  assert.equal(blockedNodes.find((n) => n.props?.id === "blockers-disabled-reason"), undefined,
    "no disabled-reason when the door is armed");
  btn.props.onClick();
  assert.deepEqual(opened, ["victim"], "click delegates the row id to onOpen");
  // clean row => no door at all (nothing invented for unblocked beads)
  assert.equal(door("liveB1", true).find((n) => n.props?.id === "row-doors"), undefined,
    "unblocked rows render no door");
  // missing facade => present-but-disabled with a VISIBLE reason, never a throw
  const offNodes = door("victim", false);
  const offBtn = offNodes.find((n) => n.props?.id === "blockers-open:victim");
  assert.ok(offBtn, "button still PRESENT without the facade (present-but-disabled)");
  assert.equal(offBtn.props.disabled, true, "disabled without the provider facade");
  const reason = offNodes.find((n) => n.props?.id === "blockers-disabled-reason");
  assert.ok(reason && /provider/i.test(walk(reason).filter((s) => typeof s === "string").join("")),
    "a visible reason names the missing read provider");
  assert.doesNotThrow(() => offBtn.props.onClick(), "a disabled door click is an honest no-op, never a throw");
  assert.deepEqual(opened, ["victim"], "the disabled click reached nobody");
});

test("Return rule: the card's ONLY affordance calls onReturn (root wires returnFromCard, not rerender)", () => {
  const f = fixture("cross-branch.json");
  const snap = buildSnapshot(baseReads(f.reads));
  const card = B.buildBlockerCard({ snapshot: snap, targetId: "liveB1",
    depTree: { tree: { id: "liveB1", children: [] } }, records: {} });
  let returns = 0;
  const tree = walk(B.BlockerCard({ card, onReturn: () => { returns += 1; }, snapshot: snap }));
  const ret = tree.find((n) => typeof n === "object" && n.props?.id === "blocker-return");
  assert.ok(ret, "Return button rendered");
  ret.props.onClick();
  assert.equal(returns, 1, "clicking Return invokes onReturn exactly once " +
    "(the shipped root passed onReturn=rerender, which left the card mounted — defect 1)");
});
