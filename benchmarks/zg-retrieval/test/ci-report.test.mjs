import assert from "node:assert/strict";
import { mkdtemp, readFile, readdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";
import test from "node:test";
import {
  buildCiSummary,
  markdownCiSummary,
  QUALITY_METRICS,
} from "../reports/ci.mjs";
import {
  FILE_RETRIEVAL_CONTRACT,
  fileRetrievalForRow,
  summarizeFileRetrieval,
} from "../metrics/files.mjs";
import { loadSuite } from "../core/lib.mjs";
import { summarizeMeasurements } from "../metrics/measurements.mjs";
import { summarizeNdcg } from "../metrics/summary.mjs";
import { scoreNdcg } from "../metrics/ndcg.mjs";
import { validateZgReport, ZG_MODES } from "../reports/validation.mjs";

const suite = await loadSuite();
const clone = (value) => structuredClone(value);
function qualityRow(task, mode, { failed = false, firstRank = 3 } = {}) {
  const targets = suite.file_gold[task.task_id].targets;
  const items = failed
    ? []
    : Array.from({ length: 10 }, (_, index) => ({
        rank: index + 1,
        path:
          index + 1 === firstRank
            ? targets[0].path
            : `__unrelated__/entry_${index + 1}.none`,
        range: { kind: "text", start_line: 100, end_line: 120 },
        matched_by: mode === "hybrid" ? "fts+vector" : mode,
      }));
  const row = {
    task_id: task.task_id,
    mode,
    preview: "mcp-default",
    repetition: 5,
    quality_observation: true,
    repository: task.repository,
    category: task.category,
    language: suite.file_gold[task.task_id].language,
    gold_status: suite.gold[task.task_id].status,
    status: failed ? "product_error" : "scored",
    execution_status: failed ? "product_error" : "success",
    latency_ms: failed ? null : 12.34567,
    visible_output_bytes: failed ? null : 3584,
    items,
    ndcg: { targets, ...scoreNdcg(items, targets) },
  };
  row.file_retrieval = fileRetrievalForRow(row);
  row.measurement_observations = Array.from({ length: 5 }, (_, index) => ({
    repetition: index + 1,
    status: row.status,
    execution_status: row.execution_status,
    latency_ms: row.latency_ms,
    visible_output_bytes: row.visible_output_bytes,
  }));
  return row;
}
function modeReport(rows) {
  return {
    file_retrieval: summarizeFileRetrieval(rows),
    ndcg: summarizeNdcg(rows),
    measurements: summarizeMeasurements(
      rows.flatMap((row) => row.measurement_observations),
    ),
  };
}
function zgReport({ failedModes = [], rankForTask = () => 3 } = {}) {
  const tasks = ZG_MODES.flatMap((mode) =>
    suite.lock.tasks.map((task) =>
      qualityRow(task, mode, {
        failed: failedModes.includes(mode),
        firstRank: rankForTask(task, mode),
      }),
    ),
  );
  return {
    schema_version: 6,
    file_retrieval_contract: FILE_RETRIEVAL_CONTRACT,
    preview: "mcp-default",
    quality_repetition: 5,
    quality_score_valid: true,
    integrity_passed: failedModes.length === 0,
    integrity_errors: [],
    product_error_calls: failedModes.length * 100,
    suite: clone(suite.identity),
    scope: "full-20-original-queries",
    expected_task_ids: suite.lock.tasks.map((task) => task.task_id),
    observed_calls: 300,
    modes: Object.fromEntries(
      ZG_MODES.map((mode) => [
        mode,
        modeReport(tasks.filter((row) => row.mode === mode)),
      ]),
    ),
    tasks,
    repositories: suite.lock.repositories.map((repository) => ({
      repository: repository.repository,
    })),
  };
}
function assertUnavailable(result) {
  assert.equal(result.status, "failed");
  assert.deepEqual(
    result.rows.map((row) => row.label),
    ["zg-hybrid", "zg-fts", "zg-vector"],
  );
  for (const row of result.rows) {
    assert.equal(row.status, "invalid");
    assert.equal(row.questions, null);
    assert.equal(row.metrics, null);
    assert.equal(row.measurements, null);
  }
}

test("summary schema 4 has exactly the fixed three Rust MCP ZG arms", async () => {
  const result = await buildCiSummary({ zg: zgReport() });
  assert.equal(result.schema_version, 4);
  assert.equal(result.status, "success");
  assert.equal(result.preview, "mcp-default");
  assert.deepEqual(result.quality_metrics, [
    "file_hit_at_1",
    "file_hit_at_5",
    "file_hit_at_10",
    "file_mrr_at_10",
    "ndcg_at_10",
  ]);
  assert.deepEqual(
    result.rows.map((row) => row.label),
    ["zg-hybrid", "zg-fts", "zg-vector"],
  );
  for (const row of result.rows) {
    assert.equal(row.questions, 20);
    assert.ok(Math.abs(row.metrics.file_mrr_at_10 - 1 / 3) < 1e-14);
    assert.equal(row.measurements.output_sample_count, 20);
    assert.equal(row.measurements.latency_sample_count, 100);
  }
  assert.deepEqual(result.quality_metrics, QUALITY_METRICS);
  assert.doesNotMatch(
    JSON.stringify(result),
    /comparison|previews|primary_preview/,
  );
});

test("missing and invalid reports withhold all three arms instead of fabricating zeros", async () => {
  assertUnavailable(await buildCiSummary());
  const report = zgReport();
  report.quality_score_valid = false;
  assertUnavailable(await buildCiSummary({ zg: report }));
});

test("CI rejects old contracts and incomplete or misrouted three-mode matrices", async () => {
  for (const mutate of [
    (r) => {
      r.schema_version = 5;
    },
    (r) => {
      r.preview = "short";
    },
    (r) => {
      r.previews = {};
    },
    (r) => {
      r.primary_preview = "full";
    },
    (r) => {
      r.tasks[0].preview = "short";
    },
    (r) => {
      r.tasks.pop();
    },
    (r) => {
      r.tasks[1] = clone(r.tasks[0]);
    },
    (r) => {
      delete r.modes.vector;
    },
    (r) => {
      r.modes.extra = {};
    },
    (r) => {
      r.observed_calls = 299;
    },
    (r) => {
      r.tasks.find((row) => row.mode === "vector").items[0].matched_by =
        "fts+vector";
    },
    (r) => {
      r.tasks.find((row) => row.mode === "fts").items[0].matched_by = "vector";
    },
  ]) {
    const report = zgReport();
    mutate(report);
    assertUnavailable(await buildCiSummary({ zg: report }));
  }
});

test("per-question scores and frozen label identities cannot be changed", async () => {
  for (const mutate of [
    (r) => {
      r.tasks[0].file_retrieval.mrr_at_10 = 999;
    },
    (r) => {
      r.tasks[0].ndcg.ndcg_at_10 = 999;
    },
    (r) => {
      r.tasks[0].ndcg.targets = [{ path: "changed.py" }];
    },
    (r) => {
      r.tasks[0].repository = "forged/repository";
    },
    (r) => {
      r.tasks[0].language = "forged-language";
    },
    (r) => {
      r.tasks[0].category = "invalid";
    },
    (r) => {
      r.tasks[0].gold_status = "unknown";
    },
    (r) => {
      r.suite.protocol = "f".repeat(64);
    },
  ]) {
    const report = zgReport();
    mutate(report);
    assertUnavailable(await buildCiSummary({ zg: report }));
  }
});

test("cached aggregates never override public items or per-call measurements", async () => {
  const report = zgReport(),
    snapshot = clone(report);
  for (const mode of ZG_MODES)
    report.modes[mode] = {
      file_retrieval: { mrr_at_10: 999 },
      ndcg: { repository_macro: { ndcg_at_10: 999 } },
      measurements: { latency_ms_p50: 999 },
    };
  const result = await buildCiSummary({ zg: report });
  assert.equal(result.status, "success");
  for (const [index, mode] of ZG_MODES.entries()) {
    assert.equal(
      result.rows[index].metrics.file_mrr_at_10,
      snapshot.modes[mode].file_retrieval.mrr_at_10,
    );
    assert.equal(
      result.rows[index].metrics.ndcg_at_10,
      snapshot.modes[mode].ndcg.repository_macro.ndcg_at_10,
    );
    assert.equal(result.rows[index].measurements.latency_ms_p50, 12.34567);
  }
});

test("measurement evidence must cover all five repetitions with valid successful calls", async () => {
  for (const mutate of [
    (r) => {
      r.tasks[0].measurement_observations.pop();
    },
    (r) => {
      r.tasks[0].measurement_observations[0].repetition = 5;
    },
    ...[null, -1, Number.NaN].map((value) => (r) => {
      r.tasks[0].measurement_observations[0].latency_ms = value;
    }),
    (r) => {
      r.tasks[0].measurement_observations[0].visible_output_bytes = -1;
    },
    (r) => {
      r.tasks[0].measurement_observations[4].latency_ms = 999;
    },
  ]) {
    const report = zgReport();
    mutate(report);
    assert.throws(
      () => validateZgReport(report, "ZG", { suite }),
      /measurement|successful-call/,
    );
    assertUnavailable(await buildCiSummary({ zg: report }));
  }
});

test("product failures retain quality zeros only in the affected arm and exclude error text from measurements", async () => {
  const report = zgReport({ failedModes: ["vector"] });
  for (const row of report.tasks.filter((row) => row.mode === "vector")) {
    row.visible_output_bytes = 37;
    for (const sample of row.measurement_observations)
      sample.visible_output_bytes = 37;
  }
  const result = await buildCiSummary({ zg: report });
  assert.equal(result.status, "failed");
  assert.deepEqual(
    result.rows.map((row) => row.status),
    ["success", "success", "product_error"],
  );
  assert.equal(result.rows[2].questions, 20);
  for (const metric of QUALITY_METRICS)
    assert.equal(result.rows[2].metrics[metric], 0);
  assert.equal(result.rows[2].measurements.output_bytes_mean, null);
  assert.equal(result.rows[2].measurements.output_sample_count, 0);
  assert.equal(result.rows[2].measurements.latency_sample_count, 0);
  report.product_error_calls = 99;
  assertUnavailable(await buildCiSummary({ zg: report }));
});

test("an earlier repetition failure does not replace the successful fifth-call quality score", async () => {
  const report = zgReport(),
    first = report.tasks[0].measurement_observations[0];
  Object.assign(first, {
    execution_status: "product_error",
    status: "product_error",
    latency_ms: null,
    visible_output_bytes: 50,
  });
  report.product_error_calls = 1;
  report.integrity_passed = false;
  const result = await buildCiSummary({ zg: report });
  assert.equal(result.status, "failed");
  assert.equal(result.rows[0].status, "product_error");
  assert.equal(result.rows[0].metrics.file_hit_at_5, 1);
  assert.equal(result.rows[0].measurements.output_sample_count, 20);
  assert.equal(result.rows[0].measurements.latency_sample_count, 99);
});

test("required job failures fail the summary without changing valid measured values", async () => {
  const jobs = Object.fromEntries(
    [
      "authorize",
      "quality-contract",
      "package-candidate",
      "retrieval",
      "zg-report",
    ].map((name) => [name, { result: "success" }]),
  );
  assert.equal(
    (await buildCiSummary({ zg: zgReport(), jobResults: jobs })).status,
    "success",
  );
  for (const name of Object.keys(jobs)) {
    const changed = clone(jobs);
    changed[name].result = "failure";
    const result = await buildCiSummary({
      zg: zgReport(),
      jobResults: changed,
    });
    assert.equal(result.status, "failed");
    assert.equal(result.rows[0].metrics.file_hit_at_5, 1);
    assert.ok(result.errors.some((error) => error.includes(name)));
  }
});

test("fractional results are stable across task ordering and do not mutate evidence", async () => {
  const ranks = [1, 3, 7, 10, null, 6, 9, 5, 2, 8];
  const report = zgReport({
    rankForTask: (task) => ranks[suite.lock.tasks.indexOf(task) % ranks.length],
  });
  const snapshot = clone(report),
    before = await buildCiSummary({ zg: report });
  report.tasks.reverse();
  report.expected_task_ids.reverse();
  const after = await buildCiSummary({ zg: report });
  assert.equal(before.status, "success");
  assert.deepEqual(after.rows, before.rows);
  report.tasks.reverse();
  report.expected_task_ids.reverse();
  assert.deepEqual(report, snapshot);
});

test("Markdown exposes exactly three arms, five quality metrics and two measurements", async () => {
  const result = await buildCiSummary({
    zg: zgReport(),
    harnessCommit: "a".repeat(40),
    candidateCommit: "b".repeat(40),
    candidateRef: "feature/rust-search",
  });
  const text = markdownCiSummary(result);
  assert.match(text, /Rust public MCP default presentation/);
  assert.match(text, /20 quality observations/);
  assert.match(text, /Hit\/MRR weight all 20 questions equally/);
  assert.match(text, /nDCG@10 uses a repository macro average/);
  assert.match(text, /1 KiB = 1024 bytes/);
  assert.match(text, /20 output samples; 100 latency samples/);
  assert.match(text, /feature\/rust-search/);
  assert.match(text, new RegExp("a{40}"));
  assert.match(text, new RegExp("b{40}"));
  for (const label of ["zg-hybrid", "zg-fts", "zg-vector"])
    assert.match(
      text,
      new RegExp(
        `\\| ${label} \\| ✅ Valid \\| 0.0% \\(0/20\\) \\| 100.0% \\(20/20\\) \\| 100.0% \\(20/20\\) \\| 0.3333`,
      ),
    );
  assert.match(text, /\| 3\.50 \| 12\.35 \|/);
  assert.equal(
    text.split("\n").filter((line) => line.startsWith("| zg-")).length,
    3,
  );
  assert.doesNotMatch(
    text,
    /SDK|Disabled|comparison\.json|short|nDCG@5|anchor/,
  );
});

test("CLI accepts only --zg and --output and writes only the two overview artifacts", async (t) => {
  const directory = await mkdtemp(join(tmpdir(), "zg-ci-summary-"));
  t.after(() => rm(directory, { recursive: true, force: true }));
  const input = join(directory, "report.json"),
    output = join(directory, "summary");
  await writeFile(input, JSON.stringify(zgReport()));
  const script = fileURLToPath(new URL("../ci-report.mjs", import.meta.url));
  const env = { ...process.env, RETRIEVAL_JOB_RESULTS: "{}" };
  const result = spawnSync(
    process.execPath,
    [script, "--zg", input, "--output", output],
    { encoding: "utf8", env },
  );
  assert.equal(result.status, 0, result.stderr);
  assert.deepEqual((await readdir(output)).sort(), [
    "summary.json",
    "summary.md",
  ]);
  assert.equal(
    JSON.parse(await readFile(join(output, "summary.json"), "utf8"))
      .schema_version,
    4,
  );
  for (const unsupported of ["--engine", "--modes"])
    assert.notEqual(
      spawnSync(
        process.execPath,
        [script, "--zg", input, "--output", output, unsupported, "ignored"],
        { encoding: "utf8", env },
      ).status,
      0,
    );
});
