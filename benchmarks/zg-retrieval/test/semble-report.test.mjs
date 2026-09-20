import assert from "node:assert/strict";
import { test } from "node:test";
import { mkdtemp, readFile, writeFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { aggregateSemble } from "../engines/semble/report.mjs";
import { SEMBLE_PROTOCOL } from "../engines/semble/protocol.mjs";
import { compareSembleToZg } from "../reports/cross-engine.mjs";
import { validateSembleReport } from "../reports/validation.mjs";
import { markdownSembleReport } from "../reports/semble.mjs";
import { scoreSembleMetric } from "../metrics/ndcg.mjs";
import {
  FILE_RETRIEVAL_CONTRACT,
  fileRetrievalForRow,
  summarizeFileRetrieval,
} from "../metrics/files.mjs";
import { summarizeSembleOfficial } from "../metrics/summary.mjs";
import { summarizeMeasurements } from "../metrics/measurements.mjs";
import {
  fileHash,
  loadSuite,
  objectHash,
  readJson,
  repositorySlug,
  writeJson,
} from "../core/lib.mjs";

const suite = await loadSuite();
const emptyResponse = () => ({
  content: [
    { type: "text", text: JSON.stringify({ error: "No results found." }) },
  ],
});
const productError = () => ({
  isError: true,
  content: [{ type: "text", text: "test product error" }],
});

async function fixture(t, { subset = false, preparationError = false } = {}) {
  const directory = await mkdtemp(join(tmpdir(), "semble-report-test-"));
  t.after(() => rm(directory, { recursive: true, force: true }));
  const tasks = subset ? suite.lock.tasks.slice(0, 1) : suite.lock.tasks;
  const modelEntries = [{ path: "model.bin", sha256: "0".repeat(64) }];
  await writeJson(join(directory, "model-identity.json"), {
    sha256: objectHash(modelEntries),
    entries: modelEntries,
  });
  await writeJson(join(directory, "runtime.json"), {
    version: "0.6.0",
    dependencies: [],
  });
  const experiment = {
    schema_version: 1,
    engine: "semble",
    complete: true,
    suite: {
      source: suite.identity.source,
      gold: suite.identity.gold,
      semble_gold: suite.identity.semble_gold,
      protocol: objectHash(SEMBLE_PROTOCOL),
    },
    protocol: SEMBLE_PROTOCOL,
    tool: {
      version: "0.6.0",
      source_commit: "a".repeat(40),
      model_sha256: objectHash(modelEntries),
      model: { directory: "/locked/model" },
      runtime_sha256: await fileHash(join(directory, "runtime.json")),
    },
    environment: {
      platform: "test",
      architecture: "test",
      node: process.version,
    },
    expected_task_ids: tasks.map((task) => task.task_id),
    scope: subset ? "explicit-subset" : "full-20-original-queries",
    repository_runs: [],
  };
  const shards = new Map();
  for (const repository of new Set(tasks.map((task) => task.repository))) {
    const selected = tasks.filter((task) => task.repository === repository);
    const slug = repositorySlug(repository),
      root = join(directory, slug);
    const run = {
      schema_version: 1,
      engine: "semble",
      repository,
      repository_commit: selected[0].repository_commit,
      corpus_root: `/locked/${slug}`,
      tasks: selected.map((task) => task.task_id),
      modes: ["hybrid"],
      planned_calls: selected.length * 5,
      preparation_status: preparationError ? "product_error" : "ready",
      post_run_integrity: preparationError ? undefined : "verified",
      invalid_reasons: [],
      preparation_error: preparationError ? "test index failure" : null,
      index_seconds: 0,
      mcp_connect_ms: 0,
      evidence: { before: {}, after: {} },
    };
    const corpusEntries = [
      ...new Map(
        selected.flatMap((task) =>
          suite.gold[task.task_id].targets.map((target) => [
            target.path,
            {
              path: target.path,
              kind: "file",
              sha256: target.source_sha256,
              size: 1,
            },
          ]),
        ),
      ).values(),
    ].sort((a, b) => a.path.localeCompare(b.path));
    await writeJson(join(root, "preparation.json"), {
      loaded_from_disk: false,
      content: ["code"],
      index_directory: `/locked/index/${slug}`,
      source_mapping_verified: true,
      chunk_count: corpusEntries.length,
      indexed_files: corpusEntries.map((entry) => entry.path),
    });
    run.preparation = {
      path: "preparation.json",
      sha256: await fileHash(join(root, "preparation.json")),
    };
    for (const phase of ["before", "after"]) {
      for (const kind of ["corpus", "index", "model"]) {
        const entries =
          kind === "corpus"
            ? corpusEntries
            : [{ path: `${kind}.bin`, sha256: "0".repeat(64) }];
        const path = `${kind}-${phase}.json`;
        await writeJson(join(root, path), {
          sha256: objectHash(entries),
          entries,
        });
        run.evidence[phase][kind] = {
          path,
          sha256: await fileHash(join(root, path)),
        };
      }
    }
    const calls = [],
      audit = { schema_version: 1, calls: [] };
    for (const task of selected) {
      for (let repetition = 1; repetition <= 5; repetition++) {
        const raw_path = `raw/${task.task_slug}-hybrid-${repetition}.json`;
        await writeJson(
          join(root, raw_path),
          preparationError ? productError() : emptyResponse(),
        );
        const raw_sha256 = await fileHash(join(root, raw_path));
        calls.push({
          task_id: task.task_id,
          mode: "hybrid",
          repetition,
          quality_observation: repetition === 5,
          session_first_query: calls.length === 0,
          latency_ms: preparationError ? null : 1,
          transport_error: null,
          request: {
            name: "search",
            arguments: {
              repo: run.corpus_root,
              query: task.query,
              top_k: 10,
              max_snippet_lines: null,
              content: "code",
            },
          },
          raw_path,
          raw_sha256,
        });
        audit.calls.push({
          raw_path,
          raw_sha256,
          errors: [],
          results_checked: 0,
        });
      }
    }
    const reference = { repository, path: `${slug}/run.json`, sha256: "" };
    experiment.repository_runs.push(reference);
    const sdkReplay = {
      schema_version: 1,
      engine: "semble",
      index_directory: `/locked/index/${slug}`,
      corpus_root: run.corpus_root,
      model_path: "/locked/model",
      loaded_from_disk: true,
      content: ["code"],
      parameters: {
        top_k: 10,
        alpha: null,
        rerank: null,
        filter_languages: null,
        filter_paths: null,
        max_snippet_lines: null,
      },
      queries: selected.map((task) => ({
        task_id: task.task_id,
        query: task.query,
        results: [],
      })),
    };
    const sdkParity = {
      schema_version: 1,
      quality_repetition: 5,
      calls: calls
        .filter((call) => call.repetition === 5)
        .map((call) => ({
          task_id: call.task_id,
          repetition: 5,
          raw_path: call.raw_path,
          raw_sha256: call.raw_sha256,
          matches: true,
          errors: [],
        })),
    };
    shards.set(repository, {
      root,
      run,
      reference,
      calls,
      audit,
      sdkReplay,
      sdkParity,
    });
  }
  async function save() {
    for (const {
      root,
      run,
      reference,
      calls,
      audit,
      sdkReplay,
      sdkParity,
    } of shards.values()) {
      await writeFile(
        join(root, "calls.jsonl"),
        calls.map((call) => JSON.stringify(call)).join("\n") + "\n",
      );
      run.calls = {
        path: "calls.jsonl",
        sha256: await fileHash(join(root, "calls.jsonl")),
      };
      await writeJson(join(root, "source-audit.json"), audit);
      run.source_audit = {
        path: "source-audit.json",
        sha256: await fileHash(join(root, "source-audit.json")),
      };
      await writeJson(join(root, "sdk-replay.json"), sdkReplay);
      run.sdk_replay = {
        path: "sdk-replay.json",
        sha256: await fileHash(join(root, "sdk-replay.json")),
      };
      sdkParity.sdk_replay_sha256 = run.sdk_replay.sha256;
      for (const call of sdkParity.calls)
        call.sdk_result_sha256 = objectHash(
          sdkReplay.queries.find((query) => query.task_id === call.task_id),
        );
      await writeJson(join(root, "sdk-parity.json"), sdkParity);
      run.sdk_parity = {
        path: "sdk-parity.json",
        sha256: await fileHash(join(root, "sdk-parity.json")),
      };
      await writeJson(join(root, "run.json"), run);
      reference.sha256 = await fileHash(join(root, "run.json"));
    }
    await writeJson(join(directory, "experiment.json"), experiment);
  }
  await save();
  return {
    directory,
    experiment,
    shards,
    save,
    first: [...shards.values()][0],
  };
}

function zgBaseline(report) {
  const baseline = structuredClone(report);
  baseline.schema_version = 3;
  baseline.preview = "short";
  for (const row of baseline.tasks) row.preview = "short";
  delete baseline.engine;
  baseline.suite.protocol = suite.identity.protocol;
  delete baseline.protocol;
  return baseline;
}

function expectInvalid(report, pattern) {
  assert.equal(report.quality_score_valid, false);
  assert.equal(report.integrity_passed, false);
  assert.equal(report.modes.hybrid.file_retrieval, null);
  assert.equal(report.modes.hybrid.semble_official, null);
  assert.equal(report.modes.hybrid.measurements, null);
  assert.match(report.integrity_errors.join("\n"), pattern);
}

test("Semble comparison retains both zg preview arms and publishes one shared metric table", async (t) => {
  const f = await fixture(t);
  const report = await aggregateSemble(f.directory);
  const baseline = zgBaseline(report);
  baseline.primary_preview = "short";
  baseline.schema_version = 3;
  baseline.tasks = ["short", "full"].flatMap((preview) =>
    baseline.tasks.map((row) => ({ ...structuredClone(row), preview })),
  );
  baseline.previews = Object.fromEntries(
    ["short", "full"].map((preview) => [
      preview,
      { modes: structuredClone(baseline.modes) },
    ]),
  );
  const comparison = await compareSembleToZg(baseline, report);
  assert.equal(comparison.zg_previews.short.tasks.length, 20);
  assert.equal(comparison.zg_previews.full.tasks.length, 20);
  assert.equal(comparison.zg_previews.full.zg_preview, "full");
  assert.match(
    comparison.zg_previews.full.differences.content.zg,
    /no whole-file expansion/,
  );
  const markdown = markdownSembleReport({
    ...report,
    cross_tool_comparison: comparison,
  });
  assert.match(markdown, /\| zg MCP short \|/);
  assert.match(markdown, /\| zg MCP full \|/);
  assert.match(markdown, /\| Semble MCP full chunk \|/);
  assert.match(
    markdown,
    /File Hit@1.*File MRR@10.*nDCG@10.*Output KiB.*Latency P50/,
  );
  assert.doesNotMatch(
    markdown,
    /anchor|nDCG@5|file presence|Preparation and latency/i,
  );
  assert.equal(
    markdown.split("\n").filter((line) => line.startsWith("| Arm |")).length,
    1,
  );
  baseline.tasks.pop();
  await assert.rejects(compareSembleToZg(baseline, report), /coverage/);
});

test("offline aggregation re-scores all 100 public calls with only five quality metrics and audited measurements", async (t) => {
  const f = await fixture(t),
    report = await aggregateSemble(f.directory);
  assert.equal(report.quality_score_valid, true);
  assert.equal(report.integrity_passed, true);
  assert.equal(report.observed_calls, 100);
  assert.equal(report.tasks.length, 20);
  assert.equal(report.schema_version, 2);
  assert.equal(report.modes.hybrid.semble_official.query_count, 20);
  assert.equal(report.modes.hybrid.semble_official.repository_count, 11);
  assert.equal(
    report.modes.hybrid.semble_official.repository_macro.ndcg_at_10,
    0,
  );
  assert.equal(report.modes.hybrid.measurements.latency_sample_count, 100);
  assert.equal(report.modes.hybrid.measurements.output_sample_count, 20);
  assert.equal(
    report.modes.hybrid.measurements.output_bytes_mean,
    Buffer.byteLength(emptyResponse().content[0].text, "utf8"),
  );
  assert.ok(
    report.tasks.every((row) => row.measurement_observations.length === 5),
  );
  for (const row of report.tasks)
    for (const field of [
      "first_hit_rank",
      "hit_at_1",
      "hit_at_5",
      "hit_at_10",
      "rr_at_10",
      "ndcg_at_5",
      "ndcg_at_10",
      "target_matches",
      "repeat_ranks",
      "gold_file_presence_at_10",
    ])
      assert.ok(!Object.hasOwn(row, field), field);
  assert.ok(!Object.hasOwn(report.modes.hybrid, "summary"));
  assert.ok(!Object.hasOwn(report.modes.hybrid, "diagnostics"));
  assert.ok(!Object.hasOwn(report.modes.hybrid, "by_category"));
  assert.equal(report.file_retrieval_contract, FILE_RETRIEVAL_CONTRACT);
  assert.equal(report.modes.hybrid.file_retrieval.scored_tasks, 20);
  assert.equal(report.modes.hybrid.file_retrieval.hit_at_10_count, 0);
  assert.ok(report.tasks.every((row) => row.file_retrieval.rr_at_10 === 0));
  assert.equal(report.modes.hybrid.ranking_repeatable_tasks, 20);
  assert.equal(report.modes.hybrid.output_repeatable_tasks, 20);
  assert.equal(
    (await readFile(join(f.directory, "scores.jsonl"), "utf8"))
      .trim()
      .split("\n").length,
    100,
  );
  assert.match(
    await readFile(join(f.directory, "report.md"), "utf8"),
    /Integrity: \*\*PASS\*\*/,
  );
});

test("standalone Semble validation accepts complete results and rejects protocol or call-coverage drift", async (t) => {
  const f = await fixture(t),
    report = await aggregateSemble(f.directory);
  assert.equal((await validateSembleReport(report)).length, 20);
  assert.equal((await validateSembleReport(report, suite)).length, 20);
  for (const [mutate, pattern] of [
    [
      (r) => {
        r.schema_version = 1;
      },
      /schema/,
    ],
    [
      (r) => {
        r.engine = "zg";
      },
      /engine/,
    ],
    [
      (r) => {
        r.protocol.limit = 9;
      },
      /protocol/,
    ],
    [
      (r) => {
        r.suite.protocol = "f".repeat(64);
      },
      /protocol/,
    ],
    [
      (r) => {
        r.observed_calls = 99;
      },
      /100-call coverage/,
    ],
    [
      (r) => {
        r.observed_calls = 101;
      },
      /100-call coverage/,
    ],
    [
      (r) => {
        r.tasks.pop();
      },
      /missing\/duplicate/,
    ],
  ]) {
    const changed = structuredClone(report);
    mutate(changed);
    await assert.rejects(validateSembleReport(changed, suite), pattern);
  }
});

test("standalone Semble validation rejects hidden modes, previews, and extra task rows", async (t) => {
  const f = await fixture(t),
    report = await aggregateSemble(f.directory);
  for (const mutate of [
    (r) => {
      r.modes.vector = structuredClone(r.modes.hybrid);
    },
    (r) => {
      r.tasks[0].mode = "vector";
    },
    (r) => {
      r.tasks[0].preview = "full";
    },
    (r) => {
      r.tasks[0].preview = null;
    },
    (r) => {
      r.tasks.push({ ...structuredClone(r.tasks[0]), mode: "vector" });
    },
  ]) {
    const changed = structuredClone(report);
    mutate(changed);
    await assert.rejects(
      validateSembleReport(changed, suite),
      /mode|preview|coverage/,
    );
  }
});

test("Semble product error count must include every repetition even if measurement caches are recomputed", async (t) => {
  const f = await fixture(t),
    report = await aggregateSemble(f.directory);
  const changed = structuredClone(report);
  Object.assign(changed.tasks[0].measurement_observations[0], {
    status: "product_error",
    execution_status: "product_error",
    latency_ms: null,
    visible_output_bytes: null,
  });
  changed.modes.hybrid.measurements = summarizeMeasurements(
    changed.tasks.flatMap((row) => row.measurement_observations),
  );
  assert.equal(changed.modes.hybrid.measurements.latency_sample_count, 99);
  await assert.rejects(
    validateSembleReport(changed, suite),
    /product error count/,
  );
  changed.product_error_calls = 1;
  changed.integrity_passed = false;
  assert.equal((await validateSembleReport(changed, suite)).length, 20);
  const failed = await fixture(t, { preparationError: true });
  const failedReport = await aggregateSemble(failed.directory);
  assert.equal((await validateSembleReport(failedReport, suite)).length, 20);
  failedReport.product_error_calls = 99;
  await assert.rejects(
    validateSembleReport(failedReport, suite),
    /product error count/,
  );
});

test("subset requires explicit allowance and cannot silently become a full-suite headline", async (t) => {
  const f = await fixture(t, { subset: true });
  expectInvalid(
    await aggregateSemble(f.directory),
    /full 20-question coverage required/,
  );
  const report = await aggregateSemble(f.directory, { allowSubset: true });
  assert.equal(report.quality_score_valid, true);
  assert.equal(report.scope, "explicit-subset");
  assert.equal(report.tasks.length, 1);
});

test("changed query, wrong corpus root, or changed top_k invalidates request contract", async (t) => {
  for (const [field, value] of [
    ["query", "rewritten query"],
    ["repo", "/wrong/root"],
    ["top_k", 20],
  ]) {
    const f = await fixture(t, { subset: true });
    f.first.calls[0].request.arguments[field] = value;
    await f.save();
    expectInvalid(
      await aggregateSemble(f.directory, { allowSubset: true }),
      /request differs/,
    );
  }
});

test("missing calls and duplicate call identities invalidate the planned matrix", async (t) => {
  for (const mutation of [
    (calls) => calls.pop(),
    (calls) => {
      calls[1] = structuredClone(calls[0]);
    },
  ]) {
    const f = await fixture(t, { subset: true });
    mutation(f.first.calls);
    await f.save();
    expectInvalid(
      await aggregateSemble(f.directory, { allowSubset: true }),
      /incomplete planned call|duplicate call/,
    );
  }
});

test("wrong repetition and first-query flags invalidate the measurement order", async (t) => {
  for (const [field, value] of [
    ["quality_observation", true],
    ["session_first_query", false],
    ["repetition", 5],
  ]) {
    const f = await fixture(t, { subset: true });
    f.first.calls[0][field] = value;
    await f.save();
    expectInvalid(
      await aggregateSemble(f.directory, { allowSubset: true }),
      /incorrect quality|first-session-query|repetition\/order/,
    );
  }
});

test("raw response content tampering and response reuse are rejected", async (t) => {
  const tampered = await fixture(t, { subset: true });
  await writeJson(
    join(tampered.first.root, tampered.first.calls[0].raw_path),
    productError(),
  );
  expectInvalid(
    await aggregateSemble(tampered.directory, { allowSubset: true }),
    /artifact file hash mismatch/,
  );
  const reused = await fixture(t, { subset: true });
  reused.first.calls[1].raw_path = reused.first.calls[0].raw_path;
  await reused.save();
  expectInvalid(
    await aggregateSemble(reused.directory, { allowSubset: true }),
    /unexpected artifact path/,
  );
});

test("manifest artifact hash is bound to actual bytes", async (t) => {
  const f = await fixture(t, { subset: true });
  await writeFile(join(f.first.root, "run.json"), "{}\n");
  expectInvalid(
    await aggregateSemble(f.directory, { allowSubset: true }),
    /artifact file hash mismatch/,
  );
});

test("before/after index identity drift is rejected even if enclosing file hashes are updated", async (t) => {
  const f = await fixture(t, { subset: true });
  const path = join(f.first.root, "index-after.json");
  const entries = [{ path: "index.bin", sha256: "1".repeat(64) }];
  await writeJson(path, { sha256: objectHash(entries), entries });
  f.first.run.evidence.after.index.sha256 = await fileHash(path);
  await f.save();
  expectInvalid(
    await aggregateSemble(f.directory, { allowSubset: true }),
    /index identity drift/,
  );
});

test("inventory payload hashes and Gold source identities are independently checked", async (t) => {
  for (const staleHash of [true, false]) {
    const f = await fixture(t, { subset: true });
    for (const phase of ["before", "after"]) {
      const path = join(f.first.root, `corpus-${phase}.json`),
        inventory = await readJson(path);
      inventory.entries[0].sha256 = "f".repeat(64);
      if (!staleHash) inventory.sha256 = objectHash(inventory.entries);
      await writeJson(path, inventory);
      f.first.run.evidence[phase].corpus.sha256 = await fileHash(path);
    }
    await f.save();
    expectInvalid(
      await aggregateSemble(f.directory, { allowSubset: true }),
      staleHash ? /changed\/truncated inventory/ : /stale\/missing Gold source/,
    );
  }
});

test("post-run verification is mandatory", async (t) => {
  const f = await fixture(t, { subset: true });
  delete f.first.run.post_run_integrity;
  await f.save();
  expectInvalid(
    await aggregateSemble(f.directory, { allowSubset: true }),
    /post-run integrity not verified/,
  );
});

test("source audit must cover every response with matching SHA, zero errors and result count", async (t) => {
  for (const mutate of [
    (audit) => audit.calls.pop(),
    (audit) => {
      audit.calls[0].raw_sha256 = "f".repeat(64);
    },
    (audit) => {
      audit.calls[0].errors = ["line mismatch"];
    },
    (audit) => {
      audit.calls[0].results_checked = 1;
    },
  ]) {
    const f = await fixture(t, { subset: true });
    mutate(f.first.audit);
    await f.save();
    expectInvalid(
      await aggregateSemble(f.directory, { allowSubset: true }),
      /source audit|source mapping audit/,
    );
  }
});

test("complete preparation product failures remain quality zeros and fail operational integrity", async (t) => {
  const f = await fixture(t, { preparationError: true });
  const report = await aggregateSemble(f.directory);
  assert.equal(report.quality_score_valid, true);
  assert.equal(report.integrity_passed, false);
  assert.equal(report.product_error_calls, 100);
  assert.equal(report.modes.hybrid.file_retrieval.scored_tasks, 20);
  assert.equal(report.modes.hybrid.file_retrieval.hit_at_10_count, 0);
  assert.equal(
    report.modes.hybrid.semble_official.repository_macro.ndcg_at_10,
    0,
  );
  assert.deepEqual(report.modes.hybrid.measurements, {
    latency_ms_p50: null,
    latency_sample_count: 0,
    output_bytes_mean: null,
    output_sample_count: 0,
  });
  assert.equal(
    report.tasks.every((row) => row.ranking_repeatable === null),
    true,
  );
});

async function replaceFifthWithProductFailure(
  f,
  raw,
  { transportError = null, latency = 1 } = {},
) {
  const call = f.first.calls[4];
  await writeJson(join(f.first.root, call.raw_path), raw);
  call.raw_sha256 = await fileHash(join(f.first.root, call.raw_path));
  call.transport_error = transportError;
  call.latency_ms = latency;
  Object.assign(f.first.audit.calls[4], {
    raw_sha256: call.raw_sha256,
    results_checked: 0,
  });
  // A relevant SDK result must not give a failed public MCP response any credit.
  f.first.sdkReplay.queries[0].results = [
    {
      file_path: suite.semble_gold[call.task_id].targets[0].path,
      start_line: 1,
      end_line: 1,
      score: 1,
      content: "def relevant():\n",
    },
  ];
  Object.assign(f.first.sdkParity.calls[0], {
    raw_sha256: call.raw_sha256,
    status: "product_error",
    matches: null,
    errors: [],
  });
  await f.save();
}

test("ready fifth-call product failures stay quality zeros without being SDK mismatches", async (t) => {
  for (const [raw, options] of [
    [productError(), {}],
    [
      {
        content: [
          {
            type: "text",
            text: "Failed to index '/locked/corpus': product failure",
          },
        ],
      },
      {},
    ],
    [
      productError(),
      { transportError: "MCP transport timeout", latency: null },
    ],
  ]) {
    const f = await fixture(t);
    await replaceFifthWithProductFailure(f, raw, options);
    const report = await aggregateSemble(f.directory);
    assert.equal(report.quality_score_valid, true);
    assert.equal(report.integrity_passed, false);
    assert.equal(report.product_error_calls, 1);
    assert.deepEqual(report.integrity_errors, []);
    assert.equal(report.observed_calls, 100);
    assert.equal(report.tasks[0].status, "product_error");
    assert.deepEqual(report.tasks[0].items, []);
    assert.equal(report.tasks[0].file_retrieval.hit_at_10, 0);
    assert.equal(report.tasks[0].semble_official.ndcg_at_10, 0);
    assert.equal(report.modes.hybrid.file_retrieval.scored_tasks, 20);
    assert.equal(report.modes.hybrid.measurements.output_sample_count, 19);
    assert.equal(report.modes.hybrid.measurements.latency_sample_count, 99);
    assert.equal(report.repositories[0].preparation_status, "ready");
    assert.equal(report.repositories[0].post_run_integrity, "verified");
    assert.equal(report.repositories[0].sdk_parity_verified, false);
    assert.equal((await validateSembleReport(report, suite)).length, 20);
  }
});

test("SDK parity cannot skip successful responses or omit the product-failure record", async (t) => {
  const success = await fixture(t, { subset: true });
  Object.assign(success.first.sdkParity.calls[0], {
    status: "product_error",
    matches: null,
  });
  await success.save();
  expectInvalid(
    await aggregateSemble(success.directory, { allowSubset: true }),
    /successful response has an invalid status/,
  );
  const failed = await fixture(t, { subset: true });
  await replaceFifthWithProductFailure(failed, productError());
  delete failed.first.sdkParity.calls[0].status;
  await failed.save();
  expectInvalid(
    await aggregateSemble(failed.directory, { allowSubset: true }),
    /product failure status missing/,
  );
});

test("ready product-error calls cannot claim null latency without transport or preparation evidence", async (t) => {
  const f = await fixture(t, { subset: true });
  await replaceFifthWithProductFailure(f, productError(), { latency: null });
  expectInvalid(
    await aggregateSemble(f.directory, { allowSubset: true }),
    /invalid call latency/,
  );
});

test("Semble protocol stays distinct and wrong source/Gold/protocol identity is rejected", async (t) => {
  const f = await fixture(t, { subset: true });
  assert.notEqual(f.experiment.suite.protocol, suite.identity.protocol);
  f.experiment.suite.protocol = suite.identity.protocol;
  await f.save();
  expectInvalid(
    await aggregateSemble(f.directory, { allowSubset: true }),
    /identity mismatch/,
  );
});

function refreshMetrics(report) {
  for (const row of report.tasks) {
    row.semble_official = {
      targets: suite.semble_gold[row.task_id].targets,
      ...scoreSembleMetric(row.items, suite.semble_gold[row.task_id].targets),
    };
    row.file_retrieval = fileRetrievalForRow(row);
  }
  report.modes.hybrid.file_retrieval = summarizeFileRetrieval(report.tasks);
  report.modes.hybrid.semble_official = summarizeSembleOfficial(report.tasks);
}

test("cross-tool comparison derives five metrics from public file ranks with distinct protocols", async (t) => {
  const f = await fixture(t),
    report = await aggregateSemble(f.directory),
    baseline = zgBaseline(report);
  const row = report.tasks[0];
  row.items = [
    { rank: 1, path: suite.semble_gold[row.task_id].targets[0].path },
  ];
  refreshMetrics(report);
  const comparison = await compareSembleToZg(baseline, report);
  assert.equal(comparison.file_retrieval.zg.hit_at_10_count, 0);
  assert.equal(comparison.file_retrieval.semble.hit_at_10_count, 1);
  assert.equal(comparison.file_retrieval.delta.mrr_at_10, 1 / 20);
  assert.equal(comparison.tasks[0].file_retrieval_delta.rr_at_10, 1);
  assert.deepEqual(Object.keys(comparison.tasks[0].semble_official_delta), [
    "ndcg_at_10",
  ]);
  assert.ok(!Object.hasOwn(comparison, "summary"));
  assert.ok(!Object.hasOwn(comparison, "diagnostics"));
  assert.ok(!Object.hasOwn(comparison, "by_category"));
  assert.notEqual(comparison.protocols.zg, comparison.protocols.semble);
  assert.deepEqual(
    comparison.measurements.semble,
    report.modes.hybrid.measurements,
  );
  assert.match(
    comparison.differences.latency,
    /No cross-environment timing ratio/,
  );
  assert.ok(
    comparison.differences.endpoint &&
      comparison.differences.model &&
      comparison.differences.content &&
      comparison.differences.filtering &&
      comparison.differences.environment,
  );
});

test("cross-tool comparison rejects incomplete coverage, frozen-label drift and forged metric or measurement caches", async (t) => {
  const f = await fixture(t),
    report = await aggregateSemble(f.directory);
  for (const mutate of [
    (r) => r.tasks.pop(),
    (r) => {
      r.schema_version = 1;
    },
    (r) => {
      r.suite.gold = "f".repeat(64);
    },
    (r) => {
      r.tasks[0].semble_official.ndcg_at_10 = 1;
    },
    (r) => {
      r.tasks[0].semble_official.targets = [{ path: "forged.py" }];
    },
    (r) => {
      r.tasks[0].items = [{ rank: 2, path: "invalid-native-rank.py" }];
    },
    (r) => {
      r.tasks[0].gold_status = "unknown";
    },
    (r) => {
      r.protocol.content = "all";
    },
    (r) => {
      r.quality_score_valid = false;
    },
    (r) => {
      r.file_retrieval_contract = "unknown";
    },
    (r) => {
      r.tasks[0].file_retrieval.rr_at_10 = 1;
    },
    (r) => {
      delete r.tasks[0].file_retrieval;
    },
    (r) => {
      r.modes.hybrid.file_retrieval.mrr_at_10 = 1;
    },
    (r) => {
      r.modes.hybrid.semble_official.repository_macro.ndcg_at_10 = 1;
    },
    (r) => {
      r.modes.hybrid.measurements.latency_ms_p50 += 100;
    },
    (r) => {
      r.tasks[0].measurement_observations.pop();
    },
    (r) => {
      r.tasks[0].measurement_observations[0].repetition = 5;
    },
    (r) => {
      r.tasks[0].measurement_observations[0].visible_output_bytes = -1;
    },
    (r) => {
      r.tasks[0].measurement_observations[4].latency_ms += 1;
    },
  ]) {
    const changed = structuredClone(report);
    mutate(changed);
    await assert.rejects(compareSembleToZg(zgBaseline(report), changed));
  }
});

test("cross-tool comparison never mutates public evidence or recalculates from cached scores", async (t) => {
  const f = await fixture(t),
    report = await aggregateSemble(f.directory),
    baseline = zgBaseline(report);
  const snapshot = structuredClone([baseline, report]);
  const comparison = await compareSembleToZg(baseline, report);
  assert.equal(comparison.file_retrieval.semble.scored_tasks, 20);
  assert.equal(comparison.file_retrieval.semble.mrr_at_10, 0);
  assert.deepEqual([baseline, report], snapshot);
});

test("baseline CLI option writes explicit cross-tool disclosure and comparison errors are withheld", async (t) => {
  const f = await fixture(t),
    original = await aggregateSemble(f.directory),
    baselinePath = join(f.directory, "baseline.json");
  await writeJson(baselinePath, zgBaseline(original));
  const report = await aggregateSemble(f.directory, { baselinePath });
  assert.equal(report.cross_tool_comparison.file_retrieval.zg.scored_tasks, 20);
  assert.equal(
    report.cross_tool_comparison.baseline_input.sha256,
    await fileHash(baselinePath),
  );
  assert.match(markdownSembleReport(report), /Cross-tool quality comparison/);
  const bad = zgBaseline(original);
  bad.suite.gold = "f".repeat(64);
  await writeJson(baselinePath, bad);
  const invalid = await aggregateSemble(f.directory, { baselinePath });
  assert.equal(invalid.cross_tool_comparison, undefined);
  assert.match(invalid.comparison_error, /frozen suite(?: identity)? mismatch/);
  assert.equal(invalid.quality_score_valid, true);
});

test("global runtime and model identities remain bound to every repository", async (t) => {
  const changedRuntime = await fixture(t, { subset: true });
  await writeJson(join(changedRuntime.directory, "runtime.json"), {
    version: "changed",
  });
  expectInvalid(
    await aggregateSemble(changedRuntime.directory, { allowSubset: true }),
    /runtime.json: artifact file hash mismatch/,
  );
  const changedModel = await fixture(t, { subset: true });
  const entries = [{ path: "model.bin", sha256: "1".repeat(64) }];
  changedModel.experiment.tool.model_sha256 = objectHash(entries);
  await writeJson(join(changedModel.directory, "model-identity.json"), {
    sha256: objectHash(entries),
    entries,
  });
  await changedModel.save();
  expectInvalid(
    await aggregateSemble(changedModel.directory, { allowSubset: true }),
    /shard model differs/,
  );
});

test("preparation evidence rejects restored indices, unverified mapping and out-of-corpus indexed files", async (t) => {
  for (const [field, value] of [
    ["loaded_from_disk", true],
    ["source_mapping_verified", false],
    ["indexed_files", ["outside.py"]],
  ]) {
    const f = await fixture(t, { subset: true });
    const path = join(f.first.root, "preparation.json");
    const preparation = await readJson(path);
    preparation[field] = value;
    await writeJson(path, preparation);
    f.first.run.preparation.sha256 = await fileHash(path);
    await f.save();
    expectInvalid(
      await aggregateSemble(f.directory, { allowSubset: true }),
      /restored an index cache|mapping not verified|outside frozen corpus/,
    );
  }
});

test("a successful call cannot claim unknown latency while unissued product failure retains null", async (t) => {
  const success = await fixture(t, { subset: true });
  success.first.calls[0].latency_ms = null;
  await success.save();
  expectInvalid(
    await aggregateSemble(success.directory, { allowSubset: true }),
    /invalid call latency/,
  );
  const failure = await fixture(t, { subset: true, preparationError: true });
  const report = await aggregateSemble(failure.directory, {
    allowSubset: true,
  });
  assert.equal(report.quality_score_valid, true);
  assert.deepEqual(
    report.tasks[0].measurement_observations.map((row) => row.latency_ms),
    [null, null, null, null, null],
  );
});

test("file localization receives credit from the public path without requiring a visible declaration", async (t) => {
  const f = await fixture(t, { subset: true });
  for (const [index, measurement] of f.first.calls.entries())
    measurement.latency_ms = [100, 1, 7, 3, 5][index];
  const call = f.first.calls[4],
    task = suite.lock.tasks.find((task) => task.task_id === call.task_id);
  const path = suite.gold[task.task_id].targets.find(
    (target) => target.role === "accepted",
  ).path;
  await writeJson(join(f.first.root, call.raw_path), {
    content: [
      {
        type: "text",
        text: JSON.stringify({
          query: task.query,
          results: [
            {
              file_path: path,
              start_line: 1,
              end_line: 1,
              score: 1,
              content: "# unrelated visible prefix 🧪",
            },
          ],
        }),
      },
    ],
  });
  call.raw_sha256 = await fileHash(join(f.first.root, call.raw_path));
  Object.assign(f.first.audit.calls[4], {
    raw_sha256: call.raw_sha256,
    results_checked: 1,
  });
  const payload = JSON.parse(
    (await readJson(join(f.first.root, call.raw_path))).content[0].text,
  );
  f.first.sdkReplay.queries[0].results = payload.results;
  f.first.sdkParity.calls[0].raw_sha256 = call.raw_sha256;
  await f.save();
  const report = await aggregateSemble(f.directory, { allowSubset: true });
  assert.equal(report.quality_score_valid, true);
  const publicText = (await readJson(join(f.first.root, call.raw_path)))
    .content[0].text;
  assert.equal(
    report.tasks[0].visible_output_bytes,
    Buffer.byteLength(publicText, "utf8"),
  );
  assert.ok(report.tasks[0].visible_output_bytes > publicText.length);
  assert.equal(
    report.modes.hybrid.measurements.output_bytes_mean,
    Buffer.byteLength(publicText, "utf8"),
  );
  assert.equal(report.modes.hybrid.measurements.output_sample_count, 1);
  assert.equal(report.modes.hybrid.measurements.latency_ms_p50, 5);
  assert.equal(report.modes.hybrid.measurements.latency_sample_count, 5);
  assert.equal(report.tasks[0].file_retrieval.rr_at_10, 1);
  assert.equal(report.modes.hybrid.file_retrieval.hit_at_1_count, 1);
  assert.equal(report.tasks[0].repetition, 5);
  assert.ok(report.tasks[0].semble_official.ndcg_at_10 > 0);
  assert.ok(!Object.hasOwn(report.tasks[0], "target_matches"));
  assert.ok(!Object.hasOwn(report.tasks[0], "hit_at_10"));
  assert.match(
    markdownSembleReport(report),
    /\| Semble MCP full chunk \| 1\.0000 \| 1\.0000 \| 1\.0000 \| 1\.0000 \|/,
  );
  assert.doesNotMatch(
    markdownSembleReport(report),
    /anchor|nDCG@5|file presence/i,
  );
});

test("cross-tool file metrics preserve native Top-10 ranks and exclude bridge-only targets", async (t) => {
  const f = await fixture(t),
    report = await aggregateSemble(f.directory),
    baseline = zgBaseline(report);
  const requestsGold = suite.gold["requests:16"],
    xarrayGold = suite.gold["xarray:46"];
  const accepted = requestsGold.targets.find(
    (target) => target.role === "accepted",
  ).path;
  const bridgeOnly = requestsGold.targets.find(
    (target) => target.role === "bridge",
  ).path;
  const shared = xarrayGold.targets.find(
    (target) => target.role === "bridge",
  ).path;
  const before = baseline.tasks.find((row) => row.task_id === "requests:16");
  before.items = Array.from({ length: 10 }, (_, i) => ({
    rank: i + 1,
    path: "unrelated.py",
  }));
  before.items[0].path = bridgeOnly;
  before.items[1].path = `${accepted}.extra`;
  report.tasks.find((row) => row.task_id === "requests:16").items = Array.from(
    { length: 10 },
    (_, i) => ({ rank: i + 1, path: i === 9 ? accepted : "unrelated.py" }),
  );
  report.tasks.find((row) => row.task_id === "xarray:46").items = [
    { rank: 1, path: shared },
  ];
  for (const r of [baseline, report]) refreshMetrics(r);
  const result = await compareSembleToZg(baseline, report);
  const pair = result.tasks.find((row) => row.task_id === "requests:16");
  assert.equal(result.file_retrieval.zg.hit_at_10_count, 0);
  assert.equal(result.file_retrieval.semble.hit_at_10_count, 2);
  assert.equal(result.file_retrieval.semble.mrr_at_10, 1.1 / 20);
  assert.equal(pair.semble.file_retrieval.first_hit_rank, 10);
  assert.equal(pair.file_retrieval_delta.rr_at_10, 0.1);
});

test("official nDCG10 retains query/repository/language weighting over all questions", () => {
  const row = (repository, language, value) => ({
    repository,
    language,
    semble_official: { ndcg_at_10: value },
  });
  const result = summarizeSembleOfficial([
    row("a", "python", 1),
    row("a", "python", 1),
    row("a", "python", 1),
    row("b", "python", 0),
    row("c", "go", 0),
  ]);
  assert.equal(result.query_count, 5);
  assert.equal(result.repository_count, 3);
  assert.equal(result.language_count, 2);
  assert.equal(result.query_mean.ndcg_at_10, 0.6);
  assert.equal(result.repository_macro.ndcg_at_10, 1 / 3);
  assert.equal(result.language_macro.ndcg_at_10, 0.25);
  assert.equal(result.by_language.python.ndcg_at_10, 0.5);
});

test("SDK parity evidence is mandatory and independently recomputed, not accepted from matches=true", async (t) => {
  for (const mutate of [
    (f) => {
      f.first.sdkReplay.parameters.alpha = 0.8;
    },
    (f) => {
      f.first.sdkParity.calls[0].matches = false;
    },
    (f) => {
      f.first.sdkParity.calls[0].raw_sha256 = "f".repeat(64);
    },
    (f) => {
      f.first.sdkReplay.queries[0].results = [
        {
          file_path: "other.py",
          start_line: 1,
          end_line: 1,
          score: 0,
          content: "pass",
        },
      ];
    },
    (f) => {
      f.first.sdkReplay.queries[0].query = "rewritten";
    },
  ]) {
    const f = await fixture(t, { subset: true });
    mutate(f);
    await f.save();
    expectInvalid(
      await aggregateSemble(f.directory, { allowSubset: true }),
      /SDK replay parameters|SDK parity mismatch|SDK parity raw hash|differs from official SDK|changed original query/,
    );
  }
});
