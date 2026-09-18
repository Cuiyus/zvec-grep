import assert from "node:assert/strict";
import { mkdtemp, readFile, rm, stat, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { scoreSembleMetric } from "../semble-metrics.mjs";
import {
  FILE_RETRIEVAL_CONTRACT,
  fileRetrievalForRow,
} from "../file-retrieval-metrics.mjs";
import {
  compareReports,
  selectPreviewReport,
  markdownComparison,
  writeComparison,
} from "../compare.mjs";

const clone = (value) => structuredClone(value);
function row(task_id, rank = "not_in_top10", options = {}) {
  const found = Number.isInteger(rank);
  return {
    task_id,
    mode: "hybrid",
    repetition: 5,
    quality_observation: true,
    repository: "example/repo",
    language: "python",
    items: [],
    semble_official: {
      targets: [{ path: "target.py" }],
      ...scoreSembleMetric([], [{ path: "target.py" }]),
    },
    category: "what",
    gold_status: "reviewed",
    status: "scored",
    execution_status: "success",
    first_hit_rank: rank,
    hit_at_1: Number(found && rank <= 1),
    hit_at_5: Number(found && rank <= 5),
    hit_at_10: Number(found),
    rr_at_10: found ? 1 / rank : 0,
    ndcg_at_5: null,
    ndcg_at_10: null,
    ...options,
  };
}
function report(rows = [row("example:1", 1), row("example:2", 10)]) {
  const ids = [...new Set(rows.map((item) => item.task_id))];
  const productErrors = rows.filter(
    (item) => item.execution_status === "product_error",
  ).length;
  return {
    schema_version: 1,
    quality_repetition: 5,
    quality_score_valid: true,
    integrity_passed: productErrors === 0,
    integrity_errors: [],
    product_error_calls: productErrors,
    suite: {
      source: "a".repeat(64),
      gold: "b".repeat(64),
      semble_gold: "b".repeat(64),
      protocol: "c".repeat(64),
    },
    scope: ids.length === 20 ? "full-20-original-queries" : "explicit-subset",
    expected_task_ids: ids,
    modes: Object.fromEntries(
      [...new Set(rows.map((item) => item.mode))].map((mode) => [
        mode,
        { summary: { mrr_at_10: 999 } },
      ]),
    ),
    tasks: rows,
    repositories: [
      {
        candidate_commit: "d".repeat(40),
        package: { tarball_sha256: "e".repeat(64) },
        model_files_sha256: "f".repeat(64),
      },
    ],
  };
}

function pairedReport() {
  const result = report();
  result.schema_version = 2;
  result.primary_preview = "short";
  result.tasks = ["short", "full"].flatMap((preview) =>
    result.tasks.map((item) => ({ ...clone(item), preview })),
  );
  result.previews = Object.fromEntries(
    ["short", "full"].map((preview) => [
      preview,
      { modes: clone(result.modes) },
    ]),
  );
  return result;
}

function fileRow(taskId, rank) {
  const items = Array.from({ length: 10 }, (_, index) => ({
    rank: index + 1,
    path: index + 1 === rank ? "target.py" : "other.py",
    range: { kind: "text", start_line: 100, end_line: 120 },
    source_lines: [],
    outline: [],
  }));
  const targets = [{ path: "target.py" }];
  return row(taskId, "not_in_top10", {
    items,
    semble_official: { targets, ...scoreSembleMetric(items, targets) },
  });
}

test("file comparisons use shared public file relevance even when every strict anchor misses", () => {
  const baseline = report([
    fileRow("example:1", 5),
    fileRow("example:2", null),
  ]);
  const candidate = report([fileRow("example:1", 1), fileRow("example:2", 10)]);
  candidate.file_retrieval_contract = FILE_RETRIEVAL_CONTRACT;
  for (const value of candidate.tasks)
    value.file_retrieval = fileRetrievalForRow(value);
  // Cached aggregate summaries cannot control file scores.
  candidate.modes.hybrid.file_retrieval = { mrr_at_10: 999 };
  const result = compareReports(baseline, candidate);
  const metrics = result.modes.hybrid.file_retrieval;
  assert.equal(metrics.baseline.mrr_at_10, 0.1);
  assert.equal(metrics.candidate.mrr_at_10, 0.55);
  assert.equal(result.modes.hybrid.summary.delta.mrr_at_10, 0);
  assert.equal(result.tasks[1].candidate.file_retrieval.first_hit_rank, 10);
  assert.equal(result.tasks[1].file_retrieval_delta.rr_at_10, 0.1);
  assert.match(markdownComparison(result), /File MRR@10 \(query mean\)/);
  assert.match(markdownComparison(result), /Legacy strict-anchor/);
});

test("file metric contracts and cached per-question scores cannot be silently changed", () => {
  const baseline = report([fileRow("example:1", 5)]);
  baseline.file_retrieval_contract = FILE_RETRIEVAL_CONTRACT;
  baseline.tasks[0].file_retrieval = fileRetrievalForRow(baseline.tasks[0]);
  for (const mutate of [
    (value) => {
      value.file_retrieval_contract = "different-contract";
    },
    (value) => {
      delete value.file_retrieval_contract;
    },
    (value) => {
      delete value.tasks[0].file_retrieval;
    },
    (value) => {
      value.tasks[0].file_retrieval.rr_at_10 = 1;
    },
  ]) {
    const changed = clone(baseline);
    mutate(changed);
    assert.throws(() => compareReports(baseline, changed), /file retrieval/);
  }
});

test("version comparisons keep preview arms separate and recompute from public rows", () => {
  const baseline = pairedReport();
  const candidate = clone(baseline);
  candidate.tasks.find(
    (item) => item.preview === "full" && item.task_id === "example:2",
  ).first_hit_rank = 1;
  Object.assign(
    candidate.tasks.find(
      (item) => item.preview === "full" && item.task_id === "example:2",
    ),
    {
      hit_at_1: 1,
      hit_at_5: 1,
      hit_at_10: 1,
      rr_at_10: 1,
    },
  );
  const result = compareReports(baseline, candidate);
  assert.equal(result.previews.short.modes.hybrid.summary.delta.mrr_at_10, 0);
  assert.ok(
    Math.abs(result.previews.full.modes.hybrid.summary.delta.mrr_at_10 - 0.45) <
      1e-12,
  );
  assert.equal(result.previews.short.tasks.length, 2);
  assert.equal(result.previews.full.tasks.length, 2);
  assert.match(markdownComparison(result), /short source/);
  assert.match(markdownComparison(result), /full source/);
  assert.equal(selectPreviewReport(candidate, "full").tasks.length, 2);
});

test("version comparisons reject incomplete, duplicated or unlabeled preview matrices", () => {
  for (const mutate of [
    (value) => {
      value.previews.short.modes.fts = {};
    },
    (value) => {
      delete value.previews.full;
    },
    (value) => {
      delete value.tasks[0].preview;
    },
    (value) => {
      value.tasks.pop();
    },
    (value) => {
      value.tasks.push(clone(value.tasks[0]));
    },
  ]) {
    const baseline = pairedReport();
    const changed = clone(baseline);
    mutate(changed);
    assert.throws(() => compareReports(baseline, changed));
  }
  assert.throws(
    () => compareReports(pairedReport(), report()),
    /preview arm sets/,
  );
  assert.throws(
    () =>
      compareReports(
        selectPreviewReport(pairedReport(), "short"),
        selectPreviewReport(pairedReport(), "full"),
      ),
    /incompatible preview arms/,
  );
});

test("comparison pairs quality rows, retains offsetting flips and recomputes summaries", () => {
  const baseline = report([
    row("example:1", 1, { ndcg_at_5: 0.7, ndcg_at_10: 0.8 }),
    row("example:2"),
  ]);
  const candidate = report([
    row("example:1", 10, { ndcg_at_5: 0, ndcg_at_10: 0.3 }),
    row("example:2", 1),
  ]);
  const result = compareReports(baseline, candidate);
  const summary = result.modes.hybrid.summary;
  assert.equal(summary.baseline.mrr_at_10, 0.5);
  assert.equal(summary.candidate.mrr_at_10, 0.55);
  assert.ok(Math.abs(summary.delta.mrr_at_10 - 0.05) < 1e-12);
  assert.equal(summary.delta.hit_at_1_count, 0);
  assert.deepEqual(summary.hit_flips.hit_at_1, {
    improvements: 1,
    regressions: 1,
  });
  assert.equal(summary.delta.hit_at_10_count, 1);
  assert.equal(summary.baseline.ndcg_tasks, 1);
  assert.equal(summary.delta.ndcg_at_10, -0.5);
  assert.equal(result.tasks[0].rank_change, "regressed");
  assert.equal(result.tasks[1].rank_change, "improved");
  assert.equal(result.tasks[0].delta.rr_at_10, -0.9);
  assert.equal(result.modes.hybrid.by_category.what.baseline.planned_tasks, 2);
  assert.equal(
    result.modes.hybrid.by_repository["example/repo"].candidate.scored_tasks,
    2,
  );
  assert.match(markdownComparison(result), /report-only comparison/);
});

test("ordering may differ while identical task and mode sets preserve baseline table order", () => {
  const baseline = report([
    row("example:1", 1),
    row("example:2", 5),
    row("example:1", 3, { mode: "fts" }),
    row("example:2", 9, { mode: "fts" }),
  ]);
  const candidate = clone(baseline);
  candidate.expected_task_ids.reverse();
  candidate.tasks.reverse();
  candidate.modes = { fts: {}, hybrid: {} };
  const result = compareReports(baseline, candidate);
  assert.deepEqual(result.expected_task_ids, baseline.expected_task_ids);
  assert.deepEqual(
    result.tasks.map((item) => `${item.task_id}/${item.mode}`),
    ["example:1/hybrid", "example:2/hybrid", "example:1/fts", "example:2/fts"],
  );
  assert.equal(result.modes.fts.summary.delta.mrr_at_10, 0);
});

test("valid full-20 reports and identical explicit subsets are supported", () => {
  const full = report(
    Array.from({ length: 20 }, (_, i) => row(`example:${i}`, 1)),
  );
  assert.equal(
    compareReports(full, clone(full)).scope,
    "full-20-original-queries",
  );
  const subset = report([row("example:1")]);
  assert.equal(
    compareReports(subset, clone(subset)).expected_task_ids.length,
    1,
  );
  assert.throws(
    () => compareReports(full, subset),
    /incompatible report scopes/,
  );
});

for (const [name, mutate, pattern] of [
  [
    "source identity",
    (r) => {
      r.suite.source = "1".repeat(64);
    },
    /incompatible suite source/,
  ],
  [
    "Gold identity",
    (r) => {
      r.suite.gold = "2".repeat(64);
    },
    /incompatible suite gold/,
  ],
  [
    "protocol identity",
    (r) => {
      r.suite.protocol = "3".repeat(64);
    },
    /incompatible suite protocol/,
  ],
  [
    "missing task",
    (r) => {
      r.tasks.pop();
    },
    /missing task\/mode coverage/,
  ],
  [
    "duplicate task",
    (r) => {
      r.tasks[1] = clone(r.tasks[0]);
    },
    /duplicate task\/mode/,
  ],
  [
    "duplicate expected task",
    (r) => {
      r.expected_task_ids[1] = r.expected_task_ids[0];
    },
    /duplicate value/,
  ],
  [
    "quality flag",
    (r) => {
      r.quality_score_valid = false;
    },
    /invalid quality report/,
  ],
  [
    "hidden invalidity",
    (r) => {
      r.integrity_errors.push("missing raw file");
    },
    /experimental integrity errors/,
  ],
  [
    "mode coverage",
    (r) => {
      r.modes.vector = {};
    },
    /missing task\/mode coverage/,
  ],
  [
    "unexpected task",
    (r) => {
      r.tasks[1].task_id = "other:1";
    },
    /unexpected task\/mode/,
  ],
  [
    "non-quality repeat",
    (r) => {
      r.tasks[1].repetition = 2;
    },
    /quality repetition 5/,
  ],
  [
    "hit-score corruption",
    (r) => {
      r.tasks[1].hit_at_1 = 1;
    },
    /hit\/rank mismatch/,
  ],
  [
    "RR corruption",
    (r) => {
      r.tasks[1].rr_at_10 = 0;
    },
    /RR\/rank mismatch/,
  ],
  [
    "status-score conflict",
    (r) => {
      r.tasks[0].status = "product_error";
    },
    /inconsistent scored status/,
  ],
  [
    "unexplained integrity failure",
    (r) => {
      r.integrity_passed = false;
    },
    /integrity flag and product errors disagree/,
  ],
]) {
  test(`comparison rejects ${name}`, () => {
    const baseline = report();
    const candidate = clone(baseline);
    mutate(candidate);
    assert.throws(() => compareReports(baseline, candidate), pattern);
  });
}

test("same-size but different task/mode sets cannot be compared", () => {
  const before = report([row("example:1")]);
  assert.throws(
    () => compareReports(before, report([row("example:2")])),
    /incompatible expected task sets/,
  );
  const after = report([
    row("example:1"),
    row("example:1", 1, { mode: "vector" }),
  ]);
  assert.throws(() => compareReports(before, after), /incompatible mode sets/);
});

test("nDCG eligibility must match per task, not just by total denominator", () => {
  const baseline = report([
    row("example:1", 1, { ndcg_at_5: 1, ndcg_at_10: 1 }),
    row("example:2", 1),
  ]);
  const candidate = report([
    row("example:1", 1),
    row("example:2", 1, { ndcg_at_5: 1, ndcg_at_10: 1 }),
  ]);
  assert.throws(
    () => compareReports(baseline, candidate),
    /changed scoring eligibility/,
  );
});

test("product-error zeros retain denominators and expose recovery/failure transitions", () => {
  const failed = row("example:1", "not_in_top10", {
    status: "product_error",
    execution_status: "product_error",
    ndcg_at_5: 0,
    ndcg_at_10: 0,
  });
  const healthy = row("example:1", 1, { ndcg_at_5: 1, ndcg_at_10: 1 });
  const recovery = compareReports(report([failed]), report([healthy]));
  assert.equal(recovery.modes.hybrid.summary.baseline.scored_tasks, 1);
  assert.equal(recovery.modes.hybrid.summary.delta.mrr_at_10, 1);
  assert.equal(recovery.tasks[0].status_transition, "product_error -> scored");
  assert.equal(
    recovery.tasks[0].execution_transition,
    "product_error -> success",
  );
  assert.match(recovery.warnings[0], /baseline: operational integrity failed/);
  const failure = compareReports(report([healthy]), report([failed]));
  assert.equal(failure.tasks[0].rank_change, "regressed");
  assert.equal(failure.modes.hybrid.summary.delta.product_error_tasks, 1);
  assert.match(markdownComparison(failure), /Operational integrity warnings/);
});

test("matched unreviewed Gold remains N/A and never becomes a zero", () => {
  const unreviewed = row("example:2", null, {
    status: "gold_unknown",
    gold_status: "unknown",
    hit_at_1: null,
    hit_at_5: null,
    hit_at_10: null,
    rr_at_10: null,
  });
  const input = report([row("example:1", 1), unreviewed]);
  const result = compareReports(input, clone(input));
  assert.equal(result.modes.hybrid.summary.baseline.planned_tasks, 2);
  assert.equal(result.modes.hybrid.summary.baseline.scored_tasks, 1);
  assert.equal(result.tasks[1].delta.rr_at_10, null);
  assert.equal(result.tasks[1].rank_change, "unscored");
});

test("writes JSON and Markdown with input hashes only after validation and refuses overwrite", async (t) => {
  const temp = await mkdtemp(join(tmpdir(), "zg-compare-"));
  t.after(() => rm(temp, { recursive: true, force: true }));
  const baselinePath = join(temp, "baseline.json");
  const candidatePath = join(temp, "candidate.json");
  const baseline = report();
  const candidate = report([row("example:1", 5), row("example:2", 1)]);
  await writeFile(baselinePath, JSON.stringify(baseline));
  await writeFile(candidatePath, JSON.stringify(candidate));
  const output = join(temp, "comparison");
  const result = await writeComparison(baselinePath, candidatePath, output);
  assert.match(result.inputs.baseline.sha256, /^[a-f0-9]{64}$/);
  assert.equal(
    JSON.parse(await readFile(join(output, "comparison.json"), "utf8")).tasks
      .length,
    2,
  );
  assert.match(
    await readFile(join(output, "comparison.md"), "utf8"),
    /example:1/,
  );
  await assert.rejects(writeComparison(baselinePath, candidatePath, output), {
    code: "EEXIST",
  });
  candidate.quality_score_valid = false;
  await writeFile(candidatePath, JSON.stringify(candidate));
  const invalidOutput = join(temp, "invalid");
  await assert.rejects(
    writeComparison(baselinePath, candidatePath, invalidOutput),
    /invalid quality report/,
  );
  await assert.rejects(stat(invalidOutput), { code: "ENOENT" });
});

test("official comparison re-scores native first target ranks and ignores cached macro totals", () => {
  const targets = [{ path: "a.py" }, { path: "b.py" }];
  const oldRow = row("example:1", "not_in_top10", {
    semble_official: { targets, ...scoreSembleMetric([], targets) },
  });
  const items = [
    { rank: 1, path: "a.py" },
    { rank: 2, path: "a.py" },
    { rank: 3, path: "b.py" },
  ];
  const newRow = row("example:1", "not_in_top10", {
    items,
    semble_official: { targets, ...scoreSembleMetric(items, targets) },
  });
  const baseline = report([oldRow]),
    candidate = report([newRow]);
  candidate.modes.hybrid.semble_official = {
    repository_macro: { ndcg_at_10: 999 },
  };
  const result = compareReports(baseline, candidate);
  assert.deepEqual(
    result.tasks[0].candidate.semble_official.target_ranks,
    [1, 3],
  );
  const expected = 1.5 / (1 + 1 / Math.log2(3));
  assert.equal(
    result.modes.hybrid.semble_official.candidate.repository_macro.ndcg_at_10,
    expected,
  );
  assert.equal(result.tasks[0].semble_official_delta.ndcg_at_10, expected);
  assert.equal(result.modes.hybrid.summary.delta.mrr_at_10, 0);
  assert.match(markdownComparison(result), /SWE-QA accepted-file projection/);
});

test("official comparison rejects score corruption, changed projection and old quality-repetition metadata", () => {
  const baseline = report();
  for (const mutate of [
    (r) => {
      r.tasks[0].semble_official.ndcg_at_10 = 1;
    },
    (r) => {
      r.suite.semble_gold = "9".repeat(64);
    },
    (r) => {
      r.tasks[0].semble_official.targets = [{ path: "changed.py" }];
    },
    (r) => {
      r.quality_repetition = 1;
    },
  ]) {
    const candidate = clone(baseline);
    mutate(candidate);
    assert.throws(
      () => compareReports(baseline, candidate),
      /Semble official score|incompatible suite semble_gold|incompatible Semble target projection|quality repetition 5/,
    );
  }
});
