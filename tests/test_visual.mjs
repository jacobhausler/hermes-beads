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

test("hbl-pnu.2.10: blocked chip border meets 3:1 non-text contrast on white (real-Chrome finding: Mark fallback = yellow, 1.07:1)", async () => {
  const { WORKBENCH_CSS } = await import("../desktop/workbench_css.mjs");
  const m = WORKBENCH_CSS.match(/\.blocked-chip\s*\{[^}]*var\(--danger,\s*([^)]+)\)/);
  assert.ok(m, "blocked chip border uses a --danger token with an explicit fallback");
  const fb = m[1].trim();
  assert.match(fb, /^#[0-9a-f]{6}$/i, "fallback is a concrete colour, not a system keyword (Mark renders yellow)");
  const ch = (i) => { const v = parseInt(fb.slice(1 + 2 * i, 3 + 2 * i), 16) / 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; };
  const L = 0.2126 * ch(0) + 0.7152 * ch(1) + 0.0722 * ch(2);
  assert.ok(1.05 / (L + 0.05) >= 3, `fallback contrast vs white >= 3:1 (got ${(1.05 / (L + 0.05)).toFixed(2)})`);
});
