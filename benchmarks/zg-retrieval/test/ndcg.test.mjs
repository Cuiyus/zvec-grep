import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import test from "node:test";
import {
  NDCG_SOURCE,
  ndcgAtK,
  pathMatches,
  projectFileTargets,
  scoreNdcg,
  targetMatchesLocation,
  targetRank,
} from "../metrics/ndcg.mjs";

const item = (rank, path = "pkg/a.py", start = 10, end = 20) => ({
  rank,
  path,
  range: { kind: "text", start_line: start, end_line: end },
});
const approximate = (actual, expected, message) =>
  assert.ok(
    Math.abs(actual - expected) <= 1e-14,
    `${message}: ${actual} != ${expected}`,
  );

test("upstream path matching retains bidirectional slash-boundary suffix semantics", () => {
  for (const [left, right] of [
    ["pkg/a.py", "pkg/a.py"],
    ["/checkout/pkg/a.py", "pkg/a.py"],
    ["pkg/a.py", "/checkout/pkg/a.py"],
    ["C:\\checkout\\pkg\\a.py", "pkg/a.py"],
    ["pkg/a.py", "a.py"],
  ])
    assert.equal(pathMatches(left, right), true);
  for (const [left, right] of [
    ["other/a.py", "pkg/a.py"],
    ["notpkg/a.py", "pkg/a.py"],
    ["pkg/A.py", "pkg/a.py"],
    ["pkg/a.py.bak", "pkg/a.py"],
  ])
    assert.equal(pathMatches(left, right), false);
});

test("upstream location matching uses any inclusive overlap and only complete spans", () => {
  const result = item(1);
  for (const target of [
    { path: "pkg/a.py" },
    { path: "pkg/a.py", start_line: 1000 },
    { path: "pkg/a.py", end_line: 1 },
    { path: "pkg/a.py", start_line: 20, end_line: 25 },
    { path: "pkg/a.py", start_line: 1, end_line: 10 },
    { path: "pkg/a.py", start_line: 1, end_line: 200 },
  ])
    assert.equal(targetRank([result], target), 1);
  for (const target of [
    { path: "pkg/a.py", start_line: 21, end_line: 25 },
    { path: "pkg/a.py", start_line: 1, end_line: 9 },
    { path: "other/a.py" },
  ])
    assert.equal(targetRank([result], target), null);
});

test("duplicate file chunks consume native ranks but do not repeatedly reward one target", () => {
  const scored = scoreNdcg(
    [item(1), item(2), item(3, "pkg/b.py")],
    [{ path: "pkg/a.py" }, { path: "pkg/b.py" }],
  );
  assert.deepEqual(scored.target_ranks, [1, 3]);
  assert.deepEqual(scored.relevant_ranks, [1, 3]);
  assert.equal(scored.n_relevant, 2);
  assert.equal(
    scored.ndcg_at_10,
    (1 + 1 / Math.log2(4)) / (1 + 1 / Math.log2(3)),
  );
});

test("repeated targets keep the upstream IDCG denominator while one result rank stays binary", () => {
  const scored = scoreNdcg(
    [item(1)],
    [{ path: "pkg/a.py" }, { path: "pkg/a.py" }],
  );
  assert.deepEqual(scored.target_ranks, [1, 1]);
  assert.deepEqual(scored.relevant_ranks, [1, 1]);
  assert.equal(scored.n_relevant, 2);
  assert.equal(scored.ndcg_at_10, 1 / (1 + 1 / Math.log2(3)));
});

test("upstream first-target ranks are not optimally reassigned across overlapping targets", () => {
  const targets = [
    { path: "pkg/a.py", start_line: 10, end_line: 20 },
    { path: "pkg/a.py", start_line: 15, end_line: 30 },
  ];
  const scored = scoreNdcg([item(1), item(2, "pkg/a.py", 25, 30)], targets);
  assert.deepEqual(scored.target_ranks, [1, 1]);
  assert.equal(scored.ndcg_at_10, 1 / (1 + 1 / Math.log2(3)));
});

test("combined primary and secondary labels receive identical binary weight", () => {
  const primary = [{ path: "pkg/a.py" }];
  const secondary = [{ path: "pkg/b.py" }];
  const inputs = [item(1, "pkg/b.py"), item(2, "pkg/a.py")];
  const scored = scoreNdcg(inputs, [...primary, ...secondary]);
  assert.deepEqual(scored.target_ranks, [2, 1]);
  assert.equal(scored.ndcg_at_10, 1);
  assert.deepEqual(
    scoreNdcg(inputs, [...secondary, ...primary]).ndcg_at_10,
    scored.ndcg_at_10,
  );
});

test("location metrics ignore visible text, snippet truncation, outline and score", () => {
  const plain = [item(1)];
  const decorated = [
    {
      ...item(1),
      score: -100,
      source_lines: [{ line: 10, text: "unrelated visible text" }],
      outline: ["another function"],
      content: "",
    },
  ];
  assert.deepEqual(
    scoreNdcg(plain, [{ path: "pkg/a.py" }]),
    scoreNdcg(decorated, [{ path: "pkg/a.py" }]),
  );
  assert.deepEqual(
    scoreNdcg(plain, [{ path: "pkg/a.py", start_line: 19, end_line: 30 }]),
    scoreNdcg(decorated, [{ path: "pkg/a.py", start_line: 19, end_line: 30 }]),
  );
});

test("public non-text results can match file-only labels without invented source positions", () => {
  const input = [
    {
      rank: 1,
      path: "doc/manual.pdf",
      range: { kind: "other", label: "page:2" },
    },
  ];
  assert.equal(scoreNdcg(input, [{ path: "doc/manual.pdf" }]).ndcg_at_10, 1);
  assert.equal(
    scoreNdcg(input, [{ path: "doc/manual.pdf", start_line: 1, end_line: 20 }])
      .ndcg_at_10,
    0,
  );
});

test("upstream empty and cutoff cases keep denominators and ignore out-of-window ranks", () => {
  assert.deepEqual(scoreNdcg([], []), {
    ndcg_at_10: 0,
    target_ranks: [],
    n_relevant: 0,
    relevant_ranks: [],
  });
  assert.equal(scoreNdcg([], [{ path: "missing.py" }]).ndcg_at_10, 0);
  const input = Array.from({ length: 11 }, (_, i) =>
    item(i + 1, `pkg/${i + 1}.py`),
  );
  const scored = scoreNdcg(input, [
    { path: "pkg/6.py" },
    { path: "pkg/11.py" },
  ]);
  assert.deepEqual(scored.target_ranks, [6, 11]);
  assert.equal(Object.hasOwn(scored, "ndcg_at_5"), false);
  assert.equal(scored.ndcg_at_10, 1 / Math.log2(7) / (1 + 1 / Math.log2(3)));
  assert.equal(ndcgAtK([0, -1, 11], 2, 10), 0);
  assert.equal(ndcgAtK([1, 2], 2, 0), 0);
  assert.equal(ndcgAtK([1], 0, 10), 0);
});

test("SWE-QA projection deduplicates accepted paths without promoting bridge labels or anchor spans", () => {
  const labels = {
    targets: [
      { role: "bridge", path: "pkg/bridge.py" },
      {
        role: "accepted",
        path: "pkg/a.py",
        anchors: [{ start_line: 10, end_line: 11, text: "first anchor" }],
      },
      {
        role: "accepted",
        path: "pkg/a.py",
        anchors: [{ start_line: 100, end_line: 100, text: "another function" }],
      },
      { role: "accepted", path: "pkg/b.py" },
      { role: "bridge", path: "pkg/b.py" },
    ],
  };
  const before = structuredClone(labels);
  assert.deepEqual(projectFileTargets(labels), [
    { path: "pkg/a.py" },
    { path: "pkg/b.py" },
  ]);
  assert.deepEqual(labels, before);
  assert.deepEqual(
    projectFileTargets({ targets: [{ role: "bridge", path: "pkg/a.py" }] }),
    [],
  );
});

test("the adapter rejects malformed or compacted native ranks instead of silently renumbering", () => {
  assert.throws(() => scoreNdcg([item(2)], []), /consecutive native ranks/);
  assert.throws(
    () => scoreNdcg([item(1), item(3)], []),
    /consecutive native ranks/,
  );
  assert.throws(
    () => scoreNdcg([item(1), item(1)], []),
    /consecutive native ranks/,
  );
  assert.throws(
    () => scoreNdcg([item(1, "pkg/a.py", undefined, 10)], [{ path: 1 }]),
    /target path/,
  );
  assert.throws(
    () => scoreNdcg([item(1, "pkg/a.py", 20, 10)], []),
    /valid native source range/,
  );
});

test("vendored oracle is pinned to byte-identical upstream sources and includes its license", () => {
  const fixture = new URL("./fixtures/ndcg-reference/", import.meta.url);
  const manifest = JSON.parse(
    readFileSync(new URL("source.json", fixture), "utf8"),
  );
  assert.equal(manifest.commit, NDCG_SOURCE.commit);
  assert.equal(manifest.license, "MIT");
  const hashes = {
    "data.py":
      "4b6294e30b8dba1ae019b6be82daca9f44e4002c40a6552fe596994d42753d71",
    "metrics.py":
      "bd6cdfc820b159f11317104db7f2c7592d7c379c0bde608b242642bdbf69666e",
    LICENSE: "d1aa11c9413f6732a492cbe8b57f9fc5d9bc7a5f1575f420619d099b3358dc12",
  };
  assert.deepEqual(
    manifest.files.map((entry) => entry.path).sort(),
    Object.keys(hashes).sort(),
  );
  for (const entry of manifest.files) {
    assert.equal(entry.sha256, hashes[entry.path]);
    assert.equal(
      createHash("sha256")
        .update(readFileSync(new URL(entry.path, fixture)))
        .digest("hex"),
      entry.sha256,
    );
  }
});

test("JavaScript results agree with the original Python functions on boundary and overlap fixtures", () => {
  const pathInputs = [
    "pkg/a.py",
    "/repo/pkg/a.py",
    "C:\\repo\\pkg\\a.py",
    "other/a.py",
    "notpkg/a.py",
    "a.py",
    "pkg/A.py",
    "",
    "pkg/a.py.bak",
  ];
  const paths = pathInputs.flatMap((left) =>
    pathInputs.map((right) => [left, right]),
  );
  const spanInputs = [
    {},
    { start_line: null, end_line: null },
    { start_line: 100 },
    { end_line: 1 },
    { start_line: 1, end_line: 9 },
    { start_line: 1, end_line: 10 },
    { start_line: 20, end_line: 20 },
    { start_line: 20, end_line: 25 },
    { start_line: 21, end_line: 25 },
    { start_line: 1, end_line: 100 },
  ];
  const locations = paths.flatMap(([file, target]) =>
    spanInputs.map((span) => ({
      file_path: file,
      start_line: 10,
      end_line: 20,
      target: { path: target, ...span },
    })),
  );
  const scores = [{ items: [], targets: [] }];
  for (const targetPath of pathInputs) {
    for (const span of spanInputs) {
      scores.push({
        items: [
          item(1),
          item(2),
          item(3, "pkg/a.py", 25, 30),
          item(4, "other/a.py"),
        ],
        targets: [
          { path: targetPath, ...span },
          { path: "other/a.py" },
          { path: targetPath, ...span },
        ],
      });
    }
  }
  scores.push({
    items: Array.from({ length: 12 }, (_, i) => item(i + 1, `pkg/${i}.py`)),
    targets: Array.from({ length: 12 }, (_, i) => ({ path: `pkg/${i}.py` })),
  });
  const ndcg = [];
  for (const k of [0, 1, 5, 10, 12])
    for (const nRelevant of [0, 1, 2, 6, 15])
      for (const ranks of [
        [],
        [1],
        [1, 1],
        [1, 3],
        [5, 6, 10, 11],
        [0, -1, 12],
      ])
        ndcg.push({ ranks, n_relevant: nRelevant, k });
  const oracle = spawnSync(
    process.env.PYTHON || "python3",
    [
      fileURLToPath(
        new URL("./fixtures/ndcg-reference/oracle.py", import.meta.url),
      ),
    ],
    {
      input: JSON.stringify({ paths, locations, scores, ndcg }),
      encoding: "utf8",
      timeout: 30_000,
      maxBuffer: 2 * 1024 * 1024,
    },
  );
  assert.ifError(oracle.error);
  assert.equal(
    oracle.status,
    0,
    `upstream Python oracle failed: ${oracle.stderr}`,
  );
  const expected = JSON.parse(oracle.stdout);
  assert.deepEqual(
    paths.map(([left, right]) => pathMatches(left, right)),
    expected.path_matches,
  );
  assert.deepEqual(
    locations.map((input) =>
      targetMatchesLocation(
        input.file_path,
        input.start_line,
        input.end_line,
        input.target,
      ),
    ),
    expected.target_matches_location,
  );
  for (const [index, input] of scores.entries()) {
    const actual = scoreNdcg(input.items, input.targets);
    const reference = expected.scores[index];
    assert.deepEqual(
      actual.target_ranks,
      reference.target_ranks,
      `score case ${index}`,
    );
    assert.deepEqual(
      actual.relevant_ranks,
      reference.relevant_ranks,
      `score case ${index}`,
    );
    assert.equal(actual.n_relevant, reference.n_relevant);
    assert.equal(Object.hasOwn(actual, "ndcg_at_5"), false);
    approximate(
      actual.ndcg_at_10,
      reference.ndcg_at_10,
      `score case ${index} @10`,
    );
  }
  for (const [index, input] of ndcg.entries())
    approximate(
      ndcgAtK(input.ranks, input.n_relevant, input.k),
      expected.ndcg_at_k[index],
      `nDCG case ${index}`,
    );
});
