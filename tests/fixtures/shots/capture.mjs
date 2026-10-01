// tests/fixtures/shots/capture.mjs — seed a DEMO store, capture every read the
// pane needs into world.json, destroy the store. Evidence harness only: no
// product code lives here, nothing in this directory ships.
// Run: node tests/fixtures/shots/capture.mjs
import { execFileSync } from "node:child_process";
import { writeFileSync, mkdirSync, rmSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(here, "..", "..", "..");
const BD = process.env.BEADS_LAB_BD || "bd";
const J = (argv) => JSON.parse(execFileSync("python3", argv,
  { encoding: "utf8", maxBuffer: 1 << 26 }));

const store = execFileSync("python3",
  [path.join(REPO, "tests", "fixtures", "scenarios", "make_store.py"),
   "seed", "--prefix", "plat"],
  { encoding: "utf8", maxBuffer: 1 << 26 });
const S = JSON.parse(store).store;

// act/create via the seeder passthrough so actor/ownership stay clean
const act = (...argv) => J([path.join(REPO, "tests", "fixtures", "scenarios",
  "make_store.py"), "act", S, "plat-actor", ...argv]);
const read = (name, ...a) => {
  const o = J([path.join(REPO, "tests", "fixtures", "scenarios",
    "make_store.py"), "read", S, name, ...a]);
  return o.rc === 0 ? o.payload : null;
};

// Re-title the fixture world into a believable demo project: the pane should
// screenshot like a team's real graph, not like a test lab.
const RENAMES = {
  "Hermes Beads laboratory": "Q1 platform hardening",
  "branch A": "API",
  "branch A gate": "auth refactor",
  "branch B": "UI",
  "branch B blocked task": "billing dashboard",
  "plain ready task": "empty states",
  "claimed row": "session cache",
  "direct blocker": "rate limiter",
  "multi blocker one": "webhook retry",
  "multi blocker two": "queue drain",
  "inherit gate": "event bus",
  "inherited victim": "notification fan-out",
  "dead worker task": "log compaction",
  "conflict edit target": "export formats",
  "move host A": "mobile nav",
  "move host B": "deep links",
  "move me": "share sheet",
};

try {
  const list = read("list_all");
  for (const row of list) {
    const want = RENAMES[row.title];
    if (want) act("update", row.id, "--title", want, "--json");
  }
  // a realistic extras row the fixture world lacks
  act("create", "nightly flake triage", "--json");

  const list_all = read("list_all");
  const blocked = read("blocked");
  const world = JSON.parse(store);
  const ids = world.ids;
  const shows = {}, deptrees = {};
  for (const r of list_all) shows[r.id] = read("show", r.id);
  for (const id of Object.values(ids)) {
    if (typeof id === "string") deptrees[id] = read("deptree", id);
  }
  const search = JSON.parse(execFileSync(BD, ["-C", S, "--readonly",
    "--actor", "plat-actor", "search", "dashboard", "--limit", "25",
    "--json"], { encoding: "utf8" }));
  const out = {
    captured_at: new Date().toISOString(),
    bd: `${execFileSync(BD, ["version"], { encoding: "utf8" }).trim()}, --readonly`,
    storeInfo: world.storeInfo, ids, list_all, blocked, shows, deptrees,
    search: { dashboard: search },
  };
  mkdirSync(path.join(here, "out"), { recursive: true });
  writeFileSync(path.join(here, "out", "world.json"), JSON.stringify(out));
  console.log("captured", list_all.length, "rows into out/world.json");
} finally {
  execFileSync("python3", [path.join(REPO, "tests", "fixtures", "scenarios",
    "make_store.py"), "cleanup", S]);
}
