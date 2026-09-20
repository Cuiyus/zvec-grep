import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import test from "node:test";
import { parseVisibleResponse, scoreResponse } from "../scoring.mjs";
import {
  createSembleResponseParser,
  scoreSembleResponse,
} from "../semble-scoring.mjs";
import { scoreFileRetrieval } from "../file-retrieval-metrics.mjs";

const query = "How does wanted keep its result?";
const sha = (text) => createHash("sha256").update(text).digest("hex");
const response = (payload, extra = {}) => ({
  content: [{ type: "text", text: JSON.stringify(payload) }],
  ...extra,
});
const entry = (overrides = {}) => {
  const content = overrides.content ?? "def wanted():\n    return state";
  const start = overrides.start_line ?? 20;
  const count =
    typeof content === "string"
      ? content.split("\n").length - Number(content.endsWith("\n"))
      : 1;
  return {
    file_path: "pkg/main.py",
    start_line: start,
    end_line: start + count - 1,
    score: 0.9,
    content,
    ...overrides,
  };
};
const result = (entries = [entry()], extra = {}) =>
  response({ query, results: entries }, extra);
const anchor = (text = "def wanted():", start = 20) => ({
  start_line: start,
  end_line: start + text.split("\n").length - 1,
  text,
  sha256: sha(text),
});
const target = (id = "wanted", overrides = {}) => ({
  id,
  path: "pkg/main.py",
  kind: "symbol",
  symbol: "wanted",
  source_sha256: "a".repeat(64),
  anchors: [anchor()],
  role: "accepted",
  ...overrides,
});
const gold = (targets = [target()], groups = []) => ({
  schema_version: 1,
  status: "reviewed",
  targets,
  ndcg: { enabled: groups.length > 0, groups },
});
const score = (raw, labels = gold()) =>
  scoreSembleResponse(raw, labels, { expectedQuery: query });
const parse = createSembleResponseParser({ expectedQuery: query });
const filler = () => entry({ start_line: 2, content: "def other():" });

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
const assertNoLegacyScores = (parsed) => {
  for (const key of removedFields)
    assert.equal(Object.hasOwn(parsed, key), false, key);
};

for (const rank of [1, 5, 10]) {
  test(`Semble preserves native array rank ${rank} without file compaction`, () => {
    const raw = result([...Array.from({ length: rank - 1 }, filler), entry()]);
    const scored = score(raw);
    assert.equal(scored.status, "scored");
    assertNoLegacyScores(scored);
    assert.equal(scored.items.length, rank);
    assert.deepEqual(
      scored.items.map((item) => item.rank),
      Array.from({ length: rank }, (_, index) => index + 1),
    );
  });
}

test("Semble uses visible content at consecutive source lines without trimming or outlines", () => {
  const content = "    def wanted():\n\treturn state\n";
  const parsed = parse(result([entry({ content })]));
  assert.deepEqual(parsed.items[0].source_lines, [
    { line: 20, text: "    def wanted():" },
    { line: 21, text: "\treturn state" },
  ]);
  assert.deepEqual(parsed.items[0].outline, []);
  assert.equal(parsed.items[0].range.end_line, 21);
  assert.equal(parsed.items[0].source_content, content);
  assert.equal(parsed.freshness, null);
});

test("Semble full chunks retain evidence beyond line ten and terminal blank lines", () => {
  const prefix = Array.from({ length: 12 }, (_, index) => `# context ${index}`);
  const content = `${prefix.join("\n")}\ndef wanted():\n\n`;
  const raw = result([entry({ content })]);
  const labels = gold([
    target("wanted", { anchors: [anchor("def wanted():", 32)] }),
  ]);
  const scored = score(raw, labels);
  assert.equal(scored.status, "scored");
  assertNoLegacyScores(scored);
  assert.equal(scored.items[0].source_lines.length, 14);
  assert.deepEqual(scored.items[0].source_lines.at(-1), { line: 33, text: "" });
  assert.equal(scored.items[0].source_content, content);
});

test("Semble full content keeps non-LF separators on their physical source line", () => {
  const content = "first\fsecond\u2028third\nnext\n";
  const parsed = parse(result([entry({ content })]));
  assert.deepEqual(parsed.items[0].source_lines, [
    { line: 20, text: "first\fsecond\u2028third" },
    { line: 21, text: "next" },
  ]);
});

test("Semble never expands the chunk range or hidden structured content", () => {
  const raw = result([entry({ content: "def other():" })], {
    structuredContent: {
      results: [entry()],
      outline: "def wanted():",
    },
  });
  assert.equal(score(raw).items[0].source_content, "def other():");
  assert.deepEqual(score(raw).items[0].outline, []);
  assertNoLegacyScores(score(raw));
  const outsideSnippet = gold([
    target("body", {
      kind: "code_span",
      anchors: [anchor("    return state", 35)],
    }),
  ]);
  assert.equal(score(result(), outsideSnippet).items[0].range.end_line, 21);
  assert.equal(score(result(), outsideSnippet).items[0].source_lines.length, 2);
  assert.equal(
    score(result([entry({ content: "" })])).status,
    "harness_invalid",
  );
});

test("Semble explicit no-results object is a valid miss; other errors are not empty", () => {
  const scored = score(response({ error: "No results found." }));
  assert.equal(scored.status, "scored");
  assert.deepEqual(scored.items, []);
  assertNoLegacyScores(scored);
  assert.equal(scored.empty_reason, "no_results");
  for (const payload of [
    { error: "No results found.", results: [] },
    { error: "Index unavailable." },
    { query, results: [] },
    null,
    [],
  ])
    assert.equal(score(response(payload)).status, "harness_invalid");
});

test("Semble indexing plaintext and MCP isError retain product-failure eligibility", () => {
  const failures = [
    {
      content: [
        { type: "text", text: "Failed to index '/tmp/repo': model failed" },
      ],
    },
    {
      isError: true,
      content: [{ type: "text", text: "Tool exception" }],
    },
  ];
  for (const raw of failures) {
    const scored = score(raw);
    assert.equal(scored.status, "product_error");
    assert.equal(scored.execution_status, "product_error");
    assert.deepEqual(scored.items, []);
    assertNoLegacyScores(scored);
    assert.equal(scored.invalid_reason, null);
  }
  assert.equal(
    score({ content: [{ type: "text", text: "Unexpected indexing output" }] })
      .status,
    "harness_invalid",
  );
});

test("Semble parser requires the exact unmodified query and a single public text block", () => {
  assert.throws(() => createSembleResponseParser(), /exact original query/);
  assert.throws(
    () => createSembleResponseParser({ expectedQuery: "" }),
    /exact original query/,
  );
  const malformed = [
    response({ query: query.toLowerCase(), results: [entry()] }),
    response({ query: `${query}\n`, results: [entry()] }),
    response({ query, results: [entry()], repos: { main: "/tmp/repo" } }),
    { content: [] },
    { content: [{ type: "image", text: "{}" }] },
    { content: [...result().content, { type: "text", text: "extra" }] },
    { structuredContent: { query, results: [entry()] } },
  ];
  for (const raw of malformed)
    assert.equal(score(raw).status, "harness_invalid");
});

test("Semble rejects unsupported ranks, paths, fields, ranges, scores and snippet framing", () => {
  const malformed = [
    { rank: 2 },
    { file_path: "/tmp/pkg/main.py" },
    { file_path: "C:/pkg/main.py" },
    { file_path: "pkg\\main.py" },
    { file_path: "../pkg/main.py" },
    { file_path: "./pkg/main.py" },
    { file_path: "pkg//main.py" },
    { file_path: "pkg/\nmain.py" },
    { start_line: 0 },
    { start_line: 20.5 },
    { start_line: Number.MAX_SAFE_INTEGER + 1 },
    { end_line: 19 },
    { end_line: 20, content: "first\nsecond" },
    { end_line: 40, content: "first\nsecond" },
    { score: "0.5" },
    { score: null },
    { content: undefined },
    { content: ["def wanted():"] },
    { content: "first\r\nsecond" },
  ];
  for (const overrides of malformed) {
    const scored = score(result([entry(overrides)]));
    assert.equal(scored.status, "harness_invalid", JSON.stringify(overrides));
    assertNoLegacyScores(scored);
  }
  assert.equal(
    score(result(Array.from({ length: 11 }, () => entry()))).status,
    "harness_invalid",
  );
});

test("Semble visible output and rank hashes do not depend on hidden metadata", () => {
  const raw = result();
  const plain = score(raw);
  const extra = score({ ...raw, structuredContent: { private: "different" } });
  assert.equal(plain.visible_output_sha256, extra.visible_output_sha256);
  assert.equal(plain.ranking_sha256, extra.ranking_sha256);
  assert.notEqual(
    plain.structured_output_sha256,
    extra.structured_output_sha256,
  );
  assert.notEqual(
    plain.ranking_sha256,
    score(result([entry({ start_line: 21 })])).ranking_sha256,
  );
});

test("the shared scorer keeps its default zg behavior unchanged", () => {
  const raw = {
    content: [
      {
        type: "text",
        text: "freshness: fresh\n#1 matchedBy=fts+vector pkg/main.py:20-40\nsource:\n20\tdef wanted():",
      },
    ],
  };
  assert.deepEqual(
    scoreResponse(raw, gold()),
    scoreResponse(raw, gold(), { parseResponse: parseVisibleResponse }),
  );
  assert.equal(scoreResponse(raw, gold()).status, "scored");
  assertNoLegacyScores(scoreResponse(raw, gold()));
});

test("Semble file relevance ignores anchor text and uses only frozen accepted paths", () => {
  const targets = [{ path: "pkg/main.py" }];
  for (const content of [
    "def wanted():",
    "a useful function body",
    "an unrelated declaration",
  ]) {
    const parsed = score(result([entry({ start_line: 200, content })]));
    assert.equal(parsed.status, "scored");
    assert.equal(scoreFileRetrieval(parsed.items, targets).hit_at_1, 1);
    assertNoLegacyScores(parsed);
  }
  const other = score(result([entry({ file_path: "pkg/else.py" })]));
  assert.equal(scoreFileRetrieval(other.items, targets).hit_at_10, 0);
});
