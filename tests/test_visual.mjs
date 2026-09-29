// tests/test_visual.mjs — hbl-pnu.2.10: the shipped visual layer (unit tier).
// Fast, DOM-free checks of the stylesheet contract; the mounted tier
// (test_mounted_smoke.mjs T10) proves the same facts in real mounted DOM.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { WORKBENCH_CSS } from "../desktop/workbench_css.mjs";

const here = path.dirname(fileURLToPath(import.meta.url));
const read = (p) => readFileSync(path.join(here, "..", p), "utf8");

test("F1: exactly one stylesheet ships — workbench.css is byte-identical to WORKBENCH_CSS", () => {
  const cssFile = read("desktop/workbench.css");
  assert.equal(cssFile, WORKBENCH_CSS,
    "desktop/workbench.css must be the byte-identical file copy of the shipped string");
});

test("F2: the stylesheet carries the accessibility floor", () => {
  for (const [needle, why] of [
    [":focus-visible", "focus ring target"],
    [/outline\s*:\s*2px\s+solid/, "2px solid focus ring"],
    [/outline-offset/, "ring offset"],
    [/\[aria-selected="true"\]/, "selection cursor styled"],
    [/\[data-keyboard-focus="true"\]/, "keyboard-focus marker styled (never attribute-only)"],
    ["(prefers-reduced-motion: reduce)", "motion preference honoured"],
  ]) {
    if (typeof needle === "string") {
      assert.ok(WORKBENCH_CSS.includes(needle), `${why}: ${needle} missing`);
    } else {
      assert.match(WORKBENCH_CSS, needle, `${why} missing`);
    }
  }
});

test("theme tokens fall back to system colors; sizes are rem, never px widths", () => {
  for (const v of ["--foreground, CanvasText", "--accent, Highlight", "--border, GrayText"]) {
    assert.ok(new RegExp(`var\\(${v.replace(",", "\\s*,\\s*")}\\)`).test(WORKBENCH_CSS),
      `fallback pair missing: ${v}`);
  }
  assert.match(WORKBENCH_CSS, /font-size:\s*1rem/, "root font-size in rem");
  assert.ok(!/width\s*:\s*\d+(\.\d+)?px/.test(WORKBENCH_CSS),
    "stylesheet must not declare fixed px widths (reflow law)");
  assert.match(WORKBENCH_CSS, /40rem/, "wrap point declared in rem");
});

test("layout: tree left, panels right, flex-wrap under 40rem", () => {
  assert.match(WORKBENCH_CSS, /#workbench\s*\{[^}]*display:\s*flex/, "root is flex");
  assert.match(WORKBENCH_CSS, /flex-wrap:\s*wrap/, "wraps");
  assert.match(WORKBENCH_CSS, /@media\s*\(max-width:\s*40rem\)/, "wrap point");
});

test("the shipped root renders the stylesheet exactly once (source truth)", () => {
  const src = read("desktop/workbench.mjs");
  assert.equal((src.match(/jsx\("style"/g) ?? []).length, 1,
    "exactly one <style> render in the shipped root");
  assert.ok(src.includes("WORKBENCH_CSS"), "root injects the exported constant");
});

test("F4-F7 field separation is structural, not paint-adjacent text", () => {
  const tree = read("desktop/tree.mjs");
  assert.ok(tree.includes('className: "status-chip"'), "row status chip element");
  assert.ok(tree.includes('aria-label": `status:'), "status chip labelled");
  assert.ok(tree.includes('className: "progress-chip"'), "progress chip element");
  assert.ok(tree.includes('className: "blocked-chip"'), "blocked chip element");
  const blockers = read("desktop/blockers.mjs");
  assert.ok(blockers.includes('className: "blocker-id"'), "blocker id separated element");
  assert.ok(blockers.includes("title"), "blocker titles surfaced");
  assert.ok(blockers.includes('children: " \\u203A "'), "ancestry ' › ' separator element");
  const bot = read("desktop/bot_action.mjs");
  assert.ok(bot.includes('"aria-label": "Bot actions"'), "bot panel is a labelled group");
  assert.ok(bot.includes('role: "group"'), "bot panel role=group");
});
