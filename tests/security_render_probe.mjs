// tests/security_render_probe.mjs — hbl-pnu.4.4 hostile-content render gate.
// Renders hostile bead payloads through the REAL desktop components
// (model.mjs buildSnapshot -> tree.mjs Tree + record.mjs RecordCard) under
// the jsx-capture shim and asserts the payload is INERT: present only as a
// verbatim escaped text child; no dangerouslySetInnerHTML prop anywhere; no
// function props; the desktop sources themselves contain no
// dangerouslySetInnerHTML at all.
//
//   --real    <json map>   render through the real components; exit 1 with
//                          "INERT-FAIL:<name>" if a payload never appears as
//                          a text child, "ACTIVE-MARKUP" if an injection
//                          prop shows up, "SRC-SOURCE-MARKUP" if a desktop
//                          source file carries dangerouslySetInnerHTML.
//   --control <json map>   render the SAME payloads through an intentionally
//                          unsafe renderer (dangerouslySetInnerHTML) — this
//                          MUST exit nonzero with ACTIVE-MARKUP; the Python
//                          negative control asserts that.
import { readFileSync } from "node:fs";
import { register } from "node:module";
import { fileURLToPath, pathToFileURL } from "node:url";
import path from "node:path";

const here = path.dirname(fileURLToPath(import.meta.url));
const lane = path.dirname(here);
register(pathToFileURL(path.join(here, "__shims__", "jsx-loader.mjs")).href);

const shim = await import(pathToFileURL(path.join(here, "__shims__", "jsx-capture.mjs")).href);
const { buildSnapshot, createWorkbenchState } = await import("../desktop/model.mjs");
const { Tree } = await import("../desktop/tree.mjs");
const { RecordCard } = await import("../desktop/record.mjs");
const { buildTreeRows } = await import("../desktop/tree.mjs");

const mode = process.argv[2];
const payloads = JSON.parse(process.argv[3]);

function fail(code, detail) {
  process.stderr.write(`${code}: ${detail}\n`);
  process.exit(1);
}

// any injection-capable props anywhere in a captured tree
function* propsOf(node) {
  if (node && typeof node === "object") {
    if (node.props && typeof node.props === "object") {
      for (const [k, v] of Object.entries(node.props)) {
        yield [k, v];
        if (k === "dangerouslySetInnerHTML") yield ["__INJECTION__", v];
        if (typeof v === "function") yield ["__FUNCTION-PROP__", k];
      }
    }
    for (const kid of node.children ?? []) yield* propsOf(kid);
  }
}

const baseReads = (issues) => ({
  issues, ready: [], blocked: [],
  storeInfo: { workspace: "/lab/store", db: "/lab/store/.beads/lab.db" },
});

if (mode === "--real") {
  // source-level gate: the real desktop modules never touch innerHTML
  const srcFiles = [
    "desktop/tree.mjs", "desktop/record.mjs", "desktop/model.mjs",
    "desktop/blockers.mjs", "desktop/compare.mjs", "desktop/drafts.mjs",
    "desktop/history.mjs", "desktop/search.mjs", "desktop/bot_action.mjs",
  ];
  for (const f of srcFiles) {
    const src = readFileSync(path.join(lane, f), "utf8")
      // strip comments so prose like "no dangerouslySetInnerHTML" does not
      // false-positive; code-level uses still match
      .replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
    if (/dangerouslySetInnerHTML|\.innerHTML\s*=|insertAdjacentHTML/.test(src)) {
      fail("SRC-SOURCE-MARKUP", f);
    }
  }

  const issues = [];
  let n = 0;
  for (const [name, payload] of Object.entries(payloads)) {
    const id = `hostile-${n++}`;
    issues.push({ id, title: payload, description: payload, status: "open" });
  }
  const snap = buildSnapshot(baseReads(issues), { fetchedAt: 1 });
  const ui = createWorkbenchState(snap);

  n = 0;
  for (const [name, payload] of Object.entries(payloads)) {
    const id = `hostile-${n++}`;
    const treeEl = Tree({ snapshot: snap, ui });
    const recEl = RecordCard({ snapshot: snap, id });
    const rows = buildTreeRows(snap, ui);

    for (const [label, el] of [["tree", treeEl], ["record", recEl]]) {
      for (const [k] of propsOf(el)) {
        if (k === "__INJECTION__") fail("ACTIVE-MARKUP", `${name}/${label} html prop`);
        if (k === "__FUNCTION-PROP__") fail("ACTIVE-MARKUP", `${name}/${label} fn prop`);
      }
    }
    // payload must be present as a verbatim TEXT child (rowLabel/title path)
    const texts = [...shim.walk(rows.rows.length ? { children: rows.rows.map(r => r.title) } : treeEl)]
      .filter((x) => typeof x === "string");
    const treeTexts = [...shim.walk(treeEl)].filter((x) => typeof x === "string");
    const all = texts.concat(treeTexts);
    if (!all.some((t) => typeof t === "string" && t.includes(payload))) {
      fail("INERT-FAIL", `${name}: payload never rendered as text`);
    }
  }
  process.stdout.write(`OK real ${Object.keys(payloads).length}\n`);
} else if (mode === "--control") {
  // intentionally unsafe renderer — simulates a regression that trusts
  // stored content; the walker MUST catch the injection prop.
  const { jsx } = await import("react/jsx-runtime");
  for (const [name, payload] of Object.entries(payloads)) {
    const el = jsx("div", { dangerouslySetInnerHTML: { __html: payload } }, `c:${name}`);
    for (const [k] of propsOf(el)) {
      if (k === "__INJECTION__") fail("ACTIVE-MARKUP", `${name}: control html prop caught`);
    }
  }
  fail("CONTROL-LOST", "unsafe renderer was NOT caught");
} else {
  fail("USAGE", "expected --real|--control <json>");
}
