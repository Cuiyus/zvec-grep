import assert from "node:assert/strict";
import { test } from "node:test";
import { mkdtemp, readFile, writeFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  aggregateSemble,
  compareSembleToZg,
  SEMBLE_PROTOCOL,
  markdownSembleReport,
} from "../semble-report.mjs";
import {
  fileHash,
  loadSuite,
  objectHash,
  readJson,
  repositorySlug,
  writeJson,
} from "../lib.mjs";

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
      protocol: objectHash(SEMBLE_PROTOCOL),
    },
    protocol: SEMBLE_PROTOCOL,
    tool: {
      version: "0.6.0",
      source_commit: "a".repeat(40),
      model_sha256: objectHash(modelEntries),
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
    for (let repetition = 1; repetition <= 5; repetition++) {
      for (const task of selected) {
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
          quality_observation: repetition === 1,
          session_first_query: calls.length === 0,
          latency_ms: preparationError ? null : 1,
          transport_error: null,
          request: {
            name: "search",
            arguments: {
              repo: run.corpus_root,
              query: task.query,
              top_k: 10,
              max_snippet_lines: 10,
              content: "all",
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
    shards.set(repository, { root, run, reference, calls, audit });
  }
  async function save() {
    for (const { root, run, reference, calls, audit } of shards.values()) {
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
  delete baseline.engine;
  baseline.suite.protocol = suite.identity.protocol;
  delete baseline.protocol;
  return baseline;
}

function expectInvalid(report, pattern) {
  assert.equal(report.quality_score_valid, false);
  assert.equal(report.integrity_passed, false);
  assert.equal(report.modes.hybrid.summary, null);
  assert.match(report.integrity_errors.join("\n"), pattern);
}

test("offline aggregation requires all 100 calls, re-scores raw and records 12 eligible nDCG tasks", async (t) => {
  const f = await fixture(t),
    report = await aggregateSemble(f.directory);
  assert.equal(report.quality_score_valid, true);
  assert.equal(report.integrity_passed, true);
  assert.equal(report.observed_calls, 100);
  assert.equal(report.tasks.length, 20);
  assert.equal(report.modes.hybrid.summary.ndcg_tasks, 12);
  assert.equal(report.modes.hybrid.summary.hit_at_10_count, 0);
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
    ["quality_observation", false],
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
  assert.equal(report.modes.hybrid.summary.scored_tasks, 20);
  assert.equal(report.modes.hybrid.summary.hit_at_10_count, 0);
  assert.equal(
    report.tasks.every((row) => row.ranking_repeatable === null),
    true,
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

test("cross-tool comparison recomputes matched quality without pretending protocols match", async (t) => {
  const f = await fixture(t),
    report = await aggregateSemble(f.directory),
    baseline = zgBaseline(report);
  baseline.modes.hybrid.summary.hit_at_10_count = 999;
  const row = report.tasks[0];
  Object.assign(row, {
    first_hit_rank: 1,
    hit_at_1: 1,
    hit_at_5: 1,
    hit_at_10: 1,
    rr_at_10: 1,
  });
  const comparison = await compareSembleToZg(baseline, report);
  assert.equal(comparison.summary.zg.hit_at_10_count, 0);
  assert.equal(comparison.summary.semble.hit_at_10_count, 1);
  assert.equal(comparison.summary.delta.mrr_at_10, 1 / 20);
  assert.notEqual(comparison.protocols.zg, comparison.protocols.semble);
  assert.equal(comparison.tasks[0].delta.rr_at_10, 1);
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

test("cross-tool comparison rejects incomplete task sets, changed eligibility, identity and score corruption", async (t) => {
  const f = await fixture(t),
    report = await aggregateSemble(f.directory);
  for (const mutate of [
    (r) => r.tasks.pop(),
    (r) => {
      r.suite.gold = "f".repeat(64);
    },
    (r) => {
      r.tasks[0].hit_at_10 = 1;
    },
    (r) => {
      r.tasks.find((row) => row.ndcg_at_10 !== null).ndcg_at_10 = null;
    },
    (r) => {
      r.protocol.content = "code";
    },
    (r) => {
      r.quality_score_valid = false;
    },
  ]) {
    const changed = structuredClone(report);
    mutate(changed);
    await assert.rejects(compareSembleToZg(zgBaseline(report), changed));
  }
});

test("baseline CLI option writes explicit cross-tool disclosure and comparison errors are withheld", async (t) => {
  const f = await fixture(t),
    original = await aggregateSemble(f.directory),
    baselinePath = join(f.directory, "baseline.json");
  await writeJson(baselinePath, zgBaseline(original));
  const report = await aggregateSemble(f.directory, { baselinePath });
  assert.equal(report.cross_tool_comparison.summary.zg.scored_tasks, 20);
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
  assert.match(invalid.comparison_error, /identity mismatch/);
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
  assert.deepEqual(report.tasks[0].call_latencies_ms, [
    null,
    null,
    null,
    null,
    null,
  ]);
});

test("Gold file presence is a separate public-item diagnostic and cannot turn a missing anchor into a hit", async (t) => {
  const f = await fixture(t, { subset: true });
  const call = f.first.calls[0],
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
              content: "# unrelated visible prefix",
            },
          ],
        }),
      },
    ],
  });
  call.raw_sha256 = await fileHash(join(f.first.root, call.raw_path));
  Object.assign(f.first.audit.calls[0], {
    raw_sha256: call.raw_sha256,
    results_checked: 1,
  });
  await f.save();
  const report = await aggregateSemble(f.directory, { allowSubset: true });
  assert.equal(report.quality_score_valid, true);
  assert.equal(report.tasks[0].gold_file_presence_at_10, true);
  assert.deepEqual(report.tasks[0].gold_file_presence_paths, [path]);
  assert.equal(
    report.modes.hybrid.diagnostics.gold_file_presence_at_10.count,
    1,
  );
  assert.equal(report.tasks[0].hit_at_10, 0);
  assert.equal(report.tasks[0].rr_at_10, 0);
  assert.equal(report.modes.hybrid.summary.hit_at_10_count, 0);
  assert.match(
    markdownSembleReport(report),
    /zero score does not establish absence of relevant code/,
  );
});

test("cross-tool file presence uses exact accepted paths in native Top 10, excludes bridge-only files and recomputes both sides", async (t) => {
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
  before.items = [
    { rank: 1, path: bridgeOnly },
    { rank: 2, path: `${accepted}.extra` },
    { rank: 11, path: accepted },
  ];
  before.gold_file_presence_at_10 = true; // Cached diagnostic must not be trusted.
  report.tasks.find((row) => row.task_id === "requests:16").items = [
    { rank: 10, path: accepted },
  ];
  report.tasks.find((row) => row.task_id === "xarray:46").items = [
    { rank: 1, path: shared },
  ];
  const result = await compareSembleToZg(baseline, report);
  assert.equal(result.diagnostics.gold_file_presence_at_10.zg_count, 0);
  assert.equal(result.diagnostics.gold_file_presence_at_10.semble_count, 2);
  const pair = result.tasks.find((row) => row.task_id === "requests:16");
  assert.equal(pair.zg.gold_file_presence_at_10, false);
  assert.equal(pair.semble.gold_file_presence_at_10, true);
  assert.equal(
    result.tasks.find((row) => row.task_id === "xarray:46").semble
      .gold_file_presence_at_10,
    true,
  );
  assert.equal(result.summary.zg.hit_at_10_count, 0);
  assert.equal(result.summary.semble.hit_at_10_count, 0);
  assert.equal(result.summary.delta.mrr_at_10, 0);
});
