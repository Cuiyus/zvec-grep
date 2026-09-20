import assert from "node:assert/strict";
import test from "node:test";
import {
  FILE_RETRIEVAL_CONTRACT,
  scoreFileRetrieval,
  fileRetrievalForRow,
  summarizeFileRetrieval,
} from "../metrics/files.mjs";

const targets = [{ path: "src/answer.py" }, { path: "src/helper.py" }];
const itemsAt = (rank) =>
  Array.from({ length: 11 }, (_, index) => ({
    rank: index + 1,
    path: index + 1 === rank ? "src/answer.py" : "src/unrelated.py",
    range: { kind: "text", start_line: 1, end_line: 10 },
  }));
const rowAt = (rank) => ({
  status: "scored",
  execution_status: "success",
  gold_status: "reviewed",
  items: itemsAt(rank),
  ndcg: { targets },
});

test("standard file Hit and reciprocal rank cover cutoff boundaries and misses", () => {
  assert.equal(FILE_RETRIEVAL_CONTRACT, "sweqa-file-hit-rr-v1");
  for (const rank of [1, 2, 5, 6, 10, 11, null]) {
    const result = scoreFileRetrieval(itemsAt(rank), targets);
    const found = rank !== null && rank <= 10;
    assert.equal(result.first_hit_rank, found ? rank : "not_in_top10");
    for (const cutoff of [1, 5, 10])
      assert.equal(result[`hit_at_${cutoff}`], Number(found && rank <= cutoff));
    assert.equal(result.rr_at_10, found ? 1 / rank : 0);
  }
});

test("native duplicate chunks consume ranks and the first of any target wins", () => {
  const items = itemsAt(5);
  items[1].path = "src/helper.py";
  items[2].path = "src/helper.py";
  const result = scoreFileRetrieval(items, targets);
  assert.deepEqual(result.target_ranks, [5, 2]);
  assert.equal(result.first_hit_rank, 2);
  assert.equal(result.rr_at_10, 0.5);
  assert.throws(
    () => scoreFileRetrieval([{ rank: 5, path: "src/answer.py" }], targets),
    /native ranks without compaction/,
  );
});

test("file relevance uses upstream path matching and ignores outlines and source visibility", () => {
  const short = itemsAt(5);
  short[4].path = "/locked/repo/src/answer.py";
  const full = structuredClone(short);
  full[4].source_lines = [{ line: 100, text: "arbitrary body" }];
  full[4].outline = ["unrelated signature"];
  full[4].range = { kind: "text", start_line: 100, end_line: 100 };
  assert.deepEqual(
    scoreFileRetrieval(short, targets),
    scoreFileRetrieval(full, targets),
  );
  assert.equal(
    scoreFileRetrieval([{ rank: 1, path: "SRC/answer.py" }], targets).hit_at_10,
    0,
  );
});

test("file targets must be nonempty, unique and free of source spans", () => {
  assert.throws(() => scoreFileRetrieval([], []), /nonempty targets/);
  assert.throws(
    () => scoreFileRetrieval([], [{ path: "a.py", start_line: 1 }]),
    /file-only/,
  );
  assert.throws(
    () => scoreFileRetrieval([], [{ path: "a\\b.py" }, { path: "a/b.py" }]),
    /unique/,
  );
  assert.throws(
    () => scoreFileRetrieval([], [{ path: "" }]),
    /must not be empty/,
  );
});

test("invalid or unreviewed observations are null; product errors score zero and cannot claim items", () => {
  for (const patch of [
    { status: "harness_invalid" },
    { execution_status: "harness_invalid" },
    { status: "gold_unreviewed" },
    { gold_status: "draft" },
  ])
    assert.equal(fileRetrievalForRow({ ...rowAt(1), ...patch }), null);
  const error = {
    ...rowAt(1),
    status: "product_error",
    execution_status: "product_error",
    items: [],
  };
  assert.equal(fileRetrievalForRow(error).rr_at_10, 0);
  assert.throws(
    () => fileRetrievalForRow({ ...error, items: itemsAt(1) }),
    /cannot provide/,
  );
});

test("row and query-mean summaries recompute public ranks instead of trusting cached scores", () => {
  const rows = [
    rowAt(1),
    rowAt(5),
    rowAt(null),
    { ...rowAt(1), status: "harness_invalid" },
  ];
  for (const row of rows) {
    row.file_retrieval = { first_hit_rank: 1, rr_at_10: 999, hit_at_10: 999 };
    row.ndcg.target_ranks = [1, 1];
    row.first_hit_rank = 1;
    row.rr_at_10 = 999;
  }
  assert.equal(fileRetrievalForRow(rows[1]).rr_at_10, 0.2);
  const summary = summarizeFileRetrieval(rows);
  assert.equal(summary.planned_tasks, 4);
  assert.equal(summary.scored_tasks, 3);
  assert.equal(summary.hit_at_1_count, 1);
  assert.equal(summary.hit_at_5_count, 2);
  assert.equal(summary.hit_at_10, 2 / 3);
  assert.equal(summary.mrr_at_10, (1 + 0.2) / 3);
  assert.equal(
    summarizeFileRetrieval([{ status: "harness_invalid" }]).mrr_at_10,
    null,
  );
});
