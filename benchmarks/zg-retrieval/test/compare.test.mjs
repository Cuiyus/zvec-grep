import assert from "node:assert/strict";
import { mkdtemp, readFile, rm, stat, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { scoreSembleMetric } from "../metrics/ndcg.mjs";
import {
  FILE_RETRIEVAL_CONTRACT,
  fileRetrievalForRow,
} from "../metrics/files.mjs";
import {
  compareReports,
  selectPreviewReport,
  markdownComparison,
  writeComparison,
} from "../reports/compare.mjs";

import { validateZgReport } from "../reports/validation.mjs";

const clone = (value) => structuredClone(value);
function row(task_id, rank = null, options = {}) {
  const targets = options.targets ?? [{ path: "target.py" }];
  const items =
    options.items ??
    Array.from({ length: 10 }, (_, index) => ({
      rank: index + 1,
      path: index + 1 === rank ? "target.py" : "other.py",
    }));
  const value = {
    task_id,
    mode: "hybrid",
    preview: "short",
    repetition: 5,
    quality_observation: true,
    repository: "example/repo",
    language: "python",
    category: "what",
    gold_status: "reviewed",
    status: "scored",
    execution_status: "success",
    latency_ms: 50,
    visible_output_bytes: 1024,
    ...options,
    items,
    semble_official: { targets, ...scoreSembleMetric(items, targets) },
  };
  delete value.targets;
  value.file_retrieval = fileRetrievalForRow(value);
  value.measurement_observations = Array.from({ length: 5 }, (_, index) => ({
    repetition: index + 1,
    status: value.status,
    execution_status: value.execution_status,
    latency_ms: value.latency_ms,
    visible_output_bytes: value.visible_output_bytes,
  }));
  return value;
}
function report(rows = [row("example:1", 1), row("example:2", 10)]) {
  const ids = [...new Set(rows.map((item) => item.task_id))];
  const productErrors =
    rows.filter((item) => item.execution_status === "product_error").length * 5;
  return {
    schema_version: 3,
    file_retrieval_contract: FILE_RETRIEVAL_CONTRACT,
    preview: "short",
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
      [...new Set(rows.map((item) => item.mode))].map((mode) => [mode, {}]),
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
  const value = report();
  delete value.preview;
  value.primary_preview = "short";
  value.tasks = ["short", "full"].flatMap((preview) =>
    value.tasks.map((item) => ({ ...clone(item), preview })),
  );
  value.previews = Object.fromEntries(
    ["short", "full"].map((preview) => [
      preview,
      { modes: clone(value.modes) },
    ]),
  );
  return value;
}

test("comparison recomputes only the five quality metrics and both operational measurements", () => {
  const baseline = report([row("example:1", 5), row("example:2")]);
  const candidate = report([row("example:1", 1), row("example:2", 10)]);
  candidate.modes.hybrid.file_retrieval = { mrr_at_10: 999 };
  candidate.modes.hybrid.measurements = { latency_ms_p50: 999 };
  const result = compareReports(baseline, candidate);
  const metrics = result.modes.hybrid.file_retrieval;
  assert.equal(metrics.baseline.mrr_at_10, 0.1);
  assert.equal(metrics.candidate.mrr_at_10, 0.55);
  assert.equal(result.tasks[1].candidate.file_retrieval.first_hit_rank, 10);
  assert.equal(result.tasks[1].file_retrieval_delta.rr_at_10, 0.1);
  assert.deepEqual(result.modes.hybrid.measurements.candidate, {
    latency_ms_p50: 50,
    latency_sample_count: 10,
    output_bytes_mean: 1024,
    output_sample_count: 2,
  });
  assert.deepEqual(Object.keys(result.modes.hybrid).sort(), [
    "file_retrieval",
    "measurements",
    "semble_official",
  ]);
  const markdown = markdownComparison(result);
  assert.match(markdown, /0\.5500/);
  assert.match(markdown, /1\.0000 \| 50\.0000/);
  assert.doesNotMatch(markdown, /anchor|nDCG@5|By category/);
  assert.doesNotMatch(
    JSON.stringify(result),
    /ndcg_at_5|target_matches|by_category/,
  );
});

test("legacy schema 1/2 reports are rederived from public items without carrying removed metrics forward", () => {
  const old = report([row("example:1", 5)]);
  old.schema_version = 2;
  delete old.file_retrieval_contract;
  for (const value of old.tasks) {
    delete value.file_retrieval;
    delete value.measurement_observations;
    Object.assign(value, {
      first_hit_rank: 1,
      hit_at_1: 1,
      hit_at_5: 1,
      hit_at_10: 1,
      rr_at_10: 1,
      ndcg_at_5: 1,
      ndcg_at_10: 1,
      target_matches: [],
    });
    value.semble_official.ndcg_at_5 = 1 / Math.log2(6);
  }
  const current = report([row("example:1", 1)]);
  const result = compareReports(old, current);
  assert.equal(result.modes.hybrid.file_retrieval.baseline.mrr_at_10, 0.2);
  assert.equal(result.modes.hybrid.file_retrieval.candidate.mrr_at_10, 1);
  assert.equal(result.modes.hybrid.measurements.baseline.latency_ms_p50, null);
  assert.doesNotMatch(JSON.stringify(result), /ndcg_at_5|target_matches/);
  assert.ok(!Object.hasOwn(result.tasks[0].baseline, "rr_at_10"));
  const schema1 = clone(old);
  schema1.schema_version = 1;
  delete schema1.preview;
  assert.equal(
    compareReports(schema1, clone(schema1)).modes.hybrid.file_retrieval.baseline
      .mrr_at_10,
    0.2,
  );
});

test("file metric contracts and cached per-question scores cannot be silently changed", () => {
  const baseline = report([row("example:1", 5)]);
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

test("new reports reject removed quality fields and corrupted official scores", () => {
  for (const mutate of [
    (value) => {
      value.tasks[0].rr_at_10 = 1;
    },
    (value) => {
      value.tasks[0].target_matches = [];
    },
    (value) => {
      value.tasks[0].semble_official.ndcg_at_5 = 1;
    },
    (value) => {
      value.tasks[0].semble_official.ndcg_at_10 = 0;
    },
  ]) {
    const before = report(),
      after = clone(before);
    mutate(after);
    assert.throws(
      () => compareReports(before, after),
      /obsolete report field|Semble official score/,
    );
  }
});

test("version comparisons keep preview arms separate and render one combined table", () => {
  const baseline = pairedReport(),
    candidate = clone(baseline);
  candidate.tasks = candidate.tasks.map((item) =>
    item.preview === "full" && item.task_id === "example:2"
      ? row("example:2", 1, { preview: "full" })
      : item,
  );
  const result = compareReports(baseline, candidate);
  assert.equal(
    result.previews.short.modes.hybrid.file_retrieval.delta.mrr_at_10,
    0,
  );
  assert.ok(
    Math.abs(
      result.previews.full.modes.hybrid.file_retrieval.delta.mrr_at_10 - 0.45,
    ) < 1e-12,
  );
  assert.equal(result.previews.short.tasks.length, 2);
  assert.equal(result.previews.full.tasks.length, 2);
  const markdown = markdownComparison(result);
  assert.match(markdown, /hybrid \/ short \/ baseline/);
  assert.match(markdown, /hybrid \/ full \/ candidate/);
  assert.equal(
    (markdown.match(/\| Mode \/ preview \/ version \|/g) ?? []).length,
    1,
  );
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
    const before = pairedReport(),
      after = clone(before);
    mutate(after);
    assert.throws(() => compareReports(before, after));
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

test("offsetting hit changes remain visible per question", () => {
  const baseline = report([row("example:1", 1), row("example:2")]);
  const candidate = report([row("example:1", 10), row("example:2", 1)]);
  const result = compareReports(baseline, candidate),
    summary = result.modes.hybrid.file_retrieval;
  assert.equal(summary.baseline.mrr_at_10, 0.5);
  assert.equal(summary.candidate.mrr_at_10, 0.55);
  assert.ok(Math.abs(summary.delta.mrr_at_10 - 0.05) < 1e-12);
  assert.equal(summary.delta.hit_at_1, 0);
  assert.equal(result.tasks[0].file_retrieval_delta.hit_at_1, -1);
  assert.equal(result.tasks[1].file_retrieval_delta.hit_at_1, 1);
  assert.equal(result.tasks[0].rank_change, "regressed");
  assert.equal(result.tasks[1].rank_change, "improved");
  assert.equal(result.tasks[0].file_retrieval_delta.rr_at_10, -0.9);
});

test("identical task and mode sets preserve baseline table order", () => {
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
  assert.equal(result.modes.fts.file_retrieval.delta.mrr_at_10, 0);
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
    "file-Gold identity",
    (r) => {
      r.suite.semble_gold = "2".repeat(64);
    },
    /incompatible suite semble_gold/,
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
    "RR corruption",
    (r) => {
      r.tasks[1].file_retrieval.rr_at_10 = 0;
    },
    /file retrieval score/,
  ],
  [
    "hit corruption",
    (r) => {
      r.tasks[1].file_retrieval.hit_at_1 = 1;
    },
    /file retrieval score/,
  ],
  [
    "status conflict",
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
])
  test(`comparison rejects ${name}`, () => {
    const baseline = report(),
      candidate = clone(baseline);
    mutate(candidate);
    assert.throws(() => compareReports(baseline, candidate), pattern);
  });

test("measurement evidence cannot selectively omit repeats or successful calls", () => {
  for (const mutate of [
    (r) => {
      r.tasks[0].measurement_observations.pop();
    },
    (r) => {
      r.tasks[0].measurement_observations[0].repetition = 5;
    },
    (r) => {
      r.tasks[0].measurement_observations[0].execution_status =
        "harness_invalid";
    },
    (r) => {
      r.tasks[0].measurement_observations[0].latency_ms = null;
    },
    (r) => {
      r.tasks[0].measurement_observations[0].visible_output_bytes = -1;
    },
    (r) => {
      r.tasks[0].measurement_observations[4].latency_ms = 999;
    },
    (r) => {
      r.tasks[0].measurement_observations[0].status = "gold_unknown";
    },
    (r) => {
      r.tasks[0].measurement_observations[0].status = "product_error";
      r.tasks[0].measurement_observations[0].execution_status = "product_error";
    },
  ]) {
    const before = report(),
      after = clone(before);
    mutate(after);
    assert.throws(
      () => compareReports(before, after),
      /measurement|successful-call/,
    );
  }
});

test("same-size but different task or mode sets cannot be compared", () => {
  const before = report([row("example:1")]);
  assert.throws(
    () => compareReports(before, report([row("example:2")])),
    /incompatible expected task sets/,
  );
  assert.throws(
    () =>
      compareReports(
        before,
        report([row("example:1"), row("example:1", 1, { mode: "vector" })]),
      ),
    /incompatible mode sets/,
  );
});

test("product-error zeros retain denominators and expose delivery transitions", () => {
  const failed = row("example:1", null, {
    items: [],
    status: "product_error",
    execution_status: "product_error",
    latency_ms: null,
    visible_output_bytes: null,
  });
  const healthy = row("example:1", 1);
  const recovery = compareReports(report([failed]), report([healthy]));
  assert.equal(recovery.modes.hybrid.file_retrieval.baseline.scored_tasks, 1);
  assert.equal(recovery.modes.hybrid.file_retrieval.delta.mrr_at_10, 1);
  assert.equal(recovery.tasks[0].status_transition, "product_error -> scored");
  assert.equal(
    recovery.tasks[0].execution_transition,
    "product_error -> success",
  );
  assert.equal(
    recovery.modes.hybrid.measurements.baseline.latency_ms_p50,
    null,
  );
  assert.equal(
    recovery.modes.hybrid.semble_official.baseline.repository_macro.ndcg_at_10,
    0,
  );
  assert.match(recovery.warnings[0], /baseline: operational integrity failed/);
  const failure = compareReports(report([healthy]), report([failed]));
  assert.equal(failure.tasks[0].rank_change, "regressed");
  assert.match(markdownComparison(failure), /Operational integrity warnings/);
  const forged = report([failed]);
  forged.tasks[0].items = [{ rank: 1, path: "target.py" }];
  assert.throws(
    () => compareReports(forged, forged),
    /product errors cannot provide/,
  );
});

test("unreviewed Gold remains N/A and changed per-question eligibility is rejected", () => {
  const unreviewed = row("example:2", null, {
    status: "gold_unknown",
    gold_status: "unknown",
  });
  const input = report([row("example:1", 1), unreviewed]);
  const result = compareReports(input, clone(input));
  assert.equal(result.modes.hybrid.file_retrieval.baseline.planned_tasks, 2);
  assert.equal(result.modes.hybrid.file_retrieval.baseline.scored_tasks, 1);
  assert.equal(result.tasks[1].file_retrieval_delta.rr_at_10, null);
  assert.equal(result.tasks[1].semble_official_delta.ndcg_at_10, null);
  assert.equal(result.tasks[1].rank_change, "unscored");
  assert.throws(
    () =>
      compareReports(input, report([row("example:1", 1), row("example:2")])),
    /incompatible gold_status/,
  );
});

test("writes JSON and Markdown with input hashes after validation and refuses overwrite", async (t) => {
  const temp = await mkdtemp(join(tmpdir(), "zg-compare-"));
  t.after(() => rm(temp, { recursive: true, force: true }));
  const baselinePath = join(temp, "baseline.json"),
    candidatePath = join(temp, "candidate.json");
  const baseline = report(),
    candidate = report([row("example:1", 5), row("example:2", 1)]);
  await writeFile(baselinePath, JSON.stringify(baseline));
  await writeFile(candidatePath, JSON.stringify(candidate));
  const output = join(temp, "comparison"),
    result = await writeComparison(baselinePath, candidatePath, output);
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

test("official comparison preserves repeated chunk ranks and ignores cached macro totals", () => {
  const targets = [{ path: "a.py" }, { path: "b.py" }];
  const before = report([row("example:1", null, { items: [], targets })]);
  const items = [
    { rank: 1, path: "a.py" },
    { rank: 2, path: "a.py" },
    { rank: 3, path: "b.py" },
  ];
  const after = report([row("example:1", null, { items, targets })]);
  after.modes.hybrid.semble_official = {
    repository_macro: { ndcg_at_10: 999 },
  };
  const result = compareReports(before, after),
    expected = 1.5 / (1 + 1 / Math.log2(3));
  assert.deepEqual(
    result.tasks[0].candidate.semble_official.target_ranks,
    [1, 3],
  );
  assert.equal(
    result.modes.hybrid.semble_official.candidate.repository_macro.ndcg_at_10,
    expected,
  );
  assert.equal(result.tasks[0].semble_official_delta.ndcg_at_10, expected);
  assert.equal(result.modes.hybrid.file_retrieval.delta.mrr_at_10, 1);
});

test("official comparison rejects changed projection and old quality repetition", () => {
  for (const mutate of [
    (r) => {
      r.tasks[0].semble_official.targets = [{ path: "changed.py" }];
    },
    (r) => {
      r.quality_repetition = 1;
    },
  ]) {
    const baseline = report(),
      candidate = clone(baseline);
    mutate(candidate);
    assert.throws(
      () => compareReports(baseline, candidate),
      /Semble official score|quality repetition 5/,
    );
  }
});

test("standalone ZG validation covers both preview arms and the total error count without mutation", () => {
  const value = pairedReport();
  const snapshot = clone(value);
  const checked = validateZgReport(value);
  assert.equal(checked.previews.short.rows.size, 2);
  assert.equal(checked.previews.full.rows.size, 2);
  assert.deepEqual(value, snapshot);

  const incomplete = clone(value);
  incomplete.tasks.pop();
  assert.throws(
    () => validateZgReport(incomplete),
    /missing task\/mode coverage/,
  );
  const inflated = clone(value);
  inflated.product_error_calls = 1;
  inflated.integrity_passed = false;
  assert.throws(
    () => validateZgReport(inflated),
    /full preview matrix product error count/,
  );
});
