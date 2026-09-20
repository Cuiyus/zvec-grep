import assert from "node:assert/strict";
import test from "node:test";
import { loadSuite } from "../lib.mjs";
import { markdownReport, summarizePreviewPairs } from "../report.mjs";
import { scoreResponse } from "../scoring.mjs";
import { scoreSembleMetric } from "../semble-metrics.mjs";
import { fileRetrievalForRow } from "../file-retrieval-metrics.mjs";

const suite = await loadSuite();
const taskId = "sympy:38";
function observation(preview, repetition = 5) {
  const text = [
    "freshness: fresh",
    "#1 matchedBy=fts+vector sympy/matrices/matrices.py:674-676",
    "source:",
    "674\t    @deprecated",
    ...(preview === "full"
      ? [
          '675\t    def minorEntry(self, i, j, method="berkowitz"):',
          "676\t        return self.minor(i, j, method=method)",
        ]
      : []),
  ].join("\n");
  const score = scoreResponse(
    { content: [{ type: "text", text }] },
    suite.gold[taskId],
  );
  return {
    task_id: taskId,
    mode: "hybrid",
    preview,
    repetition,
    ...score,
    visible_output_bytes: Buffer.byteLength(text, "utf8"),
    semble_official: {
      targets: suite.semble_gold[taskId].targets,
      ...scoreSembleMetric(score.items, suite.semble_gold[taskId].targets),
    },
  };
}

test("preview text changes do not change the five quality metrics or paired native identities", () => {
  const short = observation("short"),
    full = observation("full");
  assert.notEqual(short.ranking_sha256, full.ranking_sha256);
  assert.deepEqual(short.semble_official, full.semble_official);
  assert.deepEqual(fileRetrievalForRow(short), fileRetrievalForRow(full));
  assert.equal(fileRetrievalForRow(short).hit_at_1, 1);
  const result = summarizePreviewPairs([short, full]);
  assert.equal(result.compared_pairs, 1);
  assert.equal(result.same_ranking_pairs, 1);
  assert.equal(result.same_official_ndcg_pairs, 1);
  assert.equal(result.quality.same_ranking_pairs, 1);
  assert.ok(!JSON.stringify(result).includes("anchor"));
  assert.ok(!JSON.stringify(result).includes("output_bytes"));
});

test("preview pairing separates query, mode and repetition and excludes invalid or undelivered responses", () => {
  const first = [observation("short", 1), observation("full", 1)];
  const quality = [observation("short"), observation("full")];
  const otherMode = [observation("short"), observation("full")].map((row) => ({
    ...row,
    mode: "fts",
  }));
  const otherTask = [observation("short"), observation("full")].map((row) => ({
    ...row,
    task_id: "another-query",
  }));
  otherMode[1].status = "harness_invalid";
  otherTask[1].execution_status = "product_error";
  const result = summarizePreviewPairs([
    ...first,
    ...quality,
    ...otherMode,
    ...otherTask,
    observation("short", 2),
  ]);
  assert.equal(result.observed_pairs, 5);
  assert.equal(result.compared_pairs, 2);
  assert.equal(result.same_ranking_pairs, 2);
  assert.equal(result.quality.observed_pairs, 3);
  assert.equal(result.quality.compared_pairs, 1);
  assert.equal(
    result.pairs.filter((pair) => pair.status === "unavailable").length,
    3,
  );
});

test("a changed ranked range is exposed even when file nDCG stays equal", () => {
  const short = observation("short"),
    full = observation("full");
  full.items[0].range.start_line--;
  const result = summarizePreviewPairs([short, full]);
  assert.equal(result.different_ranking_pairs, 1);
  assert.equal(result.same_official_ndcg_pairs, 1);
  assert.match(result.interpretation, /report-only/);
});

test("report has one concise seven-metric table, fixed averages and collapsed per-query details", () => {
  const mode = {
    file_retrieval: {
      planned_tasks: 20,
      scored_tasks: 20,
      hit_at_1_count: 7,
      hit_at_5_count: 12,
      hit_at_10_count: 15,
      mrr_at_10: 0.4055555556,
    },
    semble_official: {
      query_count: 20,
      repository_count: 11,
      language_count: 1,
      query_mean: { ndcg_at_10: 0.5 },
      repository_macro: { ndcg_at_10: 0.292475 },
      language_macro: { ndcg_at_10: 0.292475 },
    },
    measurements: {
      output_bytes_mean: 1536,
      output_sample_count: 20,
      latency_ms_p50: 12.34567,
      latency_sample_count: 100,
    },
  };
  const report = {
    scope: "full-20-original-queries",
    observed_calls: 200,
    integrity_passed: true,
    expected_task_ids: Array.from({ length: 20 }, (_, i) => `task:${i}`),
    primary_preview: "short",
    modes: { hybrid: mode },
    previews: {
      short: { modes: { hybrid: mode } },
      full: { modes: { hybrid: mode } },
    },
    tasks: [],
    repositories: Array.from({ length: 11 }, (_, i) => ({
      repository: `owner/repo${i}`,
    })),
    integrity_errors: [],
    paired_preview_comparison: summarizePreviewPairs([
      observation("short"),
      observation("full"),
    ]),
  };
  const text = markdownReport(report);
  for (const preview of ["short", "full"])
    assert.match(
      text,
      new RegExp(
        `\\| hybrid / ${preview} \\| 7/20 \\| 12/20 \\| 15/20 \\| 0\\.4056 \\| 0\\.2925 \\| 1\\.5000 \\| 12\\.3457 \\|`,
      ),
    );
  assert.match(text, /20 original questions \/ 11 repositories/);
  assert.match(text, /fifth call per question and preview/);
  assert.match(text, /weights repositories equally/);
  assert.match(text, /not the entire file/);
  assert.match(text, /<details>/);
  assert.match(text, /100\*\* successful valid calls/);
  assert.doesNotMatch(
    text,
    /Legacy|anchor|nDCG@5|By category|Preparation and latency|Mean output bytes/,
  );
  assert.equal((text.match(/\| Mode \/ preview \|/g) ?? []).length, 1);
});
