import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import test from "node:test";
import {
  parseVisibleResponse,
  scoreResponse,
  VisibleFormatError,
} from "../scoring.mjs";
import { scoreFileRetrieval } from "../file-retrieval-metrics.mjs";

const sha = (s) => createHash("sha256").update(s).digest("hex");
const response = (body, extra = {}) => ({
  content: [{ type: "text", text: `freshness: fresh\n${body}` }],
  ...extra,
});
const anchor = (text = "def wanted():", start = 20) => ({
  start_line: start,
  end_line: start + text.split("\n").length - 1,
  text,
  sha256: sha(text),
});
function target(id = "wanted", options = {}) {
  return {
    id,
    path: "pkg/main.py",
    kind: "symbol",
    symbol: "wanted",
    source_sha256: "a".repeat(64),
    anchors: [anchor()],
    role: "accepted",
    relevance_reason: "Defines the queried mechanism",
    ...options,
  };
}
function gold(
  targets = [target()],
  ndcg = {
    enabled: false,
    reason: "Partial positive entry labels",
    groups: [],
  },
) {
  return {
    schema_version: 1,
    gold_version: "sweqa20-entry-v1",
    task_id: "example:1",
    question_sha256: "b".repeat(64),
    repository: "example/repo",
    repository_commit: "c".repeat(40),
    status: "reviewed",
    targets,
    ndcg,
    review: { proposer: "a", reviewer: "b" },
  };
}
const item = (
  rank,
  body = "source:\n20\tdef wanted():",
  location = "pkg/main.py:20-40",
) => `#${rank} matchedBy=fts+vector ${location}\n${body}`;
const filler = (rank) =>
  item(rank, "source:\n2\tdef other():", "pkg/main.py:2-5");
const atRank = (rank) =>
  response(
    [
      ...Array.from({ length: rank - 1 }, (_, i) => filler(i + 1)),
      item(rank),
    ].join("\n\n"),
  );

const removedFields = [
  "first_hit_rank",
  "hit_at_1",
  "hit_at_5",
  "hit_at_10",
  "rr_at_10",
  "ndcg_at_5",
  "ndcg_at_10",
  "target_matches",
];
const assertNoLegacyScores = (score) => {
  for (const key of removedFields)
    assert.equal(Object.hasOwn(score, key), false, key);
};

for (const rank of [1, 5, 10]) {
  test(`public parser preserves all ${rank} native ranks without file compaction`, () => {
    const parsed = scoreResponse(atRank(rank), gold());
    assert.equal(parsed.status, "scored");
    assert.equal(parsed.execution_status, "success");
    assert.equal(parsed.items.length, rank);
    assert.deepEqual(
      parsed.items.map((item) => item.rank),
      Array.from({ length: rank }, (_, i) => i + 1),
    );
    assertNoLegacyScores(parsed);
  });
}

test("file relevance is independent of source text, outlines and declaration location", () => {
  const files = [{ path: "pkg/main.py" }];
  for (const body of [
    item(1, "source:\n20\tdef other():"),
    item(1, "symbol: function wanted"),
    item(
      1,
      "outline:\nclass Parent:\n\nmembers:\n- function wanted",
      "pkg/main.py:1-400",
    ),
    item(1, "outline:\ndef wanted():", "pkg/main.py:19-40"),
    item(1, "source:\n24\t    state = loo...\n..."),
  ]) {
    const parsed = scoreResponse(response(body), gold());
    assert.equal(parsed.status, "scored");
    assert.equal(scoreFileRetrieval(parsed.items, files).hit_at_1, 1);
    assertNoLegacyScores(parsed);
  }
});

test("lexical line markers, whitespace and matched source ranges remain exact public evidence", () => {
  const parsed = scoreResponse(
    response(
      item(
        1,
        "matched: 28\nsource:\n27-\t  before\n28:\treturn result\n29-\t\tafter",
      ),
    ),
    gold(),
  );
  assert.equal(parsed.status, "scored");
  assert.deepEqual(parsed.items[0].matched_range, {
    kind: "text",
    start_line: 28,
    end_line: 28,
  });
  assert.deepEqual(parsed.items[0].source_lines, [
    { line: 27, text: "  before" },
    { line: 28, text: "return result" },
    { line: 29, text: "\tafter" },
  ]);
  assertNoLegacyScores(parsed);
});

test("explicit empty is valid; missing, unrelated and malformed formats invalidate the harness", () => {
  for (const label of ["No matches.", "No searchable files."]) {
    const parsed = scoreResponse(response(label), gold());
    assert.equal(parsed.status, "scored");
    assert.deepEqual(parsed.items, []);
    assertNoLegacyScores(parsed);
  }
  for (const bad of [
    response(""),
    response("Nothing found"),
    response('{"items": []}'),
    response(item(2)),
    response(`${item(1)}\nunknown renderer line`),
    { content: [] },
    { content: [{ type: "text", text: item(1) }] },
    response(item(1, "source:\n20\tdef wanted():\n20\tdef wanted():")),
    response(item(1, "source:\n100\tdef wanted():")),
    response(item(1, "", "../pkg/main.py:20-40")),
    response(item(1) + "\r"),
    response(item(1) + "\x1b[0m"),
  ]) {
    assert.throws(() => parseVisibleResponse(bad), VisibleFormatError);
    const parsed = scoreResponse(bad, gold());
    assert.equal(parsed.status, "harness_invalid");
    assertNoLegacyScores(parsed);
  }
});

test("product failures and unreviewed Gold retain eligibility but never emit legacy metric fields", () => {
  const error = {
    isError: true,
    content: [{ type: "text", text: "Index unavailable" }],
  };
  const failed = scoreResponse(error, gold());
  assert.equal(failed.status, "product_error");
  assert.equal(failed.execution_status, "product_error");
  assert.deepEqual(failed.items, []);
  assertNoLegacyScores(failed);
  for (const status of ["unknown", "disputed"]) {
    const parsed = scoreResponse(error, { ...gold(), status });
    assert.equal(parsed.status, `gold_${status}`);
    assertNoLegacyScores(parsed);
  }
});

test("frozen Gold integrity is still validated even though anchors and groups are no longer scored", () => {
  const reviewed = gold([target()], {
    enabled: true,
    groups: [{ id: "g1", target_ids: ["wanted"] }],
  });
  const parsed = scoreResponse(
    response(item(1, "source:\n20\tdef unrelated():")),
    reviewed,
  );
  assert.equal(parsed.status, "scored");
  assertNoLegacyScores(parsed);
  for (const mutate of [
    (g) => {
      g.targets[0].anchors[0].sha256 = "0".repeat(64);
    },
    (g) => {
      g.targets[0].source_sha256 = "bad";
    },
    (g) => {
      g.targets[0].path = "../main.py";
    },
    (g) => {
      g.ndcg.groups[0].target_ids = ["missing"];
    },
  ]) {
    const corrupt = structuredClone(reviewed);
    mutate(corrupt);
    const invalid = scoreResponse(atRank(1), corrupt);
    assert.equal(invalid.status, "harness_invalid");
    assert.match(invalid.invalid_reason, /^gold_invalid:/);
    assertNoLegacyScores(invalid);
  }
});

test("visible and structured hashes remain separate; hidden source never augments public items", () => {
  const a = scoreResponse(
    response(item(1), { structuredContent: { a: 1, b: 2 } }),
    gold(),
  );
  const b = scoreResponse(
    response(item(1), { structuredContent: { b: 2, a: 1 } }),
    gold(),
  );
  assert.equal(a.visible_output_sha256, b.visible_output_sha256);
  assert.equal(a.structured_output_sha256, b.structured_output_sha256);
  assert.equal(a.ranking_sha256, b.ranking_sha256);
  const hidden = scoreResponse(
    response(item(1, "symbol: function wanted"), {
      structuredContent: { content: "def wanted():" },
    }),
    gold(),
  );
  assert.deepEqual(hidden.items[0].source_lines, []);
  assert.deepEqual(hidden.items[0].outline, []);
  assertNoLegacyScores(hidden);
});

test("native non-code range remains a file result without invented source positions", () => {
  const parsed = parseVisibleResponse(
    response(item(1, "heading: Notes", "docs/notes.pdf:page:2")),
  );
  assert.equal(parsed.items[0].path, "docs/notes.pdf");
  assert.deepEqual(parsed.items[0].range, { kind: "other", label: "page:2" });
  assert.equal(
    scoreFileRetrieval(parsed.items, [{ path: "docs/notes.pdf" }]).hit_at_1,
    1,
  );
});
