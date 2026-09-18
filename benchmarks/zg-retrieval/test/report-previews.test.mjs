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

test("full source can improve strict anchor visibility while preserving native identities and official file nDCG", () => {
  const short = observation("short"),
    full = observation("full");
  assert.equal(short.hit_at_10, 0);
  assert.equal(full.hit_at_10, 1);
  // The legacy repeatability identity includes displayed source locations;
  // it must not be used to compare different presentation arms.
  assert.notEqual(short.ranking_sha256, full.ranking_sha256);
  assert.deepEqual(short.semble_official, full.semble_official);
  assert.deepEqual(fileRetrievalForRow(short), fileRetrievalForRow(full));
  assert.equal(fileRetrievalForRow(short).hit_at_1, 1);
  const result = summarizePreviewPairs([short, full]);
  assert.equal(result.compared_pairs, 1);
  assert.equal(result.same_ranking_pairs, 1);
  assert.equal(result.same_official_ndcg_pairs, 1);
  assert.equal(result.quality.same_ranking_pairs, 1);
  assert.equal(result.pairs[0].primary_first_anchor_rank, "not_in_top10");
  assert.equal(result.pairs[0].comparison_first_anchor_rank, 1);
  assert.ok(
    result.pairs[0].comparison_output_bytes >
      result.pairs[0].primary_output_bytes,
  );
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
  const missingArm = observation("short", 2);
  const result = summarizePreviewPairs([
    ...first,
    ...quality,
    ...otherMode,
    ...otherTask,
    missingArm,
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

test("a changed ranked range is exposed even when file-target nDCG stays equal", () => {
  const short = observation("short"),
    full = observation("full");
  full.items[0].range.start_line--;
  const result = summarizePreviewPairs([short, full]);
  assert.equal(result.different_ranking_pairs, 1);
  assert.equal(result.same_official_ndcg_pairs, 1);
  assert.match(result.interpretation, /report-only/);
});

test("report tables retain both preview arms and label their different metric denominators", () => {
  const mode = {
    file_retrieval: {
      planned_tasks: 20,
      scored_tasks: 20,
      hit_at_1_count: 7,
      hit_at_5_count: 12,
      hit_at_10_count: 15,
      mrr_at_10: 0.4,
    },
    summary: {
      planned_tasks: 20,
      scored_tasks: 20,
      hit_at_1_count: 1,
      hit_at_5_count: 2,
      hit_at_10_count: 3,
      mrr_at_10: 0.1,
      ndcg_at_5: 0.2,
      ndcg_at_10: 0.3,
      ndcg_tasks: 12,
    },
    semble_official: {
      query_count: 20,
      repository_count: 11,
      language_count: 1,
      query_mean: { ndcg_at_5: 0.4, ndcg_at_10: 0.5 },
      repository_macro: { ndcg_at_5: 0.4, ndcg_at_10: 0.5 },
      language_macro: { ndcg_at_5: 0.4, ndcg_at_10: 0.5 },
    },
    by_category: {},
    output_size: { quality_mean_bytes: 1234 },
  };
  const full = structuredClone(mode);
  full.summary.hit_at_10_count = 9;
  full.output_size.quality_mean_bytes = 5678;
  const report = {
    scope: "full-20-original-queries",
    observed_calls: 200,
    integrity_passed: true,
    primary_preview: "short",
    modes: { hybrid: mode },
    previews: {
      short: { modes: { hybrid: mode } },
      full: { modes: { hybrid: full } },
    },
    tasks: [],
    repositories: [],
    integrity_errors: [],
    paired_preview_comparison: summarizePreviewPairs([
      observation("short"),
      observation("full"),
    ]),
  };
  const text = markdownReport(report);
  assert.match(
    text,
    /\| hybrid \/ short \| 0\.400 \| 0\.500 \| 7\/20 \| 12\/20 \| 15\/20/,
  );
  assert.match(
    text,
    /\| hybrid \/ full \| 0\.400 \| 0\.500 \| 7\/20 \| 12\/20 \| 15\/20/,
  );
  assert.match(text, /Legacy strict-anchor visibility diagnostics/);
  assert.match(text, /\| hybrid \/ full \| 20\/20 \| 1\/20 \| 2\/20 \| 9\/20/);
  assert.match(text, /12 of the full 20-question suite/);
  assert.match(text, /not the entire file/);
  assert.match(text, /not a model token estimate/);
  assert.match(text, /unbiased short\/full speed comparison/);
});
