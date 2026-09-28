// tests/test_record.mjs — hbl-pnu.1.5: native record tolerance in the
// read->render->re-emit path. Unknown statuses/issue types/edge types are
// preserved verbatim; stored status and native derived readiness render
// separately; divergence warns; the dep-tree [READY] badge is never
// authoritative; rendering is text-only (no markup injection).
// Run: node --test tests/test_record.mjs   (Node built-in runner, no deps)
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { register } from "node:module";
import { fileURLToPath, pathToFileURL } from "node:url";
import path from "node:path";

const here = path.dirname(fileURLToPath(import.meta.url));
register(pathToFileURL(path.join(here, "__shims__", "jsx-loader.mjs")).href);

const shim = await import(pathToFileURL(path.join(here, "__shims__", "jsx-capture.mjs")).href);
const { buildSnapshot } = await import("../desktop/model.mjs");
const { RecordCard, reEmitRecord } = await import("../desktop/record.mjs");

const fixture = (name) =>
  JSON.parse(readFileSync(path.join(here, "fixtures", "record", name), "utf8"));

const baseReads = (over = {}) => ({
  issues: [], ready: [], blocked: [],
  storeInfo: { workspace: "/lab/store", db: "/lab/store/.beads/lab.db" },
  ...over,
});

const walk = (t) => [...shim.walk(t)];
const textIn = (tree, s) => walk(tree).some((n) => typeof n === "string" && n.includes(s));
const texts = (tree) => walk(tree).filter((n) => typeof n === "string");

// ---- cycle 1: unknown values survive read -> render -> re-emit ---------------
test("unknown status/type/edge type survive read->render->re-emit verbatim; raw record unchanged", () => {
  const f = fixture("unknowns-roundtrip.json");
  const s = buildSnapshot(baseReads(f), { fetchedAt: 1 });
  const rec = s.byId.get("wip-1");

  const el = RecordCard({ snapshot: s, id: "wip-1" });
  const emitted = reEmitRecord(s, "wip-1");

  // re-emit is the untouched raw record (semantic equality + independence)
  assert.deepEqual(emitted, rec);
  assert.notEqual(emitted, rec, "re-emit must not hand back the live record");
  assert.notEqual(emitted.dependencies, rec.dependencies);

  // the renderer never mutated the raw record
  assert.equal(rec.status, "awaiting-review");
  assert.equal(rec.issue_type, "spike");
  assert.equal(rec.dependencies[0].dependency_type, "shadows");
  assert.deepEqual(rec.custom_field, { kept: ["verbatim", "nesting"] });

  // unknown values render verbatim as text
  assert.ok(textIn(el, "awaiting-review"), "unknown status renders verbatim");
  assert.ok(textIn(el, "spike"), "unknown issue type renders verbatim");
  assert.ok(textIn(el, "shadows"), "unknown edge type renders verbatim");
});

// ---- cycle 2: stored status vs native derived readiness, separately ----------
test("divergence case: stored 'blocked' with native-ready renders BOTH plus a warning", () => {
  const f = fixture("unknowns-roundtrip.json");
  const s = buildSnapshot(baseReads(f), { fetchedAt: 1 });
  const el = RecordCard({ snapshot: s, id: "stale-flag" });

  assert.ok(textIn(el, "stored status: blocked"), "stored status renders");
  assert.ok(textIn(el, "derived readiness: ready"), "native derived readiness renders separately");
  assert.ok(texts(el).some((t) => /⚠/.test(t) && /divergence|disagree/i.test(t)),
    "divergence warning rendered");
  // the two values stay distinct — never collapsed to one label
  assert.ok(textIn(el, "stored status: blocked") && !textIn(el, "status: ready\n"),
    "stored field is not overwritten by derived");
});

test("agreement case renders derived readiness with NO warning", () => {
  const f = fixture("unknowns-roundtrip.json");
  const s = buildSnapshot(baseReads(f), { fetchedAt: 1 });
  const el = RecordCard({ snapshot: s, id: "wall" });
  assert.ok(textIn(el, "stored status: hooked"), "custom-but-agreeing status renders");
  assert.ok(textIn(el, "derived readiness: ready"));
  assert.ok(!texts(el).some((t) => /⚠/.test(t)), "no warning when stored and derived agree");
});

// ---- cycle 3: dep-tree READY badge is never authoritative --------------------
test("dep-tree READY badge on the record never drives derived readiness", () => {
  const f = fixture("unknowns-roundtrip.json");
  const s = buildSnapshot(baseReads(f), { fetchedAt: 1 });
  const rec = s.byId.get("badge-liar");
  assert.equal(rec.dep_tree_status, "READY", "fixture: badge claims READY");
  // frontier says blocked while badge says READY -> derived must be blocked
  const s2 = buildSnapshot({ ...baseReads(f), ready: [], blocked: ["badge-liar"] },
    { fetchedAt: 1 });
  assert.equal(s2.nodes.get("badge-liar").derivedBlocked, true,
    "frontier truth wins over the badge");
  const el = RecordCard({ snapshot: s2, id: "badge-liar" });
  assert.ok(textIn(el, "derived readiness: blocked"),
    "card renders frontier truth, never the badge");
});

// ---- cycle 4: plain-text rendering, no markup injection ----------------------
test("malicious-looking unknown values render as plain text children, never markup", () => {
  const evil = {
    storeInfo: { workspace: "/lab/store", db: "/lab/store/.beads/lab.db" },
    issues: [{
      id: "evil",
      status: "<img src=x onerror=alert(1)>",
      issue_type: "<script>evil</script>",
      dependencies: [{ depends_on_id: "x", dependency_type: "<b>bold</b>" }],
    }],
    ready: ["evil"],
    blocked: [],
  };
  const s = buildSnapshot(baseReads(evil), { fetchedAt: 1 });
  const el = RecordCard({ snapshot: s, id: "evil" });
  const t = texts(el);
  assert.ok(t.some((x) => x.includes("<img src=x")), "malicious string is verbatim text");
  for (const n of walk(el)) {
    if (typeof n === "object" && n.props) {
      assert.equal(n.props.dangerouslySetInnerHTML, undefined, "no raw-HTML prop anywhere");
    }
  }
  // every value an unknown token is a plain string child, not an element tree
  assert.ok(t.every((x) => typeof x === "string"));
});
