import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { parseArgs } from "node:util";
import { readJson, writeJson } from "../core/io.mjs";
import { buildCiSummary, markdownCiSummary } from "../reports/ci.mjs";
import { scoreFileRetrieval } from "../metrics/files.mjs";
import { scoreNdcg } from "../metrics/ndcg.mjs";
import { loadPilot } from "./datasets.mjs";
import { markdownPilotReport, summarizePilotRows } from "./run.mjs";

async function checkPilot(name, report, candidateCommit) {
  const pilot = await loadPilot(name);
  if (!report)
    return {
      status: "unavailable",
      report: null,
      error: "report artifact missing",
    };
  try {
    assert.equal(report.schema_version, 1);
    assert.equal(report.suite, pilot.lock.suite);
    assert.equal(report.model, pilot.lock.model);
    assert.equal(report.candidate_commit, candidateCommit);
    assert.ok(Array.isArray(report.rows) && Array.isArray(report.failures));
    assert.ok(Array.isArray(report.summary) && report.summary.length === 3);
    const expected = new Set(
      pilot.lock.tasks.flatMap((task) =>
        pilot.modes.map((mode) => `${task.id}:${mode}`),
      ),
    );
    const actual = report.rows.map((row) => `${row.task_id}:${row.mode}`);
    assert.equal(
      new Set(actual).size,
      actual.length,
      "duplicate query/mode results",
    );
    assert.ok(
      actual.every((key) => expected.has(key)),
      "unknown query/mode result",
    );
    for (const row of report.rows) {
      const task = pilot.lock.tasks.find((item) => item.id === row.task_id);
      assert.equal(row.query, task.query, `rewritten query ${row.task_id}`);
      const paths =
        name === "beir"
          ? task.qrels.map((item) => `docs/${item.document_id}.md`)
          : [...new Set(task.positive_units.map((unit) => unit.path))];
      assert.deepEqual(
        row.targets,
        paths.map((path) => ({ path })),
        `changed gold ${row.task_id}`,
      );
      assert.equal(row.calls.length, 5);
      assert.deepEqual(
        row.calls.map((call) => call.repetition),
        [1, 2, 3, 4, 5],
      );
      for (const call of row.calls) {
        assert.ok(["success", "failed"].includes(call.status));
        assert.ok(Number.isFinite(call.latency_ms) && call.latency_ms >= 0);
      }
      if (row.status === "success") {
        assert.equal(row.calls[4].status, "success");
        assert.ok(
          Number.isSafeInteger(row.output_bytes) && row.output_bytes >= 0,
        );
        assert.deepEqual(row.file, scoreFileRetrieval(row.items, row.targets));
        assert.deepEqual(row.ndcg, scoreNdcg(row.items, row.targets));
      } else assert.equal(row.status, "failed");
    }
    assert.deepEqual(
      report.summary,
      summarizePilotRows(report.rows),
      "cached pilot summary differs from rows",
    );
    assert.ok(["success", "failed"].includes(report.status));
    if (report.status === "success") {
      assert.equal(actual.length, expected.size, "incomplete success report");
      assert.equal(report.failures.length, 0);
      assert.ok(report.summary.every((row) => row.completed === 10));
    }
    return { status: report.status, report, error: null };
  } catch (error) {
    return { status: "invalid", report: null, error: error.message };
  }
}

export async function buildCombined({
  zg,
  beir,
  quarry,
  jobResults = {},
  candidateCommit = null,
  candidateRef = null,
  harnessCommit = null,
  runUrl = null,
}) {
  const sweqa = await buildCiSummary({
    zg,
    jobResults,
    candidateCommit,
    candidateRef,
    harnessCommit,
    runUrl,
  });
  const pilots = {
    beir: await checkPilot("beir", beir, candidateCommit),
    quarry: await checkPilot("quarry", quarry, candidateCommit),
  };
  const errors = [...sweqa.errors];
  for (const name of ["beir", "quarry"]) {
    const result = pilots[name];
    if (result.status !== "success")
      errors.push(
        `${name}: ${result.error ?? result.report?.failures.length + " failed task/call records"}`,
      );
    if (
      Object.keys(jobResults).length &&
      jobResults[name]?.result !== "success"
    )
      errors.push(`${name} job: ${jobResults[name]?.result ?? "missing"}`);
  }
  return {
    schema_version: 1,
    status: errors.length ? "failed" : "success",
    candidate: { ref: candidateRef, commit: candidateCommit },
    harness_commit: harnessCommit,
    run_url: runUrl,
    sweqa,
    pilots,
    errors,
  };
}

export function markdownCombined(result) {
  const labels = { beir: "BEIR / SciFact", quarry: "Quarry / quic-go" };
  const sweqaCompleted = Math.min(
    ...result.sweqa.rows.map((row) => row.questions ?? 0),
  );
  const lines = [
    "# Retrieval-only results",
    "",
    `**${result.status === "success" ? "✅ Complete" : "❌ Failed / incomplete"}** · Rust candidate \`${result.candidate.ref ?? "?"}\` at \`${result.candidate.commit ?? "?"}\``,
    "",
    "| Suite | Status | Queries | Model |",
    "| --- | --- | ---: | --- |",
    `| SWE-QA20 | ${result.sweqa.status === "success" ? "✅ Complete" : "❌ Incomplete"} | ${sweqaCompleted}/20 | \`local/potion-code-16m-v2\` |`,
  ];
  for (const name of ["beir", "quarry"]) {
    const pilot = result.pilots[name];
    const complete = pilot.report?.summary?.[0]?.completed ?? 0;
    lines.push(
      `| ${labels[name]} | ${pilot.status === "success" ? "✅ Complete" : "❌ Incomplete"} | ${complete}/10 | \`${name === "beir" ? "local/potion-multilingual-128m" : "local/potion-code-16m-v2"}\` |`,
    );
  }
  lines.push(
    "",
    markdownCiSummary(result.sweqa).replace(
      /^# Retrieval-only results/,
      "## SWE-QA20",
    ),
  );
  for (const name of ["beir", "quarry"]) {
    const pilot = result.pilots[name];
    lines.push(
      pilot.report
        ? markdownPilotReport(pilot.report)
        : `## ${labels[name]} · ❌ ${pilot.status}\n\n${pilot.error}.\n`,
    );
  }
  if (result.errors.length) {
    lines.push(
      "## Incomplete jobs and reports",
      "",
      ...result.errors.map((error) => `- ${error.replaceAll(/\r?\n/g, " ")}`),
      "",
    );
  }
  if (result.run_url)
    lines.push(`[Open this run and artifacts](${result.run_url})`, "");
  return lines.join("\n");
}

export async function main(args = process.argv.slice(2)) {
  const { values } = parseArgs({
    args,
    options: {
      zg: { type: "string" },
      beir: { type: "string" },
      quarry: { type: "string" },
      output: { type: "string" },
    },
  });
  assert.ok(values.zg && values.beir && values.quarry && values.output);
  const maybeRead = async (path) => {
    try {
      return await readJson(path);
    } catch {
      return null;
    }
  };
  const result = await buildCombined({
    zg: await maybeRead(values.zg),
    beir: await maybeRead(values.beir),
    quarry: await maybeRead(values.quarry),
    jobResults: JSON.parse(process.env.RETRIEVAL_JOB_RESULTS ?? "{}"),
    candidateCommit: process.env.RETRIEVAL_CANDIDATE_COMMIT ?? null,
    candidateRef: process.env.RETRIEVAL_CANDIDATE_REF ?? null,
    harnessCommit: process.env.RETRIEVAL_HARNESS_COMMIT ?? null,
    runUrl: process.env.GITHUB_RUN_ID
      ? `${process.env.GITHUB_SERVER_URL}/${process.env.GITHUB_REPOSITORY}/actions/runs/${process.env.GITHUB_RUN_ID}`
      : null,
  });
  const output = resolve(values.output);
  await mkdir(output, { recursive: true });
  await writeJson(join(output, "summary.json"), result);
  await writeFile(join(output, "summary.md"), markdownCombined(result));
  if (result.status !== "success") process.exitCode = 1;
}

if (
  process.argv[1] &&
  resolve(process.argv[1]) === fileURLToPath(import.meta.url)
)
  await main();
