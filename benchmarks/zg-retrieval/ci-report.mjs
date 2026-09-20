import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import { join, resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { compareReports } from "./compare.mjs";
import { summarizeFileRetrieval } from "./file-retrieval-metrics.mjs";
import { loadSuite, readJson, writeJson } from "./lib.mjs";
import { summarizeMeasurements } from "./measurement-metrics.mjs";
import { summarizeSembleOfficial } from "./report.mjs";
import { compareSembleToZg, validateSembleReport } from "./semble-report.mjs";

export const QUALITY_METRICS = Object.freeze([
  "file_hit_at_1",
  "file_hit_at_5",
  "file_hit_at_10",
  "file_mrr_at_10",
  "ndcg_at_10",
]);

function resultRow(label, rows, report) {
  const file = summarizeFileRetrieval(rows);
  const official = summarizeSembleOfficial(rows);
  assert.equal(file.scored_tasks, 20, `${label}: requires all 20 questions`);
  return {
    label,
    status: report.integrity_passed ? "success" : "product_error",
    questions: file.scored_tasks,
    metrics: {
      file_hit_at_1: file.hit_at_1,
      file_hit_at_5: file.hit_at_5,
      file_hit_at_10: file.hit_at_10,
      file_mrr_at_10: file.mrr_at_10,
      ndcg_at_10: official.repository_macro.ndcg_at_10,
    },
    measurements: summarizeMeasurements(
      rows.flatMap((row) => row.measurement_observations),
    ),
  };
}

const unavailableRow = (label, status) => ({
  label,
  status,
  questions: null,
  metrics: null,
  measurements: null,
});

/** One overview for the requested engines; missing results never become zeros. */
export async function buildCiSummary({
  zg = null,
  semble = null,
  sembleRequested = false,
  modes = ["hybrid"],
  jobResults = {},
  runUrl = null,
  commit = null,
} = {}) {
  assert.equal(typeof sembleRequested, "boolean");
  assert.ok(
    modes.length > 0 &&
      modes.includes("hybrid") &&
      new Set(modes).size === modes.length &&
      modes.every((mode) => ["hybrid", "fts", "vector"].includes(mode)),
    "invalid requested ZG modes",
  );
  const suite = await loadSuite();
  const rows = [];
  const errors = [];
  const labels = ["short", "full"].flatMap((preview) =>
    modes.map((mode) => `ZG ${mode} / ${preview}`),
  );
  let zgValid = false;
  let sembleValid = false;
  try {
    assert.ok(zg, "missing ZG report");
    assert.equal(zg.schema_version, 3, "requires current ZG report schema");
    assert.equal(zg.scope, "full-20-original-queries");
    assert.deepEqual(zg.suite, suite.identity, "ZG frozen suite mismatch");
    assert.deepEqual(
      [...zg.expected_task_ids].sort(),
      suite.lock.tasks.map((task) => task.task_id).sort(),
    );
    assert.equal(zg.observed_calls, 20 * 2 * 5 * modes.length);
    assert.deepEqual(Object.keys(zg.modes).sort(), [...modes].sort());
    assert.equal(
      zg.tasks.length,
      20 * 2 * modes.length,
      "ZG quality matrix differs from requested modes",
    );
    for (const preview of ["short", "full"])
      assert.deepEqual(
        Object.keys(zg.previews[preview].modes).sort(),
        [...modes].sort(),
        `${preview}: mode selection differs from request`,
      );
    for (const row of zg.tasks) {
      assert.ok(modes.includes(row.mode), "unrequested ZG mode");
      const task = suite.lock.tasks.find(
        (task) => task.task_id === row.task_id,
      );
      assert.ok(task, `unknown ZG task: ${row.task_id}`);
      assert.equal(
        row.repository,
        task.repository,
        `${row.task_id}: repository mismatch`,
      );
      assert.equal(
        row.category,
        task.category,
        `${row.task_id}: category mismatch`,
      );
      assert.equal(
        row.language,
        suite.semble_gold[row.task_id].language,
        `${row.task_id}: language mismatch`,
      );
      assert.equal(
        row.gold_status,
        suite.gold[row.task_id].status,
        `${row.task_id}: Gold status mismatch`,
      );
      assert.deepEqual(
        row.semble_official.targets,
        suite.semble_gold[row.task_id].targets,
        `${row.task_id}: frozen file targets differ`,
      );
    }
    compareReports(zg, zg);
    const validated = [];
    for (const preview of ["short", "full"])
      for (const mode of modes)
        validated.push(
          resultRow(
            `ZG ${mode} / ${preview}`,
            zg.tasks.filter(
              (row) => row.preview === preview && row.mode === mode,
            ),
            zg,
          ),
        );
    rows.push(...validated);
    zgValid = true;
    if (!zg.integrity_passed)
      errors.push(`ZG: ${zg.product_error_calls} product calls failed`);
  } catch (error) {
    errors.push(`ZG: ${error.message}`);
    rows.push(...labels.map((label) => unavailableRow(label, "invalid")));
  }

  if (sembleRequested) {
    try {
      assert.ok(semble, "missing requested Semble report");
      const quality = await validateSembleReport(semble, suite);
      rows.push(resultRow("Semble MCP", quality, semble));
      sembleValid = true;
      if (!semble.integrity_passed)
        errors.push(
          `Semble: ${semble.product_error_calls} product calls failed`,
        );
    } catch (error) {
      errors.push(`Semble: ${error.message}`);
      rows.push(unavailableRow("Semble MCP", "invalid"));
    }
  } else {
    rows.push(unavailableRow("Semble MCP", "not_requested"));
  }

  let comparison = null;
  if (zgValid && sembleValid) {
    try {
      comparison = await compareSembleToZg(zg, semble, suite);
    } catch (error) {
      errors.push(`Cross-tool validation: ${error.message}`);
    }
  }
  const requiredJobs = [
    "authorize",
    "quality-contract",
    "package-candidate",
    "retrieval",
    "zg-report",
    ...(sembleRequested ? ["semble"] : []),
  ];
  if (Object.keys(jobResults).length)
    for (const job of requiredJobs)
      if (jobResults[job]?.result !== "success")
        errors.push(`${job}: ${jobResults[job]?.result ?? "missing job"}`);

  return {
    schema_version: 2,
    status: errors.length ? "failed" : "success",
    quality_metrics: QUALITY_METRICS,
    semble_requested: sembleRequested,
    run_url: runUrl,
    commit,
    rows,
    errors,
    comparison,
  };
}

const cell = (value) =>
  String(value).replaceAll("|", "\\|").replaceAll(/\r?\n/g, " ");
const number = (value, places = 4) =>
  Number.isFinite(value) ? value.toFixed(places) : "—";

export function markdownCiSummary(result) {
  const status =
    result.status === "success" ? "✅ Complete" : "❌ Failed / incomplete";
  const lines = [
    "# Retrieval-only results",
    "",
    `**${status}** · 20 original questions · 11 pinned repositories · ${result.semble_requested ? "ZG + Semble" : "ZG only"}`,
    "",
    "| Arm | Status | File Hit@1 | File Hit@5 | File Hit@10 | File MRR@10 | nDCG@10 | Mean output (KiB) | Latency P50 (ms) |",
    "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
  ];
  for (const row of result.rows) {
    const state = {
      success: "✅ Valid",
      product_error: "⚠️ Failed calls score zero",
      invalid: "❌ No valid report",
      not_requested: "Disabled",
    }[row.status];
    const hits = [1, 5, 10].map((cutoff) => {
      const value = row.metrics?.[`file_hit_at_${cutoff}`];
      return Number.isFinite(value)
        ? `${(value * 100).toFixed(1)}% (${Math.round(value * row.questions)}/${row.questions})`
        : "—";
    });
    lines.push(
      `| ${cell(row.label)} | ${state} | ${hits.join(" | ")} | ${number(row.metrics?.file_mrr_at_10)} | ${number(row.metrics?.ndcg_at_10)} | ${number(row.measurements?.output_bytes_mean == null ? null : row.measurements.output_bytes_mean / 1024, 2)} | ${number(row.measurements?.latency_ms_p50, 2)} |`,
    );
  }
  lines.push(
    "",
    "Quality uses the fifth call per question: Hit/MRR weight all 20 questions equally; nDCG@10 uses a repository macro average. All quality metrics use the same labeled relevant files. Finding a file does not establish sufficient answer evidence.",
    "",
    "Output is the mean UTF-8 byte count of successful fifth-call responses (1 KiB = 1024 bytes, not model tokens). Latency is the P50 of all successful MCP search calls, excluding indexing and SDK replay. Failed calls are excluded from these measurements. Different machines, indexes and call order make latency an observation of this run, not a controlled speed comparison.",
    "",
    ...result.rows
      .filter((row) => row.measurements)
      .map(
        (row) =>
          `- ${cell(row.label)}: ${row.measurements.output_sample_count} output samples; ${row.measurements.latency_sample_count} latency samples.`,
      ),
  );
  if (!result.semble_requested)
    lines.push(
      "",
      "Semble was not run. Enable `run_semble` when dispatching the workflow to include it. Disabled or missing results appear as —, not zero scores.",
    );
  if (result.errors.length)
    lines.push(
      "",
      "## Required action",
      "",
      ...result.errors.map((error) => `- ${cell(error)}`),
    );
  if (result.commit)
    lines.push("", `Tested commit: \`${cell(result.commit)}\`.`);
  lines.push(
    "",
    "Details: download the `retrieval-results` artifact for summary.json and, when both ZG and Semble validate, comparison.json. Per-question raw records are available in the evidence artifacts for each executed arm.",
  );
  if (result.run_url)
    lines.push("", `[Open this run and its artifacts](${result.run_url})`);
  return `${lines.join("\n")}\n`;
}

async function main() {
  const args = {};
  for (let i = 2; i < process.argv.length; i += 2) {
    const key = process.argv[i];
    assert.ok(
      ["--zg", "--semble", "--output"].includes(key) && process.argv[i + 1],
    );
    args[key.slice(2)] = process.argv[i + 1];
  }
  assert.ok(
    args.output && args.zg && args.semble,
    "requires --zg, --semble and --output",
  );
  const requested = process.env.RETRIEVAL_SEMBLE_REQUESTED ?? "false";
  assert.ok(["true", "false"].includes(requested), "invalid Semble flag");
  const maybeRead = async (path) => {
    try {
      return await readJson(path);
    } catch {
      return null;
    }
  };
  const runUrl = process.env.GITHUB_RUN_ID
    ? `${process.env.GITHUB_SERVER_URL}/${process.env.GITHUB_REPOSITORY}/actions/runs/${process.env.GITHUB_RUN_ID}`
    : null;
  const result = await buildCiSummary({
    zg: await maybeRead(args.zg),
    semble: requested === "true" ? await maybeRead(args.semble) : null,
    sembleRequested: requested === "true",
    modes: (process.env.RETRIEVAL_MODES ?? "hybrid").split(","),
    jobResults: JSON.parse(process.env.RETRIEVAL_JOB_RESULTS ?? "{}"),
    runUrl,
    commit: process.env.GITHUB_SHA ?? null,
  });
  const directory = resolve(args.output);
  await mkdir(directory, { recursive: true });
  const { comparison, ...overview } = result;
  await writeJson(join(directory, "summary.json"), overview);
  if (comparison)
    await writeJson(join(directory, "comparison.json"), comparison);
  await writeFile(join(directory, "summary.md"), markdownCiSummary(result));
  if (result.status !== "success") process.exitCode = 1;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href)
  main().catch((error) => {
    console.error(error);
    process.exitCode = 1;
  });
