import assert from "node:assert/strict";
import { validateSearchRoute } from "../engines/zg/parse.mjs";
import { scoreNdcg } from "../metrics/ndcg.mjs";
import {
  FILE_RETRIEVAL_CONTRACT,
  fileRetrievalForRow,
} from "../metrics/files.mjs";
import { validateCallLatency } from "../metrics/measurements.mjs";

export const ZG_MODES = Object.freeze(["hybrid", "fts", "vector"]);
const FILE_METRICS = ["hit_at_1", "hit_at_5", "hit_at_10", "rr_at_10"];
const CATEGORIES = ["what", "where", "how", "why"];
const key = (row) => JSON.stringify([row.task_id, row.mode]);

function uniqueStrings(values, label) {
  assert.ok(
    Array.isArray(values) && values.length > 0,
    `${label}: missing values`,
  );
  assert.ok(
    values.every((value) => typeof value === "string" && value.length > 0),
    `${label}: invalid value`,
  );
  assert.equal(
    new Set(values).size,
    values.length,
    `${label}: duplicate value`,
  );
  return [...values].sort();
}

/** Bind row identity and labels to the checked-out frozen suite. */
export function validateFrozenTask(row, suite, label) {
  const task = suite.lock.tasks.find((task) => task.task_id === row.task_id);
  assert.ok(task, `${label}: unknown task ${row.task_id}`);
  for (const field of ["repository", "category"])
    assert.equal(row[field], task[field], `${label}: ${field} mismatch`);
  assert.equal(
    row.language,
    suite.file_gold[row.task_id].language,
    `${label}: language mismatch`,
  );
  assert.equal(
    row.gold_status,
    suite.gold[row.task_id].status,
    `${label}: Gold status mismatch`,
  );
  assert.deepEqual(
    row.ndcg?.targets,
    suite.file_gold[row.task_id].targets,
    `${label}: frozen file targets differ`,
  );
}

/** Check all five calls before aggregation can omit invalid measurements. */
function validateMeasurements(row, label) {
  const samples = row.measurement_observations;
  assert.ok(
    Array.isArray(samples) && samples.length === 5,
    `${label}: incomplete measurement repetition coverage`,
  );
  const repetitions = samples.map((sample) => sample.repetition);
  assert.deepEqual(
    repetitions.sort((a, b) => a - b),
    [1, 2, 3, 4, 5],
    `${label}: invalid measurement repetition coverage`,
  );
  for (const sample of samples) {
    assert.ok(
      ["success", "product_error"].includes(sample.execution_status),
      `${label}: invalid measurement execution status`,
    );
    assert.equal(
      sample.status,
      row.gold_status === "reviewed"
        ? sample.execution_status === "success"
          ? "scored"
          : "product_error"
        : `gold_${row.gold_status}`,
      `${label}: inconsistent measurement scoring status`,
    );
    try {
      validateCallLatency(sample);
    } catch (error) {
      throw new Error(`${label}: measurement ${error.message}`, {
        cause: error,
      });
    }
    if (sample.execution_status === "success") {
      assert.ok(
        Number.isSafeInteger(sample.visible_output_bytes) &&
          sample.visible_output_bytes >= 0,
        `${label}: invalid successful-call output bytes`,
      );
    }
  }
  const quality = samples.find((sample) => sample.repetition === 5);
  for (const field of [
    "status",
    "execution_status",
    "latency_ms",
    "visible_output_bytes",
  ])
    assert.equal(
      quality[field],
      row[field],
      `${label}: quality measurement differs from quality row`,
    );
}

/** Recompute quality from native public items and verify saved row evidence. */
function validateQualityRow(row, label) {
  const targets = row.ndcg?.targets;
  assert.ok(
    Array.isArray(targets) && targets.length > 0,
    `${label}: missing frozen file-target projection`,
  );
  assert.ok(
    targets.every(
      (target) =>
        Object.keys(target).length === 1 &&
        typeof target.path === "string" &&
        target.path.length > 0,
    ),
    `${label}: expected projected file-only targets`,
  );
  assert.equal(
    new Set(targets.map((target) => target.path)).size,
    targets.length,
    `${label}: duplicate projected file targets`,
  );
  assert.ok(Array.isArray(row.items), `${label}: missing public parsed items`);
  assert.ok(
    ["success", "product_error"].includes(row.execution_status),
    `${label}: invalid execution status`,
  );
  if (row.execution_status === "product_error")
    assert.equal(
      row.items.length,
      0,
      `${label}: product errors cannot provide retrieval credit`,
    );
  if (row.gold_status === "reviewed") {
    assert.equal(
      row.status,
      row.execution_status === "success" ? "scored" : "product_error",
      `${label}: inconsistent scored status`,
    );
  } else {
    assert.ok(
      ["unknown", "disputed"].includes(row.gold_status),
      `${label}: invalid gold status`,
    );
    assert.equal(
      row.status,
      `gold_${row.gold_status}`,
      `${label}: inconsistent unscored status`,
    );
  }
  validateSearchRoute(row.items, row.mode);
  const ndcg = { targets, ...scoreNdcg(row.items, targets) };
  const saved = { ...row.ndcg };
  assert.deepEqual(
    saved,
    ndcg,
    `${label}: nDCG score differs from public items or frozen projection`,
  );
  for (const field of [
    "first_hit_rank",
    ...FILE_METRICS,
    "ndcg_at_5",
    "ndcg_at_10",
    "target_matches",
    "repeat_ranks",
    "stage_evidence",
  ])
    assert.ok(
      !Object.hasOwn(row, field),
      `${label}: obsolete report field ${field}`,
    );
  const normalized = { ...row, ndcg };
  const file = fileRetrievalForRow(normalized);
  assert.deepEqual(
    row.file_retrieval,
    file,
    `${label}: file retrieval score differs from public items or frozen projection`,
  );
  validateMeasurements(row, label);
  return { ...normalized, file_retrieval: file };
}

/** Validate the full-preview, three-mode report from saved public evidence. */
export function validateZgReport(report, label = "ZG", { suite } = {}) {
  assert.equal(
    report.schema_version,
    5,
    `${label}: unsupported report schema; schema 5 required`,
  );
  assert.equal(report.preview, "full", `${label}: full preview required`);
  for (const field of [
    "previews",
    "primary_preview",
    "paired_preview_comparison",
    "preview_pairs",
  ])
    assert.ok(
      !Object.hasOwn(report, field),
      `${label}: obsolete preview field ${field}`,
    );
  assert.equal(
    report.file_retrieval_contract,
    FILE_RETRIEVAL_CONTRACT,
    `${label}: incompatible file retrieval contract`,
  );
  assert.equal(
    report.quality_repetition,
    5,
    `${label}: quality repetition 5 required`,
  );
  assert.equal(
    report.quality_score_valid,
    true,
    `${label}: invalid quality report`,
  );
  assert.ok(
    Array.isArray(report.integrity_errors) &&
      report.integrity_errors.length === 0,
    `${label}: experimental integrity errors`,
  );
  assert.ok(
    Number.isSafeInteger(report.product_error_calls) &&
      report.product_error_calls >= 0,
    `${label}: invalid product error count`,
  );
  assert.equal(
    report.integrity_passed,
    report.product_error_calls === 0,
    `${label}: integrity flag and product errors disagree`,
  );
  for (const field of ["source", "gold", "file_gold", "protocol"])
    assert.match(
      report.suite?.[field] ?? "",
      /^[a-f0-9]{64}$/,
      `${label}: missing suite ${field} identity`,
    );
  if (suite)
    assert.deepEqual(
      report.suite,
      suite.identity,
      `${label}: frozen suite identity mismatch`,
    );

  const ids = uniqueStrings(
    report.expected_task_ids,
    `${label}: expected tasks`,
  );
  assert.ok(
    ["full-20-original-queries", "explicit-subset"].includes(report.scope),
    `${label}: unknown scope`,
  );
  assert.ok(
    report.scope === "full-20-original-queries"
      ? ids.length === 20
      : ids.length < 20,
    `${label}: scope/task-count mismatch`,
  );
  assert.ok(
    report.modes &&
      typeof report.modes === "object" &&
      !Array.isArray(report.modes),
    `${label}: missing modes`,
  );
  const modes = uniqueStrings(Object.keys(report.modes), `${label}: modes`);
  assert.deepEqual(
    modes,
    [...ZG_MODES].sort(),
    `${label}: all three modes are required`,
  );
  assert.equal(
    report.observed_calls,
    ids.length * ZG_MODES.length * 5,
    `${label}: incomplete call matrix`,
  );
  assert.ok(Array.isArray(report.tasks), `${label}: missing task rows`);
  const rows = new Map();
  for (const row of report.tasks) {
    const context = `${label}: ${row.task_id}/${row.mode}`;
    assert.equal(row.preview, "full", `${context}: full preview required`);
    assert.ok(
      ids.includes(row.task_id) && modes.includes(row.mode),
      `${context}: unexpected task/mode`,
    );
    assert.ok(!rows.has(key(row)), `${context}: duplicate task/mode`);
    assert.equal(
      row.repetition,
      5,
      `${context}: quality repetition 5 required`,
    );
    assert.equal(
      row.quality_observation,
      true,
      `${context}: not a quality observation`,
    );
    assert.ok(
      CATEGORIES.includes(row.category),
      `${context}: invalid category`,
    );
    for (const field of ["repository", "language"])
      assert.ok(
        typeof row[field] === "string" && row[field].length > 0,
        `${context}: missing ${field}`,
      );
    if (suite) validateFrozenTask(row, suite, context);
    rows.set(key(row), validateQualityRow(row, context));
  }
  assert.equal(
    rows.size,
    ids.length * ZG_MODES.length,
    `${label}: missing task/mode coverage`,
  );
  assert.equal(
    report.product_error_calls,
    report.tasks
      .flatMap((row) => row.measurement_observations)
      .filter((sample) => sample.execution_status === "product_error").length,
    `${label}: product error count differs from measurement evidence`,
  );
  return { ids, modes: [...ZG_MODES], rows };
}
