import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { join, resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { scoreSembleMetric } from "./semble-metrics.mjs";
import {
  FILE_RETRIEVAL_CONTRACT,
  fileRetrievalForRow,
  summarizeFileRetrieval,
} from "./file-retrieval-metrics.mjs";
import { summarizeMeasurements } from "./measurement-metrics.mjs";
import { summarizeSembleOfficial } from "./report.mjs";

const MODES = ["hybrid", "fts", "vector"];
const HITS = ["hit_at_1", "hit_at_5", "hit_at_10"];
const FILE_METRICS = [...HITS, "rr_at_10"];
const CATEGORIES = ["what", "where", "how", "why"];
const hash = (value) => createHash("sha256").update(value).digest("hex");
const difference = (before, after) => (before === null ? null : after - before);
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

function validateReport(report, label) {
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
    const targets = row.semble_official?.targets;
    assert.ok(
      Array.isArray(targets) && targets.length > 0,
      `${context}: missing Semble file-target projection`,
    );
    assert.ok(
      targets.every(
        (target) =>
          Object.keys(target).length === 1 &&
          typeof target.path === "string" &&
          target.path.length > 0,
      ),
      `${context}: expected projected file-only targets`,
    );
    assert.equal(
      new Set(targets.map((target) => target.path)).size,
      targets.length,
      `${context}: duplicate projected file targets`,
    );
    assert.ok(
      Array.isArray(row.items),
      `${context}: missing public parsed items`,
    );
    if (row.execution_status === "product_error")
      assert.equal(
        row.items.length,
        0,
        `${context}: product errors cannot provide official retrieval credit`,
      );
    const official = { ...row.semble_official };
    // Earlier report schemas retain an unused @5 field. The five supported
    // metrics are always rederived from their saved public items, never aliases
    // of legacy anchor scores or cached report summaries.
    if (report.schema_version < 3) delete official.ndcg_at_5;
    assert.deepEqual(
      official,
      { targets, ...scoreSembleMetric(row.items, targets) },
      `${context}: Semble official score differs from public items`,
    );
    if (report.schema_version === 3)
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
          `${context}: obsolete report field ${field}`,
        );
    if (hasFileContract || Object.hasOwn(row, "file_retrieval")) {
      assert.ok(
        hasFileContract,
        `${context}: undeclared file retrieval metric`,
      );
      assert.deepEqual(
        row.file_retrieval,
        fileRetrievalForRow(row),
        `${context}: file retrieval score differs from public items`,
      );
    }
    assert.ok(
      ["success", "product_error"].includes(row.execution_status),
      `${context}: invalid execution status`,
    );
    if (row.gold_status === "reviewed") {
      assert.equal(
        row.status,
        row.execution_status === "success" ? "scored" : "product_error",
        `${context}: inconsistent scored status`,
      );
    } else {
      assert.ok(
        ["unknown", "disputed"].includes(row.gold_status),
        `${context}: invalid gold status`,
      );
      assert.equal(
        row.status,
        `gold_${row.gold_status}`,
        `${context}: inconsistent unscored status`,
      );
    }
    if (report.schema_version === 3) {
      const samples = row.measurement_observations;
      assert.ok(
        Array.isArray(samples) && samples.length === 5,
        `${context}: incomplete measurement repetition coverage`,
      );
      assert.deepEqual(
        samples.map((sample) => sample.repetition).sort((a, b) => a - b),
        [1, 2, 3, 4, 5],
        `${context}: invalid measurement repetition coverage`,
      );
      for (const sample of samples) {
        assert.ok(
          ["success", "product_error"].includes(sample.execution_status),
          `${context}: invalid measurement execution status`,
        );
        assert.ok(
          ["scored", "product_error", "gold_unknown", "gold_disputed"].includes(
            sample.status,
          ),
          `${context}: invalid measurement scoring status`,
        );
        if (sample.execution_status === "success") {
          assert.ok(
            Number.isFinite(sample.latency_ms) && sample.latency_ms >= 0,
            `${context}: invalid successful-call latency`,
          );
          assert.ok(
            Number.isSafeInteger(sample.visible_output_bytes) &&
              sample.visible_output_bytes >= 0,
            `${context}: invalid successful-call output bytes`,
          );
        }
        assert.equal(
          sample.status,
          row.gold_status === "reviewed"
            ? sample.execution_status === "success"
              ? "scored"
              : "product_error"
            : `gold_${row.gold_status}`,
          `${context}: inconsistent measurement status`,
        );
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
          `${context}: quality measurement differs from quality row`,
        );
    }
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

function scoreView(row) {
  const targets = row.semble_official.targets;
  return {
    ...Object.fromEntries(
      [
        "task_id",
        "status",
        "execution_status",
        "gold_status",
        "repository",
        "language",
        "items",
      ].map((field) => [field, row[field]]),
    ),
    file_retrieval: fileRetrievalForRow(row),
    semble_official:
      fileRetrievalForRow(row) === null
        ? null
        : { targets, ...scoreSembleMetric(row.items, targets) },
  };
}

function filePair(pairs) {
  const baseline = summarizeFileRetrieval(pairs.map((pair) => pair.baseline));
  const candidate = summarizeFileRetrieval(pairs.map((pair) => pair.candidate));
  return {
    baseline,
    candidate,
    delta: Object.fromEntries(
      [...HITS, "mrr_at_10"].map((field) => [
        field,
        difference(baseline[field], candidate[field]),
      ]),
    ),
  };
}

function officialPair(pairs) {
  const baseline = summarizeSembleOfficial(pairs.map((pair) => pair.baseline));
  const candidate = summarizeSembleOfficial(
    pairs.map((pair) => pair.candidate),
  );
  return {
    baseline,
    candidate,
    delta: Object.fromEntries(
      ["query_mean", "repository_macro", "language_macro"].map(
        (aggregation) => [
          aggregation,
          Object.fromEntries(
            ["ndcg_at_10"].map((metric) => [
              metric,
              difference(
                baseline[aggregation][metric],
                candidate[aggregation][metric],
              ),
            ]),
          ),
        ],
      ),
    ),
  };
}

function reportIdentity(report) {
  const unique = (getter) =>
    [
      ...new Set((report.repositories ?? []).map(getter).filter(Boolean)),
    ].sort();
  return {
    generated_at: report.generated_at ?? null,
    integrity_passed: report.integrity_passed,
    product_error_calls: report.product_error_calls,
    candidate_commits: unique((repo) => repo.candidate_commit),
    package_sha256: unique((repo) => repo.package?.tarball_sha256),
    model_files_sha256: unique((repo) => repo.model_files_sha256),
  };
}

/** Compare validated quality rows; task ordering and cached aggregate summaries are not score inputs. */
export function compareReports(baseline, candidate) {
  if (baseline.previews || candidate.previews) {
    assert.ok(
      baseline.previews && candidate.previews,
      "incompatible preview arm sets",
    );
    const previews = Object.fromEntries(
      ["short", "full"].map((preview) => [
        preview,
        compareReports(
          selectPreviewReport(baseline, preview),
          selectPreviewReport(candidate, preview),
        ),
      ]),
    );
    for (const [label, report] of [
      ["baseline", baseline],
      ["candidate", candidate],
    ])
      if (report.schema_version === 3)
        assert.equal(
          report.product_error_calls,
          report.tasks
            .flatMap((row) => row.measurement_observations)
            .filter((sample) => sample.execution_status === "product_error")
            .length,
          `${label}: full preview matrix product error count differs from measurement evidence`,
        );
    return { ...previews.short, primary_preview: "short", previews };
  }
  assert.equal(
    baseline.preview,
    candidate.preview,
    "incompatible preview arms",
  );
  const before = validateReport(baseline, "baseline");
  const after = validateReport(candidate, "candidate");
  for (const field of ["source", "gold", "semble_gold", "protocol"])
    assert.equal(
      baseline.suite[field],
      candidate.suite[field],
      `incompatible suite ${field}`,
    );
  assert.equal(baseline.scope, candidate.scope, "incompatible report scopes");
  assert.deepEqual(before.ids, after.ids, "incompatible expected task sets");
  assert.deepEqual(before.modes, after.modes, "incompatible mode sets");
  const modes = MODES.filter((mode) => before.modes.includes(mode));
  const pairs = [];
  for (const mode of modes)
    for (const task_id of baseline.expected_task_ids) {
      const oldRow = before.rows.get(key({ task_id, mode }));
      const newRow = after.rows.get(key({ task_id, mode }));
      for (const field of ["repository", "category", "gold_status", "language"])
        assert.equal(
          oldRow[field],
          newRow[field],
          `${task_id}/${mode}: incompatible ${field}`,
        );
      assert.deepEqual(
        oldRow.semble_official.targets,
        newRow.semble_official.targets,
        `${task_id}/${mode}: incompatible Semble target projection`,
      );
      const oldFile = fileRetrievalForRow(oldRow);
      const newFile = fileRetrievalForRow(newRow);
      assert.equal(
        oldFile === null,
        newFile === null,
        `${task_id}/${mode}: changed scoring eligibility`,
      );
      const fileDelta = Object.fromEntries(
        FILE_METRICS.map((metric) => [
          metric,
          difference(oldFile?.[metric] ?? null, newFile?.[metric] ?? null),
        ]),
      );
      pairs.push({
        task_id,
        mode,
        repository: oldRow.repository,
        category: oldRow.category,
        baseline: scoreView(oldRow),
        candidate: scoreView(newRow),
        file_retrieval_delta: fileDelta,
        semble_official_delta: Object.fromEntries(
          ["ndcg_at_10"].map((metric) => [
            metric,
            oldFile === null
              ? null
              : newRow.semble_official[metric] - oldRow.semble_official[metric],
          ]),
        ),
        rank_change:
          fileDelta.rr_at_10 === null
            ? "unscored"
            : fileDelta.rr_at_10 > 0
              ? "improved"
              : fileDelta.rr_at_10 < 0
                ? "regressed"
                : "unchanged",
        status_transition: `${oldRow.status} -> ${newRow.status}`,
        execution_transition: `${oldRow.execution_status} -> ${newRow.execution_status}`,
      });
    }
  return {
    schema_version: 2,
    file_retrieval_contract: FILE_RETRIEVAL_CONTRACT,
    ...(baseline.preview === undefined ? {} : { preview: baseline.preview }),
    quality_repetition: 5,
    suite: structuredClone(baseline.suite),
    scope: baseline.scope,
    expected_task_ids: [...baseline.expected_task_ids],
    task_order_policy:
      "Task IDs and modes match as sets; tables follow baseline task order and hybrid/fts/vector mode order.",
    quality_gate: "report-only; no quality threshold or causal attribution",
    aggregation:
      "Five metrics recomputed from matched repetition-5 public items on the SWE-QA accepted-file projection: file Hit@1/5/10 and MRR@10 use query means; Semble nDCG@10 uses the repository macro.",
    baseline: reportIdentity(baseline),
    candidate: reportIdentity(candidate),
    warnings: [baseline, candidate].flatMap((report, i) =>
      report.integrity_passed
        ? []
        : [
            `${i === 0 ? "baseline" : "candidate"}: operational integrity failed with ${report.product_error_calls} product-error calls; valid reviewed quality zeros are retained. Differences can include delivery failures and are not pure ranking effects.`,
          ],
    ),
    modes: Object.fromEntries(
      modes.map((mode) => {
        const rows = pairs.filter((pair) => pair.mode === mode);
        return [
          mode,
          {
            measurements: {
              baseline: summarizeMeasurements(
                rows.flatMap(
                  (pair) =>
                    before.rows.get(key(pair)).measurement_observations ?? [],
                ),
              ),
              candidate: summarizeMeasurements(
                rows.flatMap(
                  (pair) =>
                    after.rows.get(key(pair)).measurement_observations ?? [],
                ),
              ),
            },
            file_retrieval: filePair(rows),
            semble_official: officialPair(rows),
          },
        ];
      }),
    ),
    tasks: pairs,
  };
}

const cell = (value) =>
  String(value ?? "N/A")
    .replaceAll("|", "\\|")
    .replace(/[\r\n]+/g, " ");
const number = (value) =>
  typeof value === "number" ? value.toFixed(4) : "N/A";
const signed = (value) =>
  typeof value === "number"
    ? `${value > 0 ? "+" : ""}${value.toFixed(4)}`
    : "N/A";

export function markdownComparison(result) {
  const variants = result.previews
    ? Object.entries(result.previews)
    : [[result.preview ?? "default", result]];
  const lines = [
    "# zg Retrieval-only version comparison",
    "",
    `Scope: **${result.scope} / ${result.expected_task_ids.length} original questions**. Quality uses the fifth call. Delta is candidate minus baseline.`,
    "",
    "Source, Gold, frozen accepted-file targets and protocol identities match; task/mode coverage and scoring eligibility are validated. All five metrics are recomputed from saved public result items. This is a report-only comparison with no quality threshold or causal attribution.",
    "",
    "| Mode / preview / version | File Hit@1 | File Hit@5 | File Hit@10 | File MRR@10 | Semble nDCG@10 | Output mean (KiB) | Latency P50 (ms) |",
    "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ...variants.flatMap(([preview, report]) =>
      Object.entries(report.modes).flatMap(([mode, entry]) =>
        ["baseline", "candidate"].map((side) => {
          const file = entry.file_retrieval[side];
          return `| ${mode} / ${preview} / ${side} | ${file.hit_at_1_count}/${file.scored_tasks} | ${file.hit_at_5_count}/${file.scored_tasks} | ${file.hit_at_10_count}/${file.scored_tasks} | ${number(file.mrr_at_10)} | ${number(entry.semble_official[side].repository_macro.ndcg_at_10)} | ${number(entry.measurements[side].output_bytes_mean == null ? null : entry.measurements[side].output_bytes_mean / 1024)} | ${number(entry.measurements[side].latency_ms_p50)} |`;
        }),
      ),
    ),
    "",
    "File Hit@1/5/10 and MRR@10 weight original questions equally. Semble nDCG@10 first averages questions within each repository, then weights repositories equally. Native ranks are preserved without deduplication. These SWE-QA accepted-file projection scores measure file localization, not sufficient answer evidence; five repetitions are not independent questions.",
  ];
  const warnings = [
    ...new Set(variants.flatMap(([, report]) => report.warnings)),
  ];
  if (warnings.length)
    lines.push(
      "",
      "Operational integrity warnings:",
      "",
      ...warnings.map((warning) => `- ${cell(warning)}`),
    );
  lines.push(
    "",
    "<details>",
    "<summary>Per-question changes</summary>",
    "",
    "| Question / mode / preview | File first rank (baseline → candidate) | ΔFile Hit@1 | ΔFile Hit@5 | ΔFile Hit@10 | ΔFile RR@10 | ΔSemble nDCG@10 | Execution |",
    "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ...variants.flatMap(([preview, report]) =>
      report.tasks.map(
        (row) =>
          `| ${cell(row.task_id)} / ${cell(row.mode)} / ${preview} | ${cell(row.baseline.file_retrieval?.first_hit_rank)} → ${cell(row.candidate.file_retrieval?.first_hit_rank)} | ${signed(row.file_retrieval_delta.hit_at_1)} | ${signed(row.file_retrieval_delta.hit_at_5)} | ${signed(row.file_retrieval_delta.hit_at_10)} | ${signed(row.file_retrieval_delta.rr_at_10)} | ${signed(row.semble_official_delta.ndcg_at_10)} | ${cell(row.execution_transition)} |`,
      ),
    ),
    "",
    "</details>",
    "",
    ...variants.flatMap(([preview, report]) =>
      Object.entries(report.modes).flatMap(([mode, entry]) =>
        ["baseline", "candidate"].map(
          (side) =>
            `${mode} / ${preview} / ${side}: output samples **${entry.measurements[side].output_sample_count}** (successful fifth calls); latency samples **${entry.measurements[side].latency_sample_count}** (all successful valid calls).`,
        ),
      ),
    ),
    "",
    "Output means use public UTF-8 bytes / 1024; latency P50 includes session load and fixed-order cache effects. Measurements are recomputed from saved per-call metadata, whose raw-response identities are audited by aggregation; this comparison does not reread raw captures. Older reports without complete per-call measurement evidence show N/A. Cross-environment timings do not establish a speed winner.",
    "",
    "Product-error zeros remain in the denominator. Invalid experiments are rejected; unreviewed Gold is N/A. JSON retains input identities, target ranks and per-question status transitions.",
  );
  return lines.join("\n") + "\n";
}

export async function writeComparison(baselinePath, candidatePath, outputPath) {
  const paths = [resolve(baselinePath), resolve(candidatePath)];
  const bytes = await Promise.all(paths.map((path) => readFile(path)));
  const result = compareReports(...bytes.map((raw) => JSON.parse(raw)));
  result.inputs = Object.fromEntries(
    paths.map((path, i) => [
      i === 0 ? "baseline" : "candidate",
      { path, sha256: hash(bytes[i]) },
    ]),
  );
  const output = resolve(outputPath);
  // Validate before creating artifacts; never overwrite a previous comparison.
  await mkdir(output, { recursive: false });
  await writeFile(
    join(output, "comparison.json"),
    `${JSON.stringify(result, null, 2)}\n`,
  );
  await writeFile(join(output, "comparison.md"), markdownComparison(result));
  return result;
}

if (
  process.argv[1] &&
  import.meta.url === pathToFileURL(process.argv[1]).href
) {
  const args = process.argv.slice(2);
  if (args.length !== 3) {
    console.error(
      "Usage: node compare.mjs BASELINE_REPORT.json CANDIDATE_REPORT.json NEW_OUTPUT_DIR",
    );
    process.exitCode = 1;
  } else {
    writeComparison(...args)
      .then(() =>
        console.log(`Comparison: ${join(resolve(args[2]), "comparison.md")}`),
      )
      .catch((error) => {
        console.error(error);
        process.exitCode = 1;
      });
  }
}
