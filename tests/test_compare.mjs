// tests/test_compare.mjs — hbl-pnu.2.7: read-only SPLIT compare with
// independent per-side selection + structural-churn refresh-diff (moved
// lines, confirmed tombstones citing `bd history <id>`), against the LAST
// MATERIALIZED snapshot (no event bus — the CLI emits nothing to viewers).
// Run: node --test tests/test_compare.mjs   (Node built-in runner, no deps)
import test from "node:test";
import assert from "node:assert/strict";
import { mkdirSync, rmSync } from "node:fs";
import { execFileSync } from "node:child_process";
import { register } from "node:module";
import { fileURLToPath, pathToFileURL } from "node:url";
import path from "node:path";

const here = path.dirname(fileURLToPath(import.meta.url));
register(pathToFileURL(path.join(here, "__shims__", "jsx-loader.mjs")).href);
const shim = await import(pathToFileURL(path.join(here, "__shims__", "jsx-capture.mjs")).href);

const {
  createSplitPanes, diffSnapshots, confirmDeletions, SplitCompare,
} = await import("../desktop/compare.mjs");
const { buildSnapshot } = await import("../desktop/model.mjs");

const walk = (t) => [...shim.walk(t)];
const textIn = (tree, s) => walk(tree).some((n) => typeof n === "string" && n.includes(s));
const kinds = (tree) => walk(tree)
  .filter((n) => typeof n === "object" && n.props?.["data-kind"])
  .map((n) => n.props["data-kind"]);

const storeInfo = (tag) => ({ workspace: `/lab/${tag}`, db: `/lab/${tag}/.beads/lab.db` });
const snap = (rows, opts = {}, readsOver = {}) =>
  buildSnapshot({ issues: rows, ready: [], blocked: [],
    storeInfo: opts.storeInfo ?? storeInfo("A"), ...readsOver }, opts);

// ---- S8: split panes — independent selection, read-only comparison --------
test("split panes: selections are fully independent; no shared cursor", () => {
  const left = snap([{ id: "a" }, { id: "b" }]);
  const right = snap([{ id: "a" }, { id: "b" }], { storeInfo: storeInfo("B") });
  const sp = createSplitPanes({ left: { snapshot: left }, right: { snapshot: right } });
  sp.select("left", "a");
  sp.select("right", "b");
  assert.equal(sp.selection("left"), "a");
  assert.equal(sp.selection("right"), "b");
  sp.arrow("left", 1);
  assert.equal(sp.selection("left"), "b");
  assert.equal(sp.selection("right"), "b"); // other side's nav changed nothing here
  sp.select("right", "a");
  assert.equal(sp.selection("left"), "b");
});

test("split panes: foreign id ignored, unknown side rejected, store identities tracked apart", () => {
  const left = snap([{ id: "a" }]);
  const right = snap([{ id: "z" }], { storeInfo: storeInfo("B") });
  const sp = createSplitPanes({ left: { snapshot: left }, right: { snapshot: right } });
  sp.select("left", "nope");
  assert.equal(sp.selection("left"), null, "foreign id is a no-op");
  assert.throws(() => sp.select("middle", "a"), /side/);
  assert.notEqual(sp.storeKey("left"), sp.storeKey("right"),
    "sides track distinct store identities — compare is cross-store readable");
});

test("split panes: edits/drafts on one side never disturb the other", () => {
  const left = snap([{ id: "a" }, { id: "b" }]);
  const right = snap([{ id: "a" }], { storeInfo: storeInfo("B") });
  const sp = createSplitPanes({
    left: { snapshot: left, draft: "L1" },
    right: { snapshot: right, draft: "R1" },
  });
  sp.setDraft("left", "L2");
  sp.select("right", "a");
  sp.setDraft("right", "R2");
  sp.setDraft("left", "L3");
  assert.equal(sp.draft("left"), "L3");
  assert.equal(sp.draft("right"), "R2", "left edits never touch right's draft");
  assert.equal(sp.selection("right"), "a");
});

test("split panes: setSide reparents one view without disturbing the other side", () => {
  const left = snap([{ id: "a" }]);
  const right = snap([{ id: "z" }], { storeInfo: storeInfo("B") });
  const sp = createSplitPanes({ left: { snapshot: left }, right: { snapshot: right } });
  sp.select("right", "z");
  sp.select("left", "a");
  sp.setSide("left", { snapshot: snap([{ id: "q" }], { storeInfo: storeInfo("C") }) });
  assert.equal(sp.selection("right"), "z", "right untouched");
  assert.equal(sp.selection("left"), null, "swapped side resets only its own selection");
});

// ---- refresh-diff: moves + deletions vs the LAST MATERIALIZED snapshot ----
test("diff: reparent renders an explicit moved line, never a ghost row", () => {
  const before = snap([{ id: "p1" }, { id: "p2" }, { id: "kid", parent: "p1" }]);
  const after = snap([{ id: "p1" }, { id: "p2" }, { id: "kid", parent: "p2" }]);
  const d = diffSnapshots(before, after);
  assert.deepEqual(d.moved, [{ id: "kid", from: "p1", to: "p2" }]);
  assert.equal(d.movedLine("kid"), "moved: kid parent p1 \u2192 p2");
  assert.equal(d.rows.some((r) => r.kind === "ghost"), false,
    "no ghost rows are ever synthesized");
});

test("diff: vanished-but-unconfirmed id is absence, NOT a deletion claim", () => {
  const before = snap([{ id: "a" }, { id: "gone" }]);
  const after = snap([{ id: "a" }]);
  const d = diffSnapshots(before, after);
  assert.deepEqual(d.absent.map((r) => r.id), ["gone"]);
  assert.equal(d.absent[0].confirmed, false);
  assert.match(d.absent[0].proof, /not proof of deletion/);
  assert.equal(d.tombstones.length, 0, "no tombstone without native confirmation");
});

test("diff: truncated / permission-denied / outage reads never yield absence claims", () => {
  const before = snap([{ id: "a" }, { id: "b" }]);

  const truncRows = Object.assign([{ id: "a" }], { truncated: true });
  let d = diffSnapshots(before, snap(truncRows));
  assert.equal(d.deletionsUnconfirmed.length, 0, "truncation suppresses all absence claims");
  assert.match(d.absentNote(), /truncat/i);
  assert.equal(d.sideHealth("after"), "truncated");

  d = diffSnapshots(before, snap([], { readError: { kind: "permission", message: "access denied" } }));
  assert.equal(d.deletionsUnconfirmed.length, 0, "permission failure is not absence");
  assert.equal(d.sideHealth("after"), "permission-denied");

  d = diffSnapshots(before, snap([], { readError: { kind: "outage", message: "connection refused" } }));
  assert.equal(d.deletionsUnconfirmed.length, 0, "outage is not absence");
  assert.equal(d.sideHealth("after"), "outage");
});

// ---- tombstone confirmation via NATIVE show + history (fail-closed) -------
// mkReads: show/history stand-ins for the raw parsed `bd …  --json` shapes.
// `deleted` lists the ids the native side reports as gone (show => [] /
// error-object; history => surviving commit snapshots).
const recs = (rec) => [rec];
const mkReads = ({ deleted = ["gone"], ...over } = {}) => ({
  show: (id) => (deleted.includes(id) ? [] : recs({ id, status: "open" })),
  history: (id) => (deleted.includes(id) ? [{ Issue: { id }, CommitHash: "c1" }] : []),
  ...over,
});

test("confirm: show-not-found + surviving history entry => tombstone citing bd history", () => {
  const before = snap([{ id: "a" }, { id: "gone" }]);
  const after = snap([{ id: "a" }]);
  const d = diffSnapshots(before, after);
  const res = confirmDeletions(d, { reads: mkReads() });
  assert.deepEqual(res.tombstones.map((t) => t.id), ["gone"]);
  assert.equal(res.tombstones[0].citation, "bd history gone");
  assert.deepEqual(res.stillUnconfirmed, []);
  assert.equal(res.rows.some((r) => r.kind === "ghost"), false);
});

test("confirm: bd show error-object shape (not-found) also confirms with history", () => {
  const before = snap([{ id: "a" }, { id: "gone" }]);
  const after = snap([{ id: "a" }]);
  const d = diffSnapshots(before, after);
  const res = confirmDeletions(d, { reads: mkReads({
    show: (id) => (id === "gone" ? { error: "no issues found matching the provided IDs" } : recs({ id })),
  }) });
  assert.deepEqual(res.tombstones.map((t) => t.id), ["gone"]);
});

test("confirm: still-visible in native show => resurrected, no tombstone", () => {
  const before = snap([{ id: "a" }, { id: "gone" }]);
  const after = snap([{ id: "a" }]);
  const d = diffSnapshots(before, after);
  const res = confirmDeletions(d, {
    reads: mkReads({ deleted: [], show: (id) => recs({ id }) }),
  });
  assert.equal(res.tombstones.length, 0);
  assert.deepEqual(res.resurrected.map((r) => r.id), ["gone"]);
});

test("confirm: show/history outage or ambiguous answer => stays unconfirmed (fail closed)", () => {
  const before = snap([{ id: "a" }, { id: "gone" }]);
  const after = snap([{ id: "a" }]);
  const d = diffSnapshots(before, after);
  let res = confirmDeletions(d, {
    reads: mkReads({ show: () => { throw new Error("dolt: connection refused"); } }),
  });
  assert.equal(res.tombstones.length, 0);
  assert.deepEqual(res.stillUnconfirmed.map((r) => r.id), ["gone"]);

  // show not-found but history ALSO gone (purged) => cannot prove deletion
  res = confirmDeletions(d, { reads: mkReads({ history: () => [] }) });
  assert.equal(res.tombstones.length, 0);
  assert.deepEqual(res.stillUnconfirmed.map((r) => r.id), ["gone"]);

  // history read outage => unconfirmed
  res = confirmDeletions(d, { reads: mkReads({ history: () => { throw new Error("outage"); } }) });
  assert.equal(res.tombstones.length, 0);
});

// ---- SplitCompare component -------------------------------------------------
const focusable = (over = {}) => ({
  focus: "kid", originPane: "tree", draft: "work-in-progress", ...over,
});

test("SplitCompare: moved line, banner + re-resolved breadcrumb for moved focus, tombstone history link", () => {
  const prev = snap([{ id: "p1" }, { id: "p2" }, { id: "kid", parent: "p1" }]);
  const leftSnap = snap([{ id: "p1" }, { id: "p2" }, { id: "kid", parent: "p2" }]);
  const rightPrev = snap([{ id: "p1" }, { id: "p2" }, { id: "kid", parent: "p1" }], { storeInfo: storeInfo("B") });
  const rightSnap = snap([{ id: "p1" }, { id: "p2" }], { storeInfo: storeInfo("B") });
  const panes = createSplitPanes({
    left: { snapshot: leftSnap, focusable: focusable(),
      breadcrumb: (id) => `p2 / ${id}` },
    right: { snapshot: rightSnap },
  });
  const d = diffSnapshots(prev, leftSnap);
  const confirmed = confirmDeletions(diffSnapshots(rightPrev, rightSnap), { reads: mkReads({ deleted: ["kid"] }) });
  const tree = SplitCompare({ panes, side: "left", diff: d, confirmed,
    breadcrumb: (id) => panes.side("left").breadcrumb(id) });
  assert.ok(textIn(tree, "moved: kid parent p1 \u2192 p2"), "move line visible");
  assert.ok(textIn(tree, "banner"), "banner for moved focus parent-chain");
  assert.ok(textIn(tree, "p2 / kid"), "breadcrumb re-resolved against CURRENT snapshot");
  assert.ok(textIn(tree, "bd history kid"), "tombstone cites native history");
  assert.ok(walk(tree).some((n) => typeof n === "object" && n.props?.href === "bd history kid"),
    "working history link element present");
  assert.equal(kinds(tree).includes("ghost"), false, "no ghost rows rendered");
});

// bead 2.7: "focused issue whose parent chain moved gets banner + re-resolved
// breadcrumb". The focused row may be a DESCENDANT of the reparented issue:
// its own parent field is unchanged, so only its ANCESTOR chain moved.
// Independent real-bd acceptance (review accept_real.mjs R6/R7) found no banner.
test("SplitCompare: focused DESCENDANT of a reparented issue gets banner + breadcrumb re-resolved from the current snapshot", () => {
  const prev = snap([{ id: "p1" }, { id: "p2" }, { id: "kid", parent: "p1" }, { id: "grand", parent: "kid" }]);
  const cur = snap([{ id: "p1" }, { id: "p2" }, { id: "kid", parent: "p2" }, { id: "grand", parent: "kid" }]);
  const d = diffSnapshots(prev, cur);
  assert.deepEqual(d.moved.map((m) => m.id), ["kid"], "only kid's own parent field changed");
  const panes = createSplitPanes({
    left: { snapshot: cur, focusable: focusable({ focus: "grand" }) },
    right: { snapshot: snap([], { storeInfo: storeInfo("B") }) },
  });
  // no breadcrumb fn injected: the component resolves it from the snapshot itself
  const tree = SplitCompare({ panes, side: "left", diff: d });
  assert.ok(textIn(tree, "banner"), "banner: focused row's parent chain moved (ancestor reparented)");
  assert.ok(textIn(tree, "p2 / kid / grand"), "breadcrumb re-resolved against the CURRENT snapshot");
  const banner = walk(tree).filter((n) => typeof n === "string" && n.startsWith("banner:")).join("");
  assert.ok(banner && !banner.includes("p1"), `old chain never shown as current: ${banner}`);
  // unrelated focus: moved line only, no banner
  const other = createSplitPanes({
    left: { snapshot: cur, focusable: focusable({ focus: "p1" }) },
    right: { snapshot: snap([], { storeInfo: storeInfo("B") }) },
  });
  assert.ok(!textIn(SplitCompare({ panes: other, side: "left", diff: d }), "banner"), "no banner for an unaffected focus");
});

test("SplitCompare: unconfirmed absence renders with the non-proof caveat", () => {
  const prev = snap([{ id: "a" }, { id: "gone" }]);
  const cur = snap([{ id: "a" }]);
  const panes = createSplitPanes({
    left: { snapshot: cur, focusable: focusable({ focus: "a" }) },
    right: { snapshot: snap([], { storeInfo: storeInfo("B") }) },
  });
  const tree = SplitCompare({ panes, side: "left", diff: diffSnapshots(prev, cur) });
  assert.ok(textIn(tree, "unconfirmed: gone"));
  assert.ok(textIn(tree, "not proof of deletion"), "bounded absence caveat rendered");
});

test("churn survival: focus, origin pane and draft persist through diff application", () => {
  const before = snap([{ id: "p1" }, { id: "p2" }, { id: "kid", parent: "p1" }]);
  const after = snap([{ id: "p1" }, { id: "p2" }, { id: "kid", parent: "p2" }]);
  const panes = createSplitPanes({
    left: { snapshot: before, focusable: focusable(), draft: "D1" },
    right: { snapshot: snap([], { storeInfo: storeInfo("B") }) },
  });
  panes.applyRefresh("left", { snapshot: after, diff: diffSnapshots(before, after) });
  assert.equal(panes.focus("left"), "kid", "focus never drops to root on reparent churn");
  assert.equal(panes.originPane("left"), "tree");
  assert.equal(panes.draft("left"), "D1", "draft survives churn");
  // unrelated deletion never disturbs the focus either
  panes.applyRefresh("left", { snapshot: after, diff: null });
  assert.equal(panes.focus("left"), "kid");
});

test("churn: focused id itself deleted (confirmed) => focus kept, marked gone, draft kept", () => {
  const before = snap([{ id: "a" }, { id: "b" }]);
  const after = snap([{ id: "a" }]);
  const panes = createSplitPanes({
    left: { snapshot: before, focusable: focusable({ focus: "b" }), draft: "keepme" },
    right: { snapshot: snap([], { storeInfo: storeInfo("B") }) },
  });
  const confirmed = confirmDeletions(diffSnapshots(before, after), { reads: mkReads({ deleted: ["b"] }) });
  panes.applyRefresh("left", { snapshot: after, confirmed });
  assert.equal(panes.focus("left"), "b", "focus identity kept for the tombstone view");
  assert.equal(panes.focusGone("left"), true);
  assert.equal(panes.draft("left"), "keepme");
});

// ---- native fixture: real bd churn (reparent + delete) driven live ---------
const LAB = "/home/hermes/.hermes/work/beads-lab";
const BIN = path.join(LAB, "bin", "bd");
const ACTOR = "completion-compare";
const FIX = path.join(here, ".compare-runtime");

const bdRead = (cwd, ...args) =>
  JSON.parse(execFileSync("flock", [path.join(LAB, "planning-access.lock"),
    BIN, "-C", cwd, "--readonly", "--actor", ACTOR, ...args, "--json"],
    { encoding: "utf8" }));
const bdMutate = (cwd, ...args) =>
  execFileSync("flock", [path.join(LAB, "planning-access.lock"),
    BIN, "-C", cwd, "--actor", ACTOR, ...args, "--json"], { encoding: "utf8" });
const firstId = (out) => {
  const parsed = JSON.parse(out);
  return (Array.isArray(parsed) ? parsed[0] : parsed).id;
};

function nativeStore(name) {
  const cwd = path.join(FIX, name);
  rmSync(cwd, { recursive: true, force: true });
  mkdirSync(cwd, { recursive: true });
  execFileSync("git", ["init", "-q", cwd]);
  execFileSync("git", ["-C", cwd, "config", "user.name", "lane-test"]);
  execFileSync("git", ["-C", cwd, "config", "user.email", "lane-test@localhost"]);
  execFileSync(BIN, ["init", "--prefix", "tst"], { cwd, encoding: "utf8" });
  return { cwd, info: bdRead(cwd, "info") };
}
const createNative = (cwd, title, parent) =>
  firstId(bdMutate(cwd, "create", title,
    ...(parent ? ["--parent", parent] : []), "--allow-empty-description", "--json"));
const nativeSide = (st) => ({ workspace: st.cwd, db: st.info.database_path });
const nativeSnap = (st, opts = {}) => buildSnapshot(
  { issues: bdRead(st.cwd, "list", "--all", "--limit", "0"),
    ready: [], blocked: [], storeInfo: nativeSide(st) }, opts);

// probed native facts (bd 1.3.0): `bd show <deleted>` exits non-zero and
// prints {"error":"no issues found matching the provided IDs",...}; a
// never-existing id prints the same error and has NO history entries.
const nativeReads = (cwd) => ({
  show: (id) => {
    try { return bdRead(cwd, "show", id); }
    catch (e) {
      const m = String(e.stdout || e.message).match(/\{[\s\S]*\}/);
      if (m && m[0].includes("error")) return JSON.parse(m[0]);
      return null;
    }
  },
  history: (id) => {
    try { return bdRead(cwd, "history", id); } catch { return null; }
  },
});

test("NATIVE: real bd reparent + external delete -> moved line + confirmed tombstone with working history link", () => {
  const st = nativeStore(`churn-${process.pid}`);
  try {
    const p1 = createNative(st.cwd, "cmp p1");
    const p2 = createNative(st.cwd, "cmp p2");
    const kid = createNative(st.cwd, "cmp kid", p1);
    const ghost = createNative(st.cwd, "cmp ghost");

    // last materialized snapshot = the state the open view was rendered from
    const before = nativeSnap(st);

    // concurrent churn via real bd commands, view open (no events reach it)
    const kid2 = firstId(bdMutate(st.cwd, "update", kid, "--parent", p2, "--json"));
    bdMutate(st.cwd, "delete", ghost, "--force");

    const after = nativeSnap(st);
    const d = diffSnapshots(before, after);

    // reparent surfaces as an explicit move line (kid's parent FIELD p1 -> p2)
    const m = d.moved.find((x) => x.id === kid2);
    assert.ok(m, `moved line names reparented kid (${kid2}); moved=${JSON.stringify(d.moved)}`);
    assert.equal(m.from, p1);
    assert.equal(m.to, p2);

    // ghost vanished: absence ONLY, no tombstone from the diff alone
    assert.ok(d.absent.some((r) => r.id === ghost && r.confirmed === false));
    assert.equal(d.tombstones.length, 0);

    // tombstone requires native show + history confirmation
    const res = confirmDeletions(d, { reads: nativeReads(st.cwd) });
    assert.deepEqual(res.tombstones.map((t) => t.id), [ghost],
      "native show+history confirmed the deletion");
    assert.equal(res.tombstones[0].citation, `bd history ${ghost}`);
    assert.deepEqual(res.stillUnconfirmed, []);

    // rendered split: move line + tombstone history link, zero ghost rows
    const panes = createSplitPanes({
      left: { snapshot: after, focusable: { focus: kid2, originPane: "tree", draft: "d" } },
      right: { snapshot: before },
    });
    const tree = SplitCompare({ panes, side: "left", diff: d, confirmed: res });
    assert.ok(textIn(tree, `moved: ${kid2} parent ${p1} \u2192 ${p2}`));
    assert.ok(textIn(tree, `bd history ${ghost}`));
    assert.ok(walk(tree).some((n) => typeof n === "object" && n.props?.href === `bd history ${ghost}`));
    assert.equal(kinds(tree).includes("ghost"), false);

    // read-back evidence from the fixture store itself
    const rows = bdRead(st.cwd, "list", "--all", "--limit", "0");
    const kidRow = rows.find((r) => r.id === kid2);
    assert.equal(kidRow.parent, p2, "store read-back: parent FIELD moved");
    assert.equal(rows.some((r) => r.id === ghost), false, "store read-back: ghost gone");
    const hist = bdRead(st.cwd, "history", ghost);
    assert.ok(Array.isArray(hist) && hist.length > 0, "native history survives deletion");
    assert.ok(hist.every((e) => e.Issue?.id === ghost), "history entries cite the deleted id");
  } finally {
    rmSync(st.cwd, { recursive: true, force: true });
  }
});

test("NATIVE guard: truncated snapshot yields zero absence claims and zero tombstones even if show says not-found", () => {
  const st = nativeStore(`trunc-${process.pid}`);
  try {
    createNative(st.cwd, "cmp a");
    const b = createNative(st.cwd, "cmp b");
    const before = nativeSnap(st);
    bdMutate(st.cwd, "delete", b);
    // bounded read cut to one row: b's absence proves nothing
    const rowsAfter = Object.assign(bdRead(st.cwd, "list", "--all", "--limit", "0").slice(0, 1),
      { truncated: true });
    const after = buildSnapshot({ issues: rowsAfter, ready: [], blocked: [],
      storeInfo: nativeSide(st) }, {});
    const d = diffSnapshots(before, after);
    assert.equal(d.deletionsUnconfirmed.length, 0, "truncation suppresses all absence claims");
    const res = confirmDeletions(d, { reads: nativeReads(st.cwd) });
    assert.equal(res.tombstones.length, 0, "nothing to confirm from a truncated window");
  } finally {
    rmSync(st.cwd, { recursive: true, force: true });
  }
});
