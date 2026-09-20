import assert from "node:assert/strict";
import test from "node:test";
import {
  buildCiSummary,
  markdownCiSummary,
  QUALITY_METRICS,
} from "../ci-report.mjs";
import {
  FILE_RETRIEVAL_CONTRACT,
  fileRetrievalForRow,
  summarizeFileRetrieval,
} from "../file-retrieval-metrics.mjs";
import { loadSuite, objectHash } from "../lib.mjs";
import { summarizeMeasurements } from "../measurement-metrics.mjs";
import { summarizeSembleOfficial } from "../report.mjs";
import { scoreSembleMetric } from "../semble-metrics.mjs";
import { SEMBLE_PROTOCOL } from "../semble-report.mjs";

const suite = await loadSuite();
const clone = (value) => structuredClone(value);
function qualityRow(
  task,
  { mode = "hybrid", preview, failed = false, firstRank = 3 } = {},
) {
  const targets = suite.semble_gold[task.task_id].targets;
  const items = failed
    ? []
    : Array.from({ length: 10 }, (_, index) => ({
        rank: index + 1,
        path:
          index + 1 === firstRank
            ? targets[0].path
            : `__unrelated__/entry_${index + 1}.none`,
        range: { kind: "text", start_line: 100, end_line: 120 },
      }));
  const row = {
    task_id: task.task_id,
    mode,
    ...(preview ? { preview } : {}),
    repetition: 5,
    quality_observation: true,
    repository: task.repository,
    category: task.category,
    language: suite.semble_gold[task.task_id].language,
    gold_status: suite.gold[task.task_id].status,
    status: failed ? "product_error" : "scored",
    execution_status: failed ? "product_error" : "success",
    latency_ms: failed ? null : 12.34567,
    visible_output_bytes: failed
      ? null
      : preview === "short"
        ? 1024
        : preview === "full"
          ? 3584
          : 10240,
    items,
    semble_official: { targets, ...scoreSembleMetric(items, targets) },
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
    semble_official: summarizeSembleOfficial(rows),
    measurements: summarizeMeasurements(
      rows.flatMap((row) => row.measurement_observations),
    ),
  };
}
function zgReport({
  modes = ["hybrid"],
  failed = false,
  rankForTask = () => 3,
} = {}) {
  const tasks = ["short", "full"].flatMap((preview) =>
    modes.flatMap((mode) =>
      suite.lock.tasks.map((task) =>
        qualityRow(task, {
          preview,
          mode,
          failed,
          firstRank: rankForTask(task),
        }),
      ),
    ),
  );
  const previews = Object.fromEntries(
    ["short", "full"].map((preview) => {
      const rows = tasks.filter((row) => row.preview === preview);
      return [
        preview,
        {
          modes: Object.fromEntries(
            modes.map((mode) => [
              mode,
              modeReport(rows.filter((row) => row.mode === mode)),
            ]),
          ),
          tasks: rows,
        },
      ];
    }),
  );
  return {
    schema_version: 3,
    file_retrieval_contract: FILE_RETRIEVAL_CONTRACT,
    primary_preview: "short",
    quality_repetition: 5,
    quality_score_valid: true,
    integrity_passed: !failed,
    integrity_errors: [],
    product_error_calls: failed ? 200 * modes.length : 0,
    suite: clone(suite.identity),
    scope: "full-20-original-queries",
    expected_task_ids: suite.lock.tasks.map((task) => task.task_id),
    observed_calls: 200 * modes.length,
    modes: previews.short.modes,
    previews,
    tasks,
    repositories: suite.lock.repositories.map((repository) => ({
      repository: repository.repository,
      environment: { platform: "fixture" },
    })),
  };
}
function sembleReport({ failed = false, rankForTask = () => 4 } = {}) {
  const tasks = suite.lock.tasks.map((task) =>
    qualityRow(task, { failed, firstRank: rankForTask(task) }),
  );
  return {
    schema_version: 2,
    file_retrieval_contract: FILE_RETRIEVAL_CONTRACT,
    engine: "semble",
    protocol: clone(SEMBLE_PROTOCOL),
    quality_repetition: 5,
    quality_score_valid: true,
    integrity_passed: !failed,
    integrity_errors: [],
    product_error_calls: failed ? 100 : 0,
    scope: "full-20-original-queries",
    expected_task_ids: suite.lock.tasks.map((task) => task.task_id),
    observed_calls: 100,
    suite: { ...suite.identity, protocol: objectHash(SEMBLE_PROTOCOL) },
    modes: { hybrid: modeReport(tasks) },
    tasks,
    environment: { platform: "fixture" },
    repositories: [],
  };
}
const successes = (includeSemble = false) =>
  Object.fromEntries(
    [
      "authorize",
      "quality-contract",
      "package-candidate",
      "retrieval",
      "zg-report",
      ...(includeSemble ? ["semble"] : []),
    ].map((job) => [job, { result: "success" }]),
  );

function assertUnavailable(row, status) {
  assert.equal(row.status, status);
  assert.equal(row.metrics, null);
  assert.equal(row.measurements, null);
  assert.equal(row.questions, null);
}

test("default summary scores both ZG previews and never inspects unrequested Semble", async () => {
  let propertyReads = 0;
  const trap = new Proxy(
    {},
    {
      get() {
        propertyReads++;
        throw new Error("unrequested Semble must not be read");
      },
    },
  );
  const result = await buildCiSummary({
    zg: zgReport(),
    semble: trap,
    jobResults: successes(),
  });
  assert.equal(propertyReads, 0);
  assert.equal(result.status, "success");
  assert.equal(result.semble_requested, false);
  assert.deepEqual(
    result.rows.map((row) => row.label),
    ["ZG hybrid / short", "ZG hybrid / full", "Semble MCP"],
  );
  assertUnavailable(result.rows[2], "not_requested");
  assert.equal(result.comparison, null);
  for (const row of result.rows.slice(0, 2)) {
    assert.equal(row.status, "success");
    assert.deepEqual(Object.keys(row.metrics), QUALITY_METRICS);
    assert.equal(row.questions, 20);
    assert.equal(row.metrics.file_hit_at_1, 0);
    assert.equal(row.metrics.file_hit_at_5, 1);
    assert.equal(row.metrics.file_hit_at_10, 1);
    assert.ok(Math.abs(row.metrics.file_mrr_at_10 - 1 / 3) < 1e-15);
    assert.equal(row.measurements.latency_sample_count, 100);
    assert.equal(row.measurements.output_sample_count, 20);
  }
});

test("requested valid Semble joins one validated cross-tool summary", async () => {
  const result = await buildCiSummary({
    zg: zgReport(),
    semble: sembleReport(),
    sembleRequested: true,
    jobResults: successes(true),
  });
  assert.equal(result.status, "success");
  assert.equal(result.rows.length, 3);
  assert.equal(result.rows[2].status, "success");
  assert.equal(result.rows[2].metrics.file_mrr_at_10, 0.25);
  assert.deepEqual(Object.keys(result.comparison.zg_previews), [
    "short",
    "full",
  ]);
  assert.equal(result.comparison.zg_previews.full.tasks.length, 20);
  assert.equal(result.comparison.file_retrieval.zg.scored_tasks, 20);
});

test("requested missing Semble fails clearly instead of fabricating zero scores", async () => {
  const result = await buildCiSummary({
    zg: zgReport(),
    sembleRequested: true,
    jobResults: successes(true),
  });
  assert.equal(result.status, "failed");
  assert.equal(result.rows[0].status, "success");
  assertUnavailable(result.rows[2], "invalid");
  assert.match(result.errors.join("\n"), /missing requested Semble report/);
  assert.equal(result.comparison, null);
  assert.match(
    markdownCiSummary(result),
    /Semble MCP \| ❌ 无有效报告 \| — \| — \| — \| — \| — \| — \| —/,
  );
});

test("missing or invalid ZG withholds both previews but does not erase valid requested Semble", async () => {
  const missing = await buildCiSummary();
  assert.equal(missing.status, "failed");
  assertUnavailable(missing.rows[0], "invalid");
  assertUnavailable(missing.rows[1], "invalid");
  assertUnavailable(missing.rows[2], "not_requested");
  const broken = zgReport();
  broken.integrity_errors.push("raw capture changed");
  const result = await buildCiSummary({
    zg: broken,
    semble: sembleReport(),
    sembleRequested: true,
  });
  assert.equal(result.status, "failed");
  assertUnavailable(result.rows[0], "invalid");
  assertUnavailable(result.rows[1], "invalid");
  assert.equal(result.rows[2].status, "success");
  assert.equal(result.comparison, null);
});

test("cached ZG aggregates never override public items or per-call measurement evidence", async () => {
  const source = zgReport(),
    original = clone(source);
  for (const preview of ["short", "full"]) {
    source.previews[preview].modes.hybrid.file_retrieval = { mrr_at_10: 999 };
    source.previews[preview].modes.hybrid.semble_official = {
      repository_macro: { ndcg_at_10: 999 },
    };
    source.previews[preview].modes.hybrid.measurements = {
      latency_ms_p50: 999,
    };
  }
  const result = await buildCiSummary({ zg: source });
  assert.equal(result.status, "success");
  assert.equal(
    result.rows[0].metrics.file_mrr_at_10,
    original.modes.hybrid.file_retrieval.mrr_at_10,
  );
  assert.equal(
    result.rows[0].metrics.semble_ndcg_at_10,
    original.modes.hybrid.semble_official.repository_macro.ndcg_at_10,
  );
  assert.equal(result.rows[0].measurements.latency_ms_p50, 12.34567);
});

test("per-question scores, frozen Gold and aggregation metadata tampering are rejected in ZG-only mode", async () => {
  for (const mutate of [
    (r) => {
      r.tasks[0].file_retrieval.rr_at_10 = 1;
    },
    (r) => {
      r.tasks[0].semble_official.ndcg_at_10 = 1;
    },
    (r) => {
      r.suite.gold = "0".repeat(64);
    },
    (r) => {
      r.tasks[0].semble_official.targets = [{ path: "forged.py" }];
    },
    (r) => {
      r.tasks[0].repository = "forged/repository";
    },
    (r) => {
      r.tasks[0].language = "forged-language";
    },
    (r) => {
      r.tasks[0].category = r.tasks[0].category === "what" ? "where" : "what";
    },
    (r) => {
      r.tasks[0].gold_status = "unknown";
    },
    (r) => {
      r.tasks[0].measurement_observations.pop();
    },
  ]) {
    const zg = zgReport();
    mutate(zg);
    const result = await buildCiSummary({ zg });
    assert.equal(result.status, "failed");
    assertUnavailable(result.rows[0], "invalid");
    assertUnavailable(result.rows[1], "invalid");
    assert.ok(result.errors.length > 0);
  }
});

test("Semble cached aggregates and metadata are validated even when ZG is unavailable", async () => {
  for (const mutate of [
    (r) => {
      r.modes.hybrid.file_retrieval.mrr_at_10 = 999;
    },
    (r) => {
      r.modes.hybrid.semble_official.repository_macro.ndcg_at_10 = 999;
    },
    (r) => {
      r.modes.hybrid.measurements.latency_ms_p50 = 999;
    },
    (r) => {
      r.tasks[0].repository = "forged/repository";
    },
  ]) {
    const semble = sembleReport();
    mutate(semble);
    const result = await buildCiSummary({ semble, sembleRequested: true });
    assert.equal(result.status, "failed");
    assertUnavailable(result.rows[2], "invalid");
  }
});

test("mode coverage follows the request and unrequested extra modes cannot hide behind top-level caches", async () => {
  const modes = ["hybrid", "fts", "vector"];
  const full = await buildCiSummary({ zg: zgReport({ modes }), modes });
  assert.equal(full.status, "success");
  assert.equal(full.rows.length, 7);
  assert.deepEqual(
    full.rows.slice(0, 6).map((r) => r.label),
    [
      "ZG hybrid / short",
      "ZG fts / short",
      "ZG vector / short",
      "ZG hybrid / full",
      "ZG fts / full",
      "ZG vector / full",
    ],
  );
  const missing = await buildCiSummary({ zg: zgReport(), modes });
  assert.equal(missing.status, "failed");
  assert.equal(missing.rows.filter((r) => r.status === "invalid").length, 6);
  const extra = zgReport({ modes: ["hybrid", "fts"] });
  extra.modes = { hybrid: extra.modes.hybrid };
  extra.observed_calls = 200;
  const hidden = await buildCiSummary({ zg: extra });
  assert.equal(hidden.status, "failed");
  assertUnavailable(hidden.rows[0], "invalid");
  for (const modes of [
    [],
    ["fts"],
    ["hybrid", "hybrid"],
    ["hybrid", "unknown"],
  ])
    await assert.rejects(
      buildCiSummary({ modes }),
      /invalid requested ZG modes/,
    );
});

test("required failed jobs fail the run and only requested Semble becomes required", async () => {
  const jobs = successes();
  jobs.retrieval.result = "failure";
  const failed = await buildCiSummary({ zg: zgReport(), jobResults: jobs });
  assert.equal(failed.status, "failed");
  assert.match(failed.errors.join("\n"), /retrieval: failure/);
  const absent = await buildCiSummary({
    zg: zgReport(),
    semble: sembleReport(),
    sembleRequested: true,
    jobResults: successes(),
  });
  assert.equal(absent.status, "failed");
  assert.match(absent.errors.join("\n"), /semble: missing job/);
  const unused = successes();
  unused.semble = { result: "failure" };
  assert.equal(
    (await buildCiSummary({ zg: zgReport(), jobResults: unused })).status,
    "success",
  );
});

test("complete product failures are quality zeros, with no fabricated measurement samples", async () => {
  const result = await buildCiSummary({
    zg: zgReport({ failed: true }),
    semble: sembleReport({ failed: true }),
    sembleRequested: true,
  });
  assert.equal(result.status, "failed");
  for (const row of result.rows) {
    assert.equal(row.status, "product_error");
    assert.equal(row.questions, 20);
    assert.deepEqual(Object.values(row.metrics), [0, 0, 0, 0, 0]);
    assert.deepEqual(row.measurements, {
      latency_ms_p50: null,
      latency_sample_count: 0,
      output_bytes_mean: null,
      output_sample_count: 0,
    });
  }
  assert.ok(result.comparison);
});

test("Markdown has five quality columns, two operational columns, exact display precision and sample definitions", async () => {
  const result = await buildCiSummary({
    zg: zgReport(),
    semble: sembleReport(),
    sembleRequested: true,
    runUrl: "https://example.invalid/run/123",
    commit: "a".repeat(40),
  });
  const text = markdownCiSummary(result);
  assert.deepEqual(result.quality_metrics, [
    "file_hit_at_1",
    "file_hit_at_5",
    "file_hit_at_10",
    "file_mrr_at_10",
    "semble_ndcg_at_10",
  ]);
  assert.match(
    text,
    /\| 文件 Hit@1 \| 文件 Hit@5 \| 文件 Hit@10 \| 文件 MRR@10 \| Semble nDCG@10 \| 平均输出 \(KiB\) \| 延迟 P50 \(ms\) \|/,
  );
  const official = result.rows[0].metrics.semble_ndcg_at_10.toFixed(4);
  assert.ok(
    text.includes(
      `| 0.0% (0/20) | 100.0% (20/20) | 100.0% (20/20) | 0.3333 | ${official} | 1.00 | 12.35 |`,
    ),
  );
  assert.match(text, /\| ZG hybrid \/ full .* \| 3\.50 \| 12\.35 \|/);
  assert.match(text, /\| Semble MCP .* \| 10\.00 \| 12\.35 \|/);
  assert.match(text, /质量取每题第 5 次调用/);
  assert.match(text, /Hit\/MRR 按 20 题等权/);
  assert.match(text, /nDCG@10 按仓库宏平均/);
  assert.match(text, /1 KiB = 1024 字节/);
  assert.match(text, /输出 20 个样本；延迟 100 个样本/);
  assert.match(text, /不含建索引/);
  assert.doesNotMatch(text, /nDCG@5|anchor|锚点|Legacy|by.category/i);
  assert.equal(
    text.split("\n").filter((line) => line.startsWith("| 测试组 |")).length,
    1,
  );
  assert.equal(result.rows[0].measurements.latency_ms_p50, 12.34567);
  assert.ok(Math.abs(result.rows[0].metrics.file_mrr_at_10 - 1 / 3) < 1e-15);
});

test("fractional aggregates are exactly independent of shard and task order without mutating rows", () => {
  const ranks = [1, 3, 7, 10, null, 6, 9, 5, 2, 8];
  const report = zgReport({
    rankForTask: (task) => ranks[suite.lock.tasks.indexOf(task) % ranks.length],
  });
  const rows = report.previews.short.tasks;
  const snapshot = clone(rows);
  const permutations = [
    [...rows].reverse(),
    [...rows].sort(
      (a, b) =>
        a.repository.localeCompare(b.repository) ||
        b.task_id.localeCompare(a.task_id),
    ),
    [...rows.slice(7), ...rows.slice(0, 7)],
  ];
  const file = summarizeFileRetrieval(rows),
    official = summarizeSembleOfficial(rows);
  for (const shuffled of permutations) {
    assert.deepEqual(summarizeFileRetrieval(shuffled), file);
    assert.deepEqual(summarizeSembleOfficial(shuffled), official);
  }
  assert.deepEqual(rows, snapshot);
});

test("combined CI table validates exact cached fractional scores after independent report reorderings", async () => {
  const ranks = [1, 3, 7, 10, null, 6, 9, 5, 2, 8];
  const rankForTask = (task) =>
    ranks[suite.lock.tasks.indexOf(task) % ranks.length];
  const zg = zgReport({ rankForTask }),
    semble = sembleReport({ rankForTask });
  const before = await buildCiSummary({ zg, semble, sembleRequested: true });
  assert.equal(before.status, "success");
  const shuffledZg = clone(zg),
    shuffledSemble = clone(semble);
  shuffledZg.tasks.sort(
    (a, b) =>
      a.repository.localeCompare(b.repository) ||
      b.task_id.localeCompare(a.task_id),
  );
  shuffledSemble.tasks.reverse();
  // Keep cached aggregates intact; exact validation must remain possible.
  const after = await buildCiSummary({
    zg: shuffledZg,
    semble: shuffledSemble,
    sembleRequested: true,
  });
  assert.equal(after.status, "success", after.errors.join("\n"));
  assert.deepEqual(after.rows, before.rows);
  for (const preview of ["short", "full"]) {
    assert.deepEqual(
      after.comparison.zg_previews[preview].semble_official,
      before.comparison.zg_previews[preview].semble_official,
    );
    assert.deepEqual(
      after.comparison.zg_previews[preview].file_retrieval,
      before.comparison.zg_previews[preview].file_retrieval,
    );
    assert.deepEqual(
      after.comparison.zg_previews[preview].semble_official.zg,
      shuffledZg.previews[preview].modes.hybrid.semble_official,
    );
  }
});
