import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import test from "node:test";
import {
  parseVisibleResponse,
  scoreResponse,
  VisibleFormatError,
} from "../scoring.mjs";

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

for (const rank of [1, 5, 10]) {
  test(`native rank ${rank} determines Hit and RR without file compaction`, () => {
    const score = scoreResponse(atRank(rank), gold());
    assert.equal(score.status, "scored");
    assert.equal(score.first_hit_rank, rank);
    assert.equal(score.hit_at_1, Number(rank === 1));
    assert.equal(score.hit_at_5, Number(rank <= 5));
    assert.equal(score.hit_at_10, 1);
    assert.equal(score.rr_at_10, 1 / rank);
    assert.equal(score.ndcg_at_10, null);
    assert.equal(score.items.length, rank);
  });
}

test("wrong function, path-only and hidden parent metadata do not earn source credit", () => {
  for (const body of [
    item(1, "source:\n20\tdef other():"),
    item(1, "symbol: function wanted"),
    item(
      1,
      "outline:\nclass Parent:\n\nmembers:\n- function wanted",
      "pkg/main.py:1-400",
    ),
  ]) {
    const score = scoreResponse(
      response(body, {
        structuredContent: { content: "def wanted():", startLine: 20 },
      }),
      gold(),
    );
    assert.equal(score.status, "scored");
    assert.equal(score.hit_at_10, 0);
    assert.equal(score.first_hit_rank, "not_in_top10");
  }
});

test("outline accepts the exact leading definition at its own source start", () => {
  const native = response(item(1, "outline:\ndef wanted():\n\ncalls: helper"));
  assert.equal(
    scoreResponse(native, gold()).target_matches[0].via,
    "definition_outline",
  );
  assert.equal(
    scoreResponse(
      response(item(1, "outline:\ndef wanted():", "pkg/main.py:19-40")),
      gold(),
    ).hit_at_10,
    0,
  );
  assert.equal(
    scoreResponse(native, gold([target("other", { symbol: "other" })]))
      .hit_at_10,
    0,
  );
  assert.equal(
    scoreResponse(
      response(item(1, "outline:\nclass Parent:\n    def wanted():")),
      gold(),
    ).hit_at_10,
    0,
  );
  const parentAnchor = gold([
    target("bad-parent", {
      anchors: [anchor("class Parent:\n    def wanted():")],
    }),
  ]);
  assert.equal(
    scoreResponse(
      response(item(1, "outline:\nclass Parent:\n    def wanted():")),
      parentAnchor,
    ).hit_at_10,
    0,
  );
});

test("visible source anchors match exact whitespace, position and every complete line", () => {
  const code = "    state = lookup()\n    return state";
  const g = gold([
    target("body", { kind: "code_span", anchors: [anchor(code, 24)] }),
  ]);
  assert.equal(
    scoreResponse(
      response(
        item(1, "source:\n24\t    state = lookup()\n25\t    return state"),
      ),
      g,
    ).hit_at_10,
    1,
  );
  for (const source of [
    "source:\n24\t    state = lookup()\n...",
    "source:\n24\t    state = loo...\n25\t    return state",
    "source:\n24\tstate = lookup()\n25\treturn state",
    "source:\n23\t    state = lookup()\n24\t    return state",
  ])
    assert.equal(scoreResponse(response(item(1, source)), g).hit_at_10, 0);
  const split = response(
    `${item(1, "source:\n24\t    state = lookup()")}\n\n${item(2, "source:\n25\t    return state")}`,
  );
  assert.equal(scoreResponse(split, g).hit_at_10, 0);
  assert.equal(
    scoreResponse(response(item(1, `outline:\n${code}`)), g).hit_at_10,
    0,
  );
});

test("source anchor alternatives and lexical source markers are supported", () => {
  const g = gold([
    target("wanted", { anchors: [anchor(), anchor("return result", 28)] }),
  ]);
  const score = scoreResponse(
    response(
      item(
        1,
        "matched: 28\nsource:\n27-\tbefore\n28:\treturn result\n29-\tafter",
      ),
    ),
    g,
  );
  assert.equal(score.hit_at_10, 1);
  assert.equal(score.target_matches[0].anchor_index, 1);
});

test("AST first-line indentation omission is bound to the public source start", () => {
  const g = gold([
    target("method", { anchors: [anchor("    def wanted():", 20)] }),
  ]);
  assert.equal(scoreResponse(response(item(1)), g).hit_at_10, 1);
  assert.equal(
    scoreResponse(
      response(
        item(
          1,
          "matched: 20-40\nsource:\n20\tdef wanted():",
          "pkg/main.py:1-400",
        ),
      ),
      g,
    ).hit_at_10,
    1,
  );
  assert.equal(
    scoreResponse(
      response(item(1, "source:\n20\tdef wanted():", "pkg/main.py:1-400")),
      g,
    ).hit_at_10,
    0,
  );
});

test("explicit empty is valid; missing, unrelated and malformed formats invalidate the harness", () => {
  for (const label of ["No matches.", "No searchable files."]) {
    assert.deepEqual(parseVisibleResponse(response(label)).items, []);
    assert.equal(scoreResponse(response(label), gold()).hit_at_10, 0);
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
  ]) {
    assert.throws(() => parseVisibleResponse(bad), VisibleFormatError);
    const score = scoreResponse(bad, gold());
    assert.equal(score.status, "harness_invalid");
    assert.equal(score.hit_at_10, null);
    assert.equal(score.rr_at_10, null);
  }
});

test("product errors score zero, but unreviewed Gold and corrupt labels are N/A", () => {
  const error = {
    isError: true,
    content: [{ type: "text", text: "Index unavailable" }],
  };
  assert.equal(scoreResponse(error, gold()).status, "product_error");
  assert.equal(scoreResponse(error, gold()).hit_at_10, 0);
  for (const status of ["unknown", "disputed"]) {
    const score = scoreResponse(error, { ...gold(), status });
    assert.equal(score.status, `gold_${status}`);
    assert.equal(score.hit_at_10, null);
  }
  const corrupted = gold();
  corrupted.targets[0].anchors[0].sha256 = "0".repeat(64);
  const invalid = scoreResponse(atRank(1), corrupted);
  assert.equal(invalid.status, "harness_invalid");
  assert.match(invalid.invalid_reason, /^gold_invalid:/);
});

test("bridge hits are recorded but cannot contribute to entry metrics", () => {
  const score = scoreResponse(
    atRank(1),
    gold([
      target("entry", { path: "pkg/else.py" }),
      target("bridge", { role: "bridge" }),
    ]),
  );
  assert.equal(score.target_matches.length, 1);
  assert.equal(score.target_matches[0].role, "bridge");
  assert.equal(score.hit_at_10, 0);
});

test("nDCG gives no repeated group credit and respects cutoffs", () => {
  const g = gold([target()], {
    enabled: true,
    reason: "Reviewed relevance unit",
    groups: [{ id: "g1", target_ids: ["wanted"] }],
  });
  assert.equal(
    scoreResponse(response(`${item(1)}\n\n${item(2)}`), g).ndcg_at_10,
    1,
  );
  const score = scoreResponse(atRank(10), g);
  assert.equal(score.ndcg_at_5, 0);
  assert.equal(score.ndcg_at_10, 1 / Math.log2(11));
  const error = scoreResponse({ isError: true }, g);
  assert.equal(error.ndcg_at_10, 0);
});

test("overlapping nDCG groups require maximum-discount injective matching", () => {
  const a = target("a");
  const b = target("b", {
    anchors: [anchor("def second():", 30)],
    symbol: "second",
  });
  const g = gold([a, b], {
    enabled: true,
    reason: "Two complementary units",
    groups: [
      { id: "flexible", target_ids: ["a", "b"] },
      { id: "a-only", target_ids: ["a"] },
    ],
  });
  // Greedily assigning rank 1 to flexible would incorrectly strand a-only.
  const score = scoreResponse(
    response(`${item(1)}\n\n${item(2, "source:\n30\tdef second():")}`),
    g,
  );
  assert.equal(score.ndcg_at_5, 1);
  assert.equal(score.ndcg_at_10, 1);
  const onlyOneRank = scoreResponse(atRank(1), g);
  assert.equal(onlyOneRank.ndcg_at_10, 1 / (1 + 1 / Math.log2(3)));
});

test("visible and structured hashes remain separate; score never reads hidden source", () => {
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
  const noSource = scoreResponse(
    response(item(1, "symbol: function wanted"), {
      structuredContent: { content: "def wanted():" },
    }),
    gold(),
  );
  assert.equal(noSource.hit_at_10, 0);
});

test("native non-code range is parsed as a non-source result", () => {
  const parsed = parseVisibleResponse(
    response(item(1, "heading: Notes", "docs/notes.pdf:page:2")),
  );
  assert.equal(parsed.items[0].path, "docs/notes.pdf");
  assert.deepEqual(parsed.items[0].range, { kind: "other", label: "page:2" });
});
