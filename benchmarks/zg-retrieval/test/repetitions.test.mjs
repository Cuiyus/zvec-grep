import assert from "node:assert/strict";
import test from "node:test";
import { summarizeRepeatedQuality } from "../metrics/repetitions.mjs";

const target = [{ path: "target.py" }];
const items = (rank, range = { kind: "text", start_line: 1, end_line: 3 }) =>
  [1, 2].map((position) => ({
    rank: position,
    path: position === rank ? "target.py" : `other-${position}.py`,
    range,
    matched_by: "fts",
  }));

test("five calls have one query vote and expose intermittent retrieval", () => {
  const calls = [1, 1, 2, null, 1].map((rank, index) => ({
    repetition: index + 1,
    items: items(rank),
  }));
  const quality = summarizeRepeatedQuality(calls, target);
  assert.equal(quality.file.hit_at_1, 3 / 5);
  assert.equal(quality.file.hit_at_5, 4 / 5);
  assert.equal(quality.file.hit_at_10, 4 / 5);
  assert.equal(quality.file.rr_at_10, 3.5 / 5);
  assert.equal(quality.hit_at_10_calls, 4);
  assert.equal(quality.ranking_repeatable, false);
  assert.equal(quality.unique_rankings, 3);
});

test("equal quality scores do not imply stable ranked locations", () => {
  const calls = [1, 2, 3, 4, 5].map((repetition) => ({
    repetition,
    items: items(1, {
      kind: "text",
      start_line: repetition,
      end_line: repetition + 2,
    }),
  }));
  const quality = summarizeRepeatedQuality(calls, target);
  assert.equal(quality.file.hit_at_1, 1);
  assert.equal(quality.ndcg_at_10, 1);
  assert.equal(quality.ranking_repeatable, false);
});
