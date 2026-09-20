import assert from "node:assert/strict";
import { loadSuite, objectHash } from "../core/lib.mjs";
import { scoreSembleMetric } from "../metrics/ndcg.mjs";
import {
  FILE_RETRIEVAL_CONTRACT,
  fileRetrievalForRow,
  summarizeFileRetrieval,
} from "../metrics/files.mjs";
import {
  summarizeMeasurements,
  validateCallLatency,
} from "../metrics/measurements.mjs";
import { summarizeSembleOfficial } from "../metrics/summary.mjs";
import { SEMBLE_PROTOCOL } from "../engines/semble/protocol.mjs";

const MODES = ["hybrid", "fts", "vector"];
const FILE_METRICS = ["hit_at_1", "hit_at_5", "hit_at_10", "rr_at_10"];
const CATEGORIES = ["what", "where", "how", "why"];
const key = (row) => JSON.stringify([row.task_id, row.mode]);
const same = (a, b, message) =>
  assert.equal(objectHash(a), objectHash(b), message);
const modeRows = (report) =>
  report.tasks.filter((row) => row.mode === "hybrid");

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

/** Select a presentation arm without treating repeated questions as new samples. */
export function selectPreviewReport(report, preview) {
  assert.ok(report.previews, "report has no preview arms");
  const names = uniqueStrings(Object.keys(report.previews), "preview arms");
  assert.deepEqual(names, ["full", "short"], "incomplete preview arm set");
  assert.equal(report.primary_preview, "short", "invalid primary preview");
  assert.deepEqual(
    uniqueStrings(Object.keys(report.previews.short.modes), "short modes"),
    uniqueStrings(Object.keys(report.previews.full.modes), "full modes"),
    "incompatible mode sets across preview arms",
  );
  assert.ok(names.includes(preview), "unknown preview arm");
  assert.ok(
    report.tasks.every((row) => names.includes(row.preview)),
    "unplanned or missing preview in quality rows",
  );
  // Cached arm tasks/summaries are not scoring inputs. The ordinary report
  // validator checks the selected public rows and recomputes their metrics.
  const selected = {
    ...report,
    preview,
    modes: report.previews[preview].modes,
    tasks: report.tasks.filter((row) => row.preview === preview),
  };
  delete selected.previews;
  return selected;
}

/** Bind row identity and labels to the checked-out frozen suite. */
export function validateFrozenTask(row, suite, label) {
  const task = suite.lock.tasks.find((task) => task.task_id === row.task_id);
  assert.ok(task, `${label}: unknown task ${row.task_id}`);
  for (const field of ["repository", "category"])
    assert.equal(row[field], task[field], `${label}: ${field} mismatch`);
  assert.equal(
    row.language,
    suite.semble_gold[row.task_id].language,
    `${label}: language mismatch`,
  );
  assert.equal(
    row.gold_status,
    suite.gold[row.task_id].status,
    `${label}: Gold status mismatch`,
  );
  assert.deepEqual(
    row.semble_official?.targets,
    suite.semble_gold[row.task_id].targets,
    `${label}: frozen file targets differ`,
  );
}

/** Check per-call measurements before aggregating; legacy reports have none. */
function validateMeasurements(
  row,
  { label, orderedMeasurements, nullErrorOutput },
) {
  const samples = row.measurement_observations;
  assert.ok(
    Array.isArray(samples) && samples.length === 5,
    `${label}: incomplete measurement repetition coverage`,
  );
  const repetitions = samples.map((sample) => sample.repetition);
  assert.deepEqual(
    orderedMeasurements ? repetitions : repetitions.sort((a, b) => a - b),
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
    } else if (nullErrorOutput) {
      assert.equal(
        sample.visible_output_bytes,
        null,
        `${label}: invalid measurement output size`,
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
function validateQualityRow(
  row,
  {
    label,
    currentSchema,
    hasFileContract,
    orderedMeasurements = false,
    nullErrorOutput = false,
  },
) {
  const targets = row.semble_official?.targets;
  assert.ok(
    Array.isArray(targets) && targets.length > 0,
    `${label}: missing Semble file-target projection`,
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
  const official = { targets, ...scoreSembleMetric(row.items, targets) };
  const saved = { ...row.semble_official };
  if (!currentSchema) delete saved.ndcg_at_5;
  assert.deepEqual(
    saved,
    official,
    `${label}: Semble official score differs from public items or frozen projection`,
  );
  if (currentSchema)
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
  const normalized = { ...row, semble_official: official };
  const file = fileRetrievalForRow(normalized);
  if (hasFileContract || Object.hasOwn(row, "file_retrieval")) {
    assert.ok(hasFileContract, `${label}: undeclared file retrieval metric`);
    assert.deepEqual(
      row.file_retrieval,
      file,
      `${label}: file retrieval score differs from public items or frozen projection`,
    );
  }
  if (currentSchema)
    validateMeasurements(row, { label, orderedMeasurements, nullErrorOutput });
  return { ...normalized, file_retrieval: file };
}

/** Validate a ZG report directly, without building a self-comparison. */
export function validateZgReport(report, label = "ZG", { suite } = {}) {
  if (suite) {
    assert.deepEqual(
      report.suite,
      suite.identity,
      `${label}: frozen suite identity mismatch`,
    );
    for (const row of report.tasks ?? [])
      validateFrozenTask(row, suite, `${label}: ${row.task_id}`);
  }
  if (!report.previews) return validateSelectedZgReport(report, label);
  const previews = Object.fromEntries(
    ["short", "full"].map((preview) => [
      preview,
      validateSelectedZgReport(
        selectPreviewReport(report, preview),
        `${label}: ${preview}`,
      ),
    ]),
  );
  if (report.schema_version === 3)
    assert.equal(
      report.product_error_calls,
      report.tasks
        .flatMap((row) => row.measurement_observations)
        .filter((sample) => sample.execution_status === "product_error").length,
      `${label}: full preview matrix product error count differs from measurement evidence`,
    );
  return { previews };
}

function validateSelectedZgReport(report, label) {
  const hasFileContract = Object.hasOwn(report, "file_retrieval_contract");
  if (report.schema_version === 3)
    assert.ok(hasFileContract, `${label}: missing file retrieval contract`);
  if (hasFileContract)
    assert.equal(
      report.file_retrieval_contract,
      FILE_RETRIEVAL_CONTRACT,
      `${label}: incompatible file retrieval contract`,
    );
  assert.ok(
    [1, 2, 3].includes(report.schema_version),
    `${label}: unsupported report schema`,
  );
  if (report.schema_version >= 2)
    assert.ok(
      ["short", "full"].includes(report.preview),
      `${label}: missing selected preview`,
    );
  assert.equal(
    report.quality_repetition,
    5,
    `${label}: comparison requires quality repetition 5`,
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
  assert.equal(
    typeof report.integrity_passed,
    "boolean",
    `${label}: missing integrity state`,
  );
  assert.ok(
    Number.isInteger(report.product_error_calls) &&
      report.product_error_calls >= 0,
    `${label}: invalid product error count`,
  );
  assert.equal(
    report.integrity_passed,
    report.product_error_calls === 0,
    `${label}: integrity flag and product errors disagree`,
  );
  for (const field of ["source", "gold", "semble_gold", "protocol"]) {
    assert.match(
      report.suite?.[field] ?? "",
      /^[a-f0-9]{64}$/,
      `${label}: missing suite ${field} identity`,
    );
  }
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
  assert.ok(
    modes.includes("hybrid") && modes.every((mode) => MODES.includes(mode)),
    `${label}: unsupported mode selection`,
  );
  assert.ok(Array.isArray(report.tasks), `${label}: missing task rows`);
  const rows = new Map();
  for (const row of report.tasks) {
    const context = `${label}: ${row.task_id}/${row.mode}`;
    if (report.preview !== undefined)
      assert.equal(row.preview, report.preview, `${context}: preview mismatch`);
    assert.ok(
      ids.includes(row.task_id) && modes.includes(row.mode),
      `${context}: unexpected task/mode`,
    );
    assert.ok(!rows.has(key(row)), `${context}: duplicate task/mode`);
    assert.equal(
      row.repetition,
      5,
      `${context}: comparison requires quality repetition 5`,
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
    assert.ok(
      typeof row.repository === "string" && row.repository.length > 0,
      `${context}: missing repository`,
    );
    assert.ok(
      typeof row.language === "string" && row.language.length > 0,
      `${context}: missing benchmark language`,
    );
    validateQualityRow(row, {
      label: context,
      currentSchema: report.schema_version === 3,
      hasFileContract,
    });
    rows.set(key(row), row);
  }
  assert.equal(
    rows.size,
    ids.length * modes.length,
    `${label}: missing task/mode coverage`,
  );
  assert.ok(
    report.product_error_calls >=
      report.tasks.filter((row) => row.execution_status === "product_error")
        .length,
    `${label}: product error count omits quality errors`,
  );
  if (report.schema_version === 3)
    assert.ok(
      report.product_error_calls >=
        report.tasks
          .flatMap((row) => row.measurement_observations)
          .filter((sample) => sample.execution_status === "product_error")
          .length,
      `${label}: product error count omits measurement failures`,
    );
  return { ids, modes, rows };
}

export function validateFrozenQualityRows(
  report,
  suite,
  label,
  { nullErrorOutput = false } = {},
) {
  assert.equal(
    report.file_retrieval_contract,
    FILE_RETRIEVAL_CONTRACT,
    `${label}: file retrieval contract mismatch`,
  );
  assert.equal(
    report.quality_repetition,
    5,
    `${label}: fifth quality observation required`,
  );
  assert.equal(
    report.quality_score_valid,
    true,
    `${label}: invalid quality report`,
  );
  assert.equal(
    report.scope,
    "full-20-original-queries",
    `${label}: full 20-question comparison required`,
  );
  same(
    report.expected_task_ids,
    suite.lock.tasks.map((task) => task.task_id),
    `${label}: task set/order mismatch`,
  );
  assert.ok(
    Array.isArray(report.integrity_errors) &&
      report.integrity_errors.length === 0,
    `${label}: integrity errors`,
  );
  assert.ok(
    Number.isInteger(report.product_error_calls) &&
      report.product_error_calls >= 0,
    `${label}: invalid product errors`,
  );
  assert.equal(
    report.integrity_passed,
    report.product_error_calls === 0,
    `${label}: inconsistent integrity state`,
  );
  for (const field of ["source", "gold", "semble_gold"])
    assert.equal(
      report.suite?.[field],
      suite.identity[field],
      `${label}: ${field} identity mismatch`,
    );
  const rows = modeRows(report);
  assert.equal(rows.length, 20, `${label}: missing/duplicate hybrid rows`);
  assert.equal(
    new Set(rows.map((row) => row.task_id)).size,
    20,
    `${label}: duplicate hybrid task`,
  );
  const derived = suite.lock.tasks.map((task) => {
    const row = rows.find((item) => item.task_id === task.task_id);
    assert.ok(row, `${label}: missing ${task.task_id}`);
    validateFrozenTask(row, suite, label);
    assert.equal(row.repetition, 5, `${label}: quality repetition mismatch`);
    assert.equal(
      row.quality_observation,
      true,
      `${label}: not quality observation`,
    );
    return validateQualityRow(row, {
      label,
      currentSchema: true,
      hasFileContract: true,
      orderedMeasurements: true,
      nullErrorOutput,
    });
  });
  same(
    report.modes.hybrid.file_retrieval,
    summarizeFileRetrieval(derived),
    `${label}: file retrieval aggregate differs from public items`,
  );
  same(
    report.modes.hybrid.semble_official,
    summarizeSembleOfficial(derived),
    `${label}: Semble aggregate differs from public items`,
  );
  same(
    report.modes.hybrid.measurements,
    summarizeMeasurements(
      derived.flatMap((row) => row.measurement_observations),
    ),
    `${label}: measurement aggregate differs from observations`,
  );
  return derived;
}

/** Validate a standalone full Semble result without requiring a zg report. */
export async function validateSembleReport(report, suite = undefined) {
  suite ??= await loadSuite();
  assert.equal(report.schema_version, 2, "Semble: unsupported report schema");
  assert.equal(report.engine, "semble", "Semble: wrong engine");
  same(report.protocol, SEMBLE_PROTOCOL, "Semble: protocol mismatch");
  assert.equal(
    report.suite?.protocol,
    objectHash(SEMBLE_PROTOCOL),
    "Semble: protocol hash mismatch",
  );
  assert.equal(
    report.observed_calls,
    100,
    "Semble: full 100-call coverage required",
  );
  same(
    Object.keys(report.modes ?? {}),
    ["hybrid"],
    "Semble: only hybrid mode is planned",
  );
  assert.ok(
    Array.isArray(report.tasks) && report.tasks.length === 20,
    "Semble: missing/duplicate or extra task coverage",
  );
  assert.ok(
    report.tasks.every(
      (row) => row.mode === "hybrid" && !Object.hasOwn(row, "preview"),
    ),
    "Semble: unplanned task mode or preview",
  );
  const rows = validateFrozenQualityRows(report, suite, "Semble", {
    nullErrorOutput: true,
  });
  assert.equal(
    report.product_error_calls,
    rows
      .flatMap((row) => row.measurement_observations)
      .filter((observation) => observation.execution_status === "product_error")
      .length,
    "Semble: product error count differs from all 100 measurement observations",
  );
  return rows;
}
