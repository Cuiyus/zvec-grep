import test from "node:test";
import assert from "node:assert/strict";
import { validateReplayUnit, replayUnit } from "./replay-search.mjs";

const unit = () => ({
  unit_id: "faithful-a",
  kind: "faithful",
  request: {
    root: "/app",
    queries: ["getter"],
    routes: [{ mode: "vector", query: '["getter", "dependencies"]' }],
    autoUpdate: false,
    trace: true,
  },
});

test("native replay preserves omitted defaults and an array-looking string as one route", () => {
  const input = unit();
  const result = validateReplayUnit(input, "/app");
  assert.deepEqual(result, input.request);
  assert.equal(result.routes.length, 1);
  assert.equal(typeof result.routes[0].query, "string");
  assert.equal(Object.hasOwn(result, "limit"), false);
  result.queries.push("mutated");
  assert.equal(input.request.queries.length, 1);
});

test("replay rejects corpus changes, updates, non-indexed routes and unsupported fields", () => {
  for (const patch of [
    { root: "/elsewhere" },
    { autoUpdate: true },
    { rg: "pattern" },
    { routes: [{ mode: "rg", query: "pattern" }] },
    { query: [] },
  ]) {
    assert.throws(() =>
      validateReplayUnit(
        { ...unit(), request: { ...unit().request, ...patch } },
        "/app",
      ),
    );
  }
});

test("five fixed attempts retain a failure without replacement or mutation of later queries", async () => {
  const calls = [];
  const events = [];
  const runtime = {
    root: "/app",
    async search(request, metadata) {
      calls.push(structuredClone(request));
      request.queries[0] = "internal mutation";
      if (metadata.repetition === 2) throw new Error("temporary failure");
      return { event: { ...metadata, status: "success" } };
    },
  };
  assert.equal(await replayUnit(runtime, unit(), 5, (e) => events.push(e)), 1);
  assert.equal(calls.length, 5);
  assert.equal(events.length, 5);
  assert.deepEqual(
    events.map((e) => e.status),
    ["success", "error", "success", "success", "success"],
  );
  assert(calls.every((request) => request.queries[0] === "getter"));
});
