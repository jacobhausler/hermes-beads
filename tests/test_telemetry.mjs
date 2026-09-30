// tests/test_telemetry.mjs — council S3 step 2 (2026-09-30): pane telemetry is
// the PRECONDITION for any future generic-pane deletion (fable caveat in the
// council READOUT: delete on observed non-use, never on maintenance-cost alone).
// Pure ring buffer + emit-whitelist + serialise; NO I/O in the desktop layer
// (the host shell owns persistence through the injected slot).
import test from "node:test";
import assert from "node:assert/strict";
import { createTelemetry, EVENTS, serialise } from "../desktop/telemetry.mjs";

test("records known events with storeKey, counts uses", () => {
  const t = createTelemetry({ storeKey: "dolt embedded/hbl" });
  t.emit("search-open");
  t.emit("search-open");
  t.emit("blockers-card-open");
  const u = t.uses();
  assert.equal(u["dolt embedded/hbl"]["search-open"], 2);
  assert.equal(u["dolt embedded/hbl"]["blockers-card-open"], 1);
});

test("unknown event names are dropped, never recorded (no unbounded keys)", () => {
  const t = createTelemetry({ storeKey: "s" });
  t.emit("anything-goes");
  t.emit("__proto__");
  assert.deepEqual(t.uses(), {});
});

test("missing storeKey falls to 'unknown'; empty counts are never serialised", () => {
  const t = createTelemetry({});
  t.emit("bot-action-click");
  const u = t.uses();
  assert.equal(u.unknown["bot-action-click"], 1);
  t.emit("drafts-save");
  assert.deepEqual(JSON.parse(serialise({
    "s1": { "search-open": 3, "tree-jump": 0 },
    "s2": {},
  })), { s1: { "search-open": 3 } });
});

test("ring is bounded and FIFO; EVENTS lists every instrumented pane", () => {
  const t = createTelemetry({ storeKey: "s", max: 3 });
  for (const e of ["search-open", "search-activate", "blockers-jump", "drafts-save"]) t.emit(e);
  assert.deepEqual(t.events.map((x) => x.e), ["search-activate", "blockers-jump", "drafts-save"]);
  assert.deepEqual([...EVENTS].sort(), [
    "blockers-card-open", "blockers-jump", "bot-action-click",
    "drafts-save", "search-open", "search-activate", "tree-jump",
  ].sort());
});
