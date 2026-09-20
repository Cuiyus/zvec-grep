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
  "semble_ndcg_at_10",
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
      semble_ndcg_at_10: official.repository_macro.ndcg_at_10,
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
    schema_version: 1,
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
    result.status === "success" ? "✅ 完成" : "❌ 失败 / 结果不完整";
  const lines = [
    "# Retrieval-only 测试结果",
    "",
    `**${status}** · 20 道原始问题 · 11 个固定仓库版本 · ${result.semble_requested ? "ZG + Semble" : "仅 ZG"}`,
    "",
    "| 测试组 | 状态 | 文件 Hit@1 | 文件 Hit@5 | 文件 Hit@10 | 文件 MRR@10 | Semble nDCG@10 | 平均输出 (KiB) | 延迟 P50 (ms) |",
    "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
  ];
  for (const row of result.rows) {
    const state = {
      success: "✅ 有效",
      product_error: "⚠️ 调用失败计零",
      invalid: "❌ 无有效报告",
      not_requested: "未启用",
    }[row.status];
    const hits = [1, 5, 10].map((cutoff) => {
      const value = row.metrics?.[`file_hit_at_${cutoff}`];
      return Number.isFinite(value)
        ? `${(value * 100).toFixed(1)}% (${Math.round(value * row.questions)}/${row.questions})`
        : "—";
    });
    lines.push(
      `| ${cell(row.label)} | ${state} | ${hits.join(" | ")} | ${number(row.metrics?.file_mrr_at_10)} | ${number(row.metrics?.semble_ndcg_at_10)} | ${number(row.measurements?.output_bytes_mean == null ? null : row.measurements.output_bytes_mean / 1024, 2)} | ${number(row.measurements?.latency_ms_p50, 2)} |`,
    );
  }
  lines.push(
    "",
    "质量取每题第 5 次调用：Hit/MRR 按 20 题等权，Semble nDCG@10 按仓库宏平均。两者使用同一份已标注相关文件；命中文件不代表回答证据充分。",
    "",
    "输出为正式计分轮次成功响应的 UTF-8 字节均值（1 KiB = 1024 字节，不是模型 token）；延迟为全部成功 MCP 搜索调用的 P50，不含建索引。失败调用不进入测量样本；机器、索引和调用顺序不同，延迟仅作本次运行观测。",
    "",
    ...result.rows
      .filter((row) => row.measurements)
      .map(
        (row) =>
          `- ${cell(row.label)}：输出 ${row.measurements.output_sample_count} 个样本；延迟 ${row.measurements.latency_sample_count} 个样本。`,
      ),
  );
  if (!result.semble_requested)
    lines.push(
      "",
      "Semble 未运行；手动触发时勾选 `run_semble` 可加入对照。未启用或缺失结果用 — 表示，不记为零分。",
    );
  if (result.errors.length)
    lines.push(
      "",
      "## 需要处理",
      "",
      ...result.errors.map((error) => `- ${cell(error)}`),
    );
  if (result.commit) lines.push("", `测试提交：\`${cell(result.commit)}\`。`);
  lines.push(
    "",
    "详细结果：下载本次运行的 `retrieval-results` artifact（summary.json；ZG 与 Semble 均通过校验时另含 comparison.json）；逐题原始记录见已运行测试组的 evidence artifacts。",
  );
  if (result.run_url)
    lines.push("", `[打开本次运行及 artifacts](${result.run_url})`);
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
