import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import { join, resolve } from "node:path";
import { summarizeFileRetrieval } from "../metrics/files.mjs";
import { loadSuite, readJson, writeJson } from "../core/lib.mjs";
import { summarizeMeasurements } from "../metrics/measurements.mjs";
import { summarizeNdcg } from "../metrics/summary.mjs";
import { validateZgReport, ZG_MODES } from "./validation.mjs";

export const QUALITY_METRICS = Object.freeze([
  "file_hit_at_1",
  "file_hit_at_5",
  "file_hit_at_10",
  "file_mrr_at_10",
  "ndcg_at_10",
]);

function resultRow(label, rows) {
  const file = summarizeFileRetrieval(rows);
  const ndcg = summarizeNdcg(rows);
  assert.equal(file.scored_tasks, 20, `${label}: requires all 20 questions`);
  return {
    label,
    status: rows.some((row) =>
      row.measurement_observations.some(
        (sample) => sample.execution_status === "product_error",
      ),
    )
      ? "product_error"
      : "success",
    questions: file.scored_tasks,
    metrics: {
      file_hit_at_1: file.hit_at_1,
      file_hit_at_5: file.hit_at_5,
      file_hit_at_10: file.hit_at_10,
      file_mrr_at_10: file.mrr_at_10,
      ndcg_at_10: ndcg.repository_macro.ndcg_at_10,
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

/** Fixed ZG arms; invalid evidence never becomes a zero score. */
export async function buildCiSummary({
  zg = null,
  jobResults = {},
  runUrl = null,
  harnessCommit = null,
  candidateCommit = null,
  candidateRef = null,
} = {}) {
  const suite = await loadSuite();
  const rows = [],
    errors = [];
  try {
    assert.ok(zg, "missing ZG report");
    const checked = validateZgReport(zg, "ZG", { suite });
    assert.equal(
      zg.scope,
      "full-20-original-queries",
      "CI requires all 20 questions",
    );
    assert.deepEqual(
      checked.ids,
      suite.lock.tasks.map((task) => task.task_id).sort(),
      "CI task selection differs from frozen suite",
    );
    for (const mode of ZG_MODES)
      rows.push(
        resultRow(
          `zg-${mode}`,
          [...checked.rows.values()].filter((row) => row.mode === mode),
        ),
      );
    if (!zg.integrity_passed)
      errors.push(`ZG: ${zg.product_error_calls} product calls failed`);
  } catch (error) {
    errors.push(`ZG: ${error.message}`);
    rows.length = 0;
    rows.push(
      ...ZG_MODES.map((mode) => unavailableRow(`zg-${mode}`, "invalid")),
    );
  }
  const requiredJobs = [
    "authorize",
    "quality-contract",
    "package-candidate",
    "retrieval",
    "zg-report",
  ];
  if (Object.keys(jobResults).length)
    for (const job of requiredJobs)
      if (jobResults[job]?.result !== "success")
        errors.push(`${job}: ${jobResults[job]?.result ?? "missing job"}`);
  return {
    schema_version: 4,
    status: errors.length ? "failed" : "success",
    preview: "mcp-default",
    quality_metrics: QUALITY_METRICS,
    run_url: runUrl,
    harness_commit: harnessCommit,
    candidate: {
      ref: candidateRef,
      commit: candidateCommit,
    },
    rows,
    errors,
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
    `**${status}** · 20 original questions · 11 pinned repositories · ZG hybrid / fts / vector · Rust MCP default presentation`,
    "",
    "| Arm | Status | File Hit@1 | File Hit@5 | File Hit@10 | File MRR@10 | nDCG@10 | Mean output (KiB) | Latency P50 (ms) |",
    "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
  ];
  for (const row of result.rows) {
    const state = {
      success: "✅ Valid",
      product_error: "⚠️ Product-call failure",
      invalid: "❌ No valid report",
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
    "All three arms use the Rust public MCP default presentation and five calls per question. No preview override is sent. Each arm has 20 quality observations from the fifth call: Hit/MRR weight all 20 questions equally; nDCG@10 uses a repository macro average. All quality metrics use the same labeled relevant files. Finding a file does not establish sufficient answer evidence.",
    "",
    "Output is the mean UTF-8 byte count of successful fifth-call responses (1 KiB = 1024 bytes, not model tokens). Latency is the P50 of all successful MCP search calls, excluding indexing. Failed calls are excluded from these measurements. A complete successful arm has 20 output samples and 100 latency samples. Index loading and fixed mode order affect latency; these are observations of this run, not a controlled speed comparison.",
    "",
    ...result.rows
      .filter((row) => row.measurements)
      .map(
        (row) =>
          `- ${cell(row.label)}: ${row.measurements.output_sample_count} output samples; ${row.measurements.latency_sample_count} latency samples.`,
      ),
  );
  if (result.errors.length)
    lines.push(
      "",
      "## Required action",
      "",
      ...result.errors.map((error) => `- ${cell(error)}`),
    );
  if (result.candidate?.commit)
    lines.push(
      "",
      `Rust candidate: \`${cell(result.candidate.ref)}\` at \`${cell(result.candidate.commit)}\`.`,
    );
  if (result.harness_commit)
    lines.push(
      "",
      `Benchmark harness commit: \`${cell(result.harness_commit)}\`.`,
    );
  lines.push(
    "",
    "Details: download the `retrieval-results` artifact for summary.md and summary.json. Per-question raw records are available in the repository evidence artifacts.",
  );
  if (result.run_url)
    lines.push("", `[Open this run and its artifacts](${result.run_url})`);
  return `${lines.join("\n")}\n`;
}

export async function main() {
  const args = {};
  for (let i = 2; i < process.argv.length; i += 2) {
    const key = process.argv[i];
    assert.ok(
      ["--zg", "--output"].includes(key) &&
        process.argv[i + 1] &&
        !Object.hasOwn(args, key.slice(2)),
    );
    args[key.slice(2)] = process.argv[i + 1];
  }
  assert.ok(args.output && args.zg, "requires --zg and --output");
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
    jobResults: JSON.parse(process.env.RETRIEVAL_JOB_RESULTS ?? "{}"),
    runUrl,
    harnessCommit: process.env.RETRIEVAL_HARNESS_COMMIT ?? null,
    candidateCommit: process.env.RETRIEVAL_CANDIDATE_COMMIT ?? null,
    candidateRef: process.env.RETRIEVAL_CANDIDATE_REF ?? null,
  });
  const directory = resolve(args.output);
  await mkdir(directory, { recursive: true });
  await writeJson(join(directory, "summary.json"), result);
  await writeFile(join(directory, "summary.md"), markdownCiSummary(result));
  if (result.status !== "success") process.exitCode = 1;
}
