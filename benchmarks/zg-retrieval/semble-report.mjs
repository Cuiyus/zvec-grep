import assert from "node:assert/strict";
import { readFile, writeFile, realpath } from "node:fs/promises";
import { join, resolve, relative, isAbsolute } from "node:path";
import { pathToFileURL } from "node:url";
import {
  loadSuite,
  readJson,
  writeJson,
  objectHash,
  fileHash,
  inside,
  repositorySlug,
} from "./lib.mjs";
import { compareReports, selectPreviewReport } from "./compare.mjs";
import { scoreSembleResponse } from "./semble-scoring.mjs";
import { scoreSembleMetric } from "./semble-metrics.mjs";
import {
  summarizeSembleOfficial,
  markdownSembleOfficialTable,
} from "./report.mjs";

export const SEMBLE_PROTOCOL = Object.freeze({
  id: "sweqa20-semble-mcp-v2",
  engine: "semble",
  mode: "hybrid",
  limit: 10,
  repetitions: 5,
  quality_repetition: 5,
  content: "code",
  max_snippet_lines: null,
  endpoint: "semble-native-stdio-mcp",
  model: "minishlab/potion-code-16M-v2",
});

const METRICS = [
  "hit_at_1",
  "hit_at_5",
  "hit_at_10",
  "rr_at_10",
  "ndcg_at_5",
  "ndcg_at_10",
];
const CATEGORIES = ["what", "where", "how", "why"];
const average = (values) =>
  values.length ? values.reduce((a, b) => a + b, 0) / values.length : null;
const same = (a, b, message) =>
  assert.equal(objectHash(a), objectHash(b), message);
const cell = (value) =>
  String(value ?? "N/A")
    .replaceAll("|", "\\|")
    .replace(/[\r\n]+/g, " ");
const number = (value) =>
  typeof value === "number" ? value.toFixed(3) : "N/A";
const sha = (value, label) =>
  assert.match(value ?? "", /^[a-f0-9]{64}$/, label);
const modeRows = (report) =>
  report.tasks.filter((row) => row.mode === "hybrid");
const FILE_PRESENCE_RULE =
  "Diagnostic only: repetition-5 native Top 10 contains an exact file path of at least one accepted Gold target. Bridge-only paths are excluded. This does not establish source-entry relevance or correctness and never changes Hit/MRR/nDCG.";

function goldFilePresence(row, gold) {
  const acceptedPaths = new Set(
    gold.targets
      .filter((target) => target.role === "accepted")
      .map((target) => target.path),
  );
  const paths = [
    ...new Set(
      (row.items ?? [])
        .filter(
          (item) =>
            Number.isInteger(item.rank) &&
            item.rank >= 1 &&
            item.rank <= 10 &&
            acceptedPaths.has(item.path),
        )
        .map((item) => item.path),
    ),
  ].sort();
  return {
    gold_file_presence_at_10:
      row.status === "harness_invalid" ? null : paths.length > 0,
    gold_file_presence_paths: paths,
  };
}

function summarize(rows) {
  const scored = rows.filter((row) => typeof row.hit_at_10 === "number");
  const ndcg = rows.filter((row) => typeof row.ndcg_at_10 === "number");
  return {
    planned_tasks: rows.length,
    scored_tasks: scored.length,
    ndcg_tasks: ndcg.length,
    ...Object.fromEntries(
      [1, 5, 10].map((cutoff) => [
        `hit_at_${cutoff}_count`,
        scored.reduce((sum, row) => sum + row[`hit_at_${cutoff}`], 0),
      ]),
    ),
    ...Object.fromEntries(
      METRICS.map((metric) => [
        metric === "rr_at_10" ? "mrr_at_10" : metric,
        average(
          rows
            .map((row) => row[metric])
            .filter((value) => typeof value === "number"),
        ),
      ]),
    ),
    product_error_tasks: rows.filter(
      (row) => row.execution_status === "product_error",
    ).length,
  };
}

function invalidScore(score, reason) {
  return {
    ...score,
    status: "harness_invalid",
    execution_status: "harness_invalid",
    invalid_reason: reason,
    first_hit_rank: null,
    ...Object.fromEntries(METRICS.map((metric) => [metric, null])),
  };
}

async function checkedArtifact(directory, reference, expectedPath) {
  assert.equal(
    reference?.path,
    expectedPath,
    `unexpected artifact path: ${expectedPath}`,
  );
  sha(reference.sha256, `${expectedPath}: missing file hash`);
  const path = join(directory, reference.path);
  assert.ok(
    inside(await realpath(directory), await realpath(path)),
    `${expectedPath}: artifact escapes run`,
  );
  assert.equal(
    await fileHash(path),
    reference.sha256,
    `${expectedPath}: artifact file hash mismatch`,
  );
  return path;
}

async function auditFrozenRun(directory, run, suite, tasks, modelIdentity) {
  assert.equal(
    run.post_run_integrity,
    "verified",
    "post-run integrity not verified",
  );
  const inventories = {};
  for (const kind of ["corpus", "index", "model"]) {
    inventories[kind] = {};
    for (const phase of ["before", "after"]) {
      const name = `${kind}-${phase}.json`;
      const path = await checkedArtifact(
        directory,
        run.evidence?.[phase]?.[kind],
        name,
      );
      const inventory = await readJson(path);
      sha(inventory.sha256, `${name}: missing inventory hash`);
      assert.ok(
        Array.isArray(inventory.entries) && inventory.entries.length > 0,
        `${name}: empty inventory`,
      );
      assert.equal(
        objectHash(inventory.entries),
        inventory.sha256,
        `${name}: changed/truncated inventory`,
      );
      const paths = inventory.entries.map((entry) => entry.path);
      assert.ok(
        paths.every(
          (path) =>
            typeof path === "string" &&
            path.length &&
            !isAbsolute(path) &&
            !path.split(/[\\/]/).includes(".."),
        ),
        `${name}: unsafe inventory path`,
      );
      assert.equal(
        new Set(paths).size,
        paths.length,
        `${name}: duplicate inventory paths`,
      );
      inventories[kind][phase] = inventory;
    }
    same(
      inventories[kind].before,
      inventories[kind].after,
      `${kind} identity drift`,
    );
  }
  const files = new Map(
    inventories.corpus.before.entries.map((entry) => [entry.path, entry]),
  );
  for (const task of tasks)
    for (const target of suite.gold[task.task_id].targets)
      assert.equal(
        files.get(target.path)?.sha256,
        target.source_sha256,
        `${task.task_id}: stale/missing Gold source ${target.path}`,
      );
  assert.equal(
    inventories.model.before.sha256,
    modelIdentity,
    "shard model differs from experiment model identity",
  );
  const preparation = await readJson(
    await checkedArtifact(directory, run.preparation, "preparation.json"),
  );
  assert.equal(
    preparation.loaded_from_disk,
    false,
    "index preparation restored an index cache",
  );
  assert.equal(
    preparation.source_mapping_verified,
    true,
    "prepared source mapping not verified",
  );
  same(
    preparation.content,
    ["code"],
    "prepared index content is not code-only",
  );
  assert.ok(
    Number.isInteger(preparation.chunk_count) && preparation.chunk_count >= 0,
    "invalid prepared chunk count",
  );
  assert.ok(
    Array.isArray(preparation.indexed_files) &&
      preparation.indexed_files.every((path) => files.has(path)),
    "prepared index contains files outside frozen corpus",
  );
  assert.equal(
    new Set(preparation.indexed_files).size,
    preparation.indexed_files.length,
    "duplicate prepared indexed file",
  );
  assert.ok(
    preparation.chunk_count >= preparation.indexed_files.length,
    "prepared chunk/file count mismatch",
  );
  return {
    corpus_sha256: inventories.corpus.before.sha256,
    index_sha256: inventories.index.before.sha256,
    model_sha256: inventories.model.before.sha256,
  };
}

async function auditSdkParity(directory, run, tasks, calls, modelDirectory) {
  const replay = await readJson(
    await checkedArtifact(directory, run.sdk_replay, "sdk-replay.json"),
  );
  const parity = await readJson(
    await checkedArtifact(directory, run.sdk_parity, "sdk-parity.json"),
  );
  assert.equal(replay.schema_version, 1, "unsupported SDK replay schema");
  assert.equal(replay.engine, "semble", "wrong SDK replay engine");
  assert.equal(
    replay.loaded_from_disk,
    true,
    "SDK replay did not use the frozen index",
  );
  assert.equal(replay.corpus_root, run.corpus_root, "SDK corpus root mismatch");
  assert.equal(replay.model_path, modelDirectory, "SDK model path mismatch");
  const preparation = await readJson(join(directory, "preparation.json"));
  assert.equal(
    replay.index_directory,
    preparation.index_directory,
    "SDK replay index differs from prepared index",
  );
  same(
    replay.content,
    ["code"],
    "SDK replay content differs from code-only protocol",
  );
  same(
    replay.parameters,
    {
      top_k: 10,
      alpha: null,
      rerank: null,
      filter_languages: null,
      filter_paths: null,
      max_snippet_lines: null,
    },
    "SDK replay parameters differ from official defaults",
  );
  assert.equal(parity.schema_version, 1, "unsupported SDK parity schema");
  assert.equal(
    parity.quality_repetition,
    5,
    "SDK parity must bind repetition 5",
  );
  assert.equal(
    parity.sdk_replay_sha256,
    run.sdk_replay.sha256,
    "SDK parity replay hash mismatch",
  );
  assert.ok(
    Array.isArray(replay.queries) && Array.isArray(parity.calls),
    "missing SDK replay/parity task records",
  );
  same(
    replay.queries.map((query) => query.task_id),
    tasks.map((task) => task.task_id),
    "SDK replay task coverage/order mismatch",
  );
  same(
    parity.calls.map((call) => call.task_id),
    tasks.map((task) => task.task_id),
    "SDK parity task coverage/order mismatch",
  );
  for (const task of tasks) {
    const sdk = replay.queries.find((query) => query.task_id === task.task_id);
    const record = parity.calls.find((call) => call.task_id === task.task_id);
    const call = calls.find(
      (call) => call.task_id === task.task_id && call.repetition === 5,
    );
    assert.ok(call, "SDK parity has no fifth MCP call");
    assert.equal(sdk.query, task.query, "SDK replay changed original query");
    assert.ok(Array.isArray(sdk.results), "SDK replay omitted result list");
    assert.equal(
      record.repetition,
      5,
      "SDK parity call is not fifth repetition",
    );
    assert.equal(
      record.raw_path,
      call.raw_path,
      "SDK parity raw call mapping mismatch",
    );
    assert.equal(
      record.raw_sha256,
      call.raw_sha256,
      "SDK parity raw hash mismatch",
    );
    assert.equal(
      record.sdk_result_sha256,
      objectHash(sdk),
      "SDK parity result hash mismatch",
    );
    assert.equal(record.matches, true, "SDK parity mismatch");
    same(record.errors, [], "SDK parity errors");
    const raw = await readJson(
      await checkedArtifact(
        directory,
        { path: call.raw_path, sha256: call.raw_sha256 },
        `raw/${task.task_slug}-hybrid-5.json`,
      ),
    );
    assert.ok(
      !raw.isError &&
        raw.content?.length === 1 &&
        raw.content[0].type === "text",
      "SDK parity requires a successful native MCP response",
    );
    const payload = JSON.parse(raw.content[0].text);
    same(
      payload,
      sdk.results.length
        ? { query: sdk.query, results: sdk.results }
        : { error: "No results found." },
      "fifth MCP result differs from official SDK replay",
    );
  }
  return true;
}

/** Recompute public-response scores, validating all recorded inputs before publishing a headline. */
export async function aggregateSemble(
  directory,
  { baselinePath, allowSubset = false } = {},
) {
  directory = resolve(directory);
  const suite = await loadSuite();
  const experiment = await readJson(join(directory, "experiment.json"));
  const errors = [],
    observations = [],
    repositories = [],
    seen = new Set();
  const recordError = (error) =>
    errors.push(error instanceof Error ? error.message : String(error));
  const expectedIdentity = {
    source: suite.identity.source,
    gold: suite.identity.gold,
    semble_gold: suite.identity.semble_gold,
    protocol: objectHash(SEMBLE_PROTOCOL),
  };
  let selected = [];
  try {
    assert.equal(experiment.schema_version, 1, "unsupported experiment schema");
    assert.equal(experiment.engine, "semble", "wrong experiment engine");
    assert.equal(experiment.complete, true, "experiment did not complete");
    same(
      experiment.protocol,
      SEMBLE_PROTOCOL,
      "Semble protocol differs from frozen contract",
    );
    same(
      experiment.suite,
      expectedIdentity,
      "suite/source/Gold/protocol identity mismatch",
    );
    assert.ok(
      Array.isArray(experiment.expected_task_ids) &&
        experiment.expected_task_ids.length > 0,
      "missing expected tasks",
    );
    assert.equal(
      new Set(experiment.expected_task_ids).size,
      experiment.expected_task_ids.length,
      "duplicate expected task IDs",
    );
    assert.ok(
      experiment.expected_task_ids.every((id) =>
        suite.lock.tasks.some((task) => task.task_id === id),
      ),
      "unknown expected task",
    );
    selected = suite.lock.tasks.filter((task) =>
      experiment.expected_task_ids.includes(task.task_id),
    );
    same(
      experiment.expected_task_ids,
      selected.map((task) => task.task_id),
      "task order differs from frozen original-query order",
    );
    assert.equal(
      experiment.scope,
      selected.length === 20 ? "full-20-original-queries" : "explicit-subset",
      "scope/task count mismatch",
    );
    assert.ok(
      allowSubset || selected.length === 20,
      "full 20-question coverage required; subset needs allowSubset",
    );
    assert.match(
      experiment.tool?.source_commit ?? "",
      /^[a-f0-9]{40}$/,
      "missing Semble source commit",
    );
    assert.ok(
      typeof experiment.tool?.version === "string" &&
        experiment.tool.version.length,
      "missing Semble version",
    );
    assert.ok(
      experiment.environment?.platform && experiment.environment?.architecture,
      "missing execution environment",
    );
    assert.ok(
      Array.isArray(experiment.repository_runs),
      "missing repository runs",
    );
    const expectedRepos = [
      ...new Set(selected.map((task) => task.repository)),
    ].sort();
    same(
      experiment.repository_runs.map((run) => run.repository).sort(),
      expectedRepos,
      "missing/duplicate/unexpected repository shard",
    );
    sha(experiment.tool.model_sha256, "missing experiment model identity");
    const model = await readJson(join(directory, "model-identity.json"));
    assert.ok(
      Array.isArray(model.entries) && model.entries.length > 0,
      "missing model inventory",
    );
    assert.equal(
      model.sha256,
      experiment.tool.model_sha256,
      "global model identity mismatch",
    );
    assert.equal(
      objectHash(model.entries),
      experiment.tool.model_sha256,
      "global model inventory mismatch",
    );
    await checkedArtifact(
      directory,
      { path: "runtime.json", sha256: experiment.tool.runtime_sha256 },
      "runtime.json",
    );
  } catch (error) {
    recordError(error);
  }

  // Keep identifiable observations in an invalid report; never silently shrink its headline denominator.
  for (const reference of experiment.repository_runs ?? []) {
    const runInvalid = [];
    let run,
      calls = [],
      directoryForRun,
      auditRows = [];
    const tasks = selected.filter(
      (task) => task.repository === reference.repository,
    );
    try {
      const path = await checkedArtifact(
        directory,
        reference,
        `${repositorySlug(reference.repository)}/run.json`,
      );
      directoryForRun = resolve(path, "..");
      run = await readJson(path);
      assert.equal(run.schema_version, 1, "unsupported run schema");
      assert.equal(run.engine, "semble", "wrong shard engine");
      assert.equal(
        run.repository,
        reference.repository,
        "shard repository mismatch",
      );
      const repo = suite.lock.repositories.find(
        (item) => item.repository === run.repository,
      );
      assert.ok(repo, "unknown repository");
      assert.equal(
        run.repository_commit,
        repo.commit,
        "repository commit mismatch",
      );
      assert.ok(
        typeof run.corpus_root === "string" && isAbsolute(run.corpus_root),
        "missing absolute corpus root",
      );
      same(
        run.tasks,
        tasks.map((task) => task.task_id),
        "shard tasks differ from expected original order",
      );
      same(run.modes, ["hybrid"], "invalid Semble mode");
      assert.equal(
        run.planned_calls,
        tasks.length * 5,
        "invalid planned call count",
      );
      assert.ok(
        Array.isArray(run.invalid_reasons),
        "missing invalid-reason list",
      );
      runInvalid.push(...run.invalid_reasons);
      const callsPath = await checkedArtifact(
        directoryForRun,
        run.calls,
        "calls.jsonl",
      );
      calls = (await readFile(callsPath, "utf8"))
        .trimEnd()
        .split("\n")
        .filter(Boolean)
        .map(JSON.parse);
      assert.equal(
        calls.length,
        run.planned_calls,
        "incomplete planned call matrix",
      );
      if (run.preparation_status === "ready") {
        Object.assign(
          run,
          await auditFrozenRun(
            directoryForRun,
            run,
            suite,
            tasks,
            experiment.tool.model_sha256,
          ),
        );
        const auditPath = await checkedArtifact(
          directoryForRun,
          run.source_audit,
          "source-audit.json",
        );
        const audit = await readJson(auditPath);
        assert.equal(
          audit.schema_version,
          1,
          "unsupported source audit schema",
        );
        assert.ok(Array.isArray(audit.calls), "missing source audit calls");
        assert.equal(
          audit.calls.length,
          calls.length,
          "incomplete source audit call coverage",
        );
        assert.equal(
          new Set(audit.calls.map((call) => call.raw_path)).size,
          calls.length,
          "duplicate source audit call",
        );
        auditRows = audit.calls;
        run.sdk_parity_verified = await auditSdkParity(
          directoryForRun,
          run,
          tasks,
          calls,
          experiment.tool.model?.directory,
        );
      } else if (run.preparation_status === "product_error") {
        assert.ok(run.preparation_error, "missing product preparation error");
      } else throw new Error("preparation did not succeed");
    } catch (error) {
      runInvalid.push(error.message);
    }
    if (!run) {
      errors.push(`${reference.repository}: ${runInvalid.join("; ")}`);
      continue;
    }
    repositories.push(run);
    for (const [index, call] of calls.entries()) {
      const task = suite.lock.tasks.find(
        (item) => item.task_id === call.task_id,
      );
      const key = `${call.task_id}/${call.mode}/${call.repetition}`;
      const callInvalid = [...runInvalid];
      let score = {};
      try {
        assert.ok(
          task && tasks.some((item) => item.task_id === task.task_id),
          "unplanned task",
        );
        assert.ok(!seen.has(key), `duplicate call: ${key}`);
        seen.add(key);
        assert.equal(call.mode, "hybrid", "unexpected mode");
        assert.equal(
          call.repetition,
          (index % 5) + 1,
          "call repetition/order mismatch",
        );
        assert.equal(
          call.task_id,
          tasks[Math.floor(index / 5)].task_id,
          "call task order mismatch",
        );
        assert.equal(
          call.quality_observation,
          call.repetition === 5,
          "incorrect quality observation",
        );
        assert.equal(
          call.session_first_query,
          index === 0,
          "incorrect first-session-query flag",
        );
        same(
          call.request,
          {
            name: "search",
            arguments: {
              repo: run.corpus_root,
              query: task.query,
              top_k: 10,
              max_snippet_lines: null,
              content: "code",
            },
          },
          "request differs from original-query protocol",
        );
        const expectedRaw = `raw/${task.task_slug}-hybrid-${call.repetition}.json`;
        const rawPath = await checkedArtifact(
          directoryForRun,
          { path: call.raw_path, sha256: call.raw_sha256 },
          expectedRaw,
        );
        score = scoreSembleResponse(
          await readJson(rawPath),
          suite.gold[task.task_id],
          { expectedQuery: task.query },
        );
        if (score.status === "harness_invalid")
          throw new Error(score.invalid_reason ?? "invalid Semble response");
        assert.ok(
          (Number.isFinite(call.latency_ms) && call.latency_ms >= 0) ||
            (call.latency_ms === null &&
              score.execution_status === "product_error" &&
              (run.preparation_status === "product_error" ||
                call.transport_error)),
          "invalid call latency: successful calls require a measurement; unissued product failures may use null",
        );
        if (call.transport_error)
          assert.equal(
            score.execution_status,
            "product_error",
            "transport error disagrees with response",
          );
        if (run.preparation_status === "product_error")
          assert.equal(
            score.execution_status,
            "product_error",
            "preparation failure contains successful response",
          );
        if (run.preparation_status === "ready") {
          const audit = auditRows.find(
            (item) => item.raw_path === call.raw_path,
          );
          assert.ok(audit, "missing source audit for response");
          assert.equal(
            audit.raw_sha256,
            call.raw_sha256,
            "source audit response hash mismatch",
          );
          assert.ok(
            Array.isArray(audit.errors) && audit.errors.length === 0,
            "visible snippet/source mapping audit failed",
          );
          assert.equal(
            audit.results_checked,
            score.items?.length ?? 0,
            "source audit result count mismatch",
          );
        }
      } catch (error) {
        callInvalid.push(error.message);
      }
      if (callInvalid.length) {
        score = invalidScore(score, callInvalid.join("; "));
        errors.push(`${key}: ${callInvalid.join("; ")}`);
      }
      observations.push({
        ...call,
        ...score,
        language: task ? suite.semble_gold[task.task_id].language : null,
        semble_official:
          task && score.status !== "harness_invalid"
            ? {
                targets: suite.semble_gold[task.task_id].targets,
                ...scoreSembleMetric(
                  score.items ?? [],
                  suite.semble_gold[task.task_id].targets,
                ),
              }
            : null,
        category: task?.category ?? null,
        repository: reference.repository,
        raw_path: directoryForRun
          ? relative(directory, join(directoryForRun, call.raw_path ?? ""))
          : call.raw_path,
      });
    }
    errors.push(
      ...runInvalid.map((error) => `${reference.repository}: ${error}`),
    );
  }
  for (const task of selected)
    for (let repetition = 1; repetition <= 5; repetition++)
      if (!seen.has(`${task.task_id}/hybrid/${repetition}`))
        errors.push(`missing call: ${task.task_id}/hybrid/${repetition}`);
  if (observations.length !== selected.length * 5)
    errors.push("observation count differs from full planned matrix");
  if (
    new Set(repositories.map((run) => run.model_sha256).filter(Boolean)).size >
    1
  )
    errors.push("model artifact identities differ across repository shards");
  if (observations.some((row) => row.status === "harness_invalid"))
    errors.push("one or more observations are experimentally invalid");
  const quality = selected.flatMap((task) => {
    const repeats = observations
      .filter((row) => row.task_id === task.task_id)
      .sort((a, b) => a.repetition - b.repetition);
    const row = repeats.find((item) => item.repetition === 5);
    if (!row) return [];
    const valid =
      repeats.length === 5 &&
      repeats.every((item) => item.execution_status === "success");
    return [
      {
        ...row,
        ...goldFilePresence(row, suite.gold[task.task_id]),
        ranking_repeatable: valid
          ? new Set(repeats.map((item) => item.ranking_sha256)).size === 1
          : null,
        output_repeatable: valid
          ? new Set(repeats.map((item) => item.visible_output_sha256)).size ===
            1
          : null,
        repeat_ranks: repeats.map((item) => item.first_hit_rank),
        call_latencies_ms: repeats.map((item) => item.latency_ms),
      },
    ];
  });
  const productErrors = observations.filter(
    (row) => row.execution_status === "product_error",
  ).length;
  const complete = errors.length === 0;
  const report = {
    schema_version: 1,
    quality_repetition: 5,
    engine: "semble",
    generated_at: new Date().toISOString(),
    suite: expectedIdentity,
    protocol: SEMBLE_PROTOCOL,
    tool: experiment.tool,
    environment: experiment.environment,
    scope: experiment.scope,
    expected_task_ids: experiment.expected_task_ids,
    observed_calls: observations.length,
    integrity_passed: complete && productErrors === 0,
    quality_score_valid: complete,
    integrity_errors: [...new Set(errors)],
    product_error_calls: productErrors,
    quality_gate: "report-only; no quality threshold or causal attribution",
    aggregation:
      "equal weight per original question; repetition 5 only; supplementary nDCG on the declared Gold subset",
    modes: {
      hybrid: {
        summary: complete ? summarize(quality) : null,
        semble_official: complete ? summarizeSembleOfficial(quality) : null,
        diagnostics: {
          gold_file_presence_at_10: {
            count: complete
              ? quality.filter((row) => row.gold_file_presence_at_10 === true)
                  .length
              : null,
            planned_tasks: selected.length,
            rule: FILE_PRESENCE_RULE,
          },
        },
        by_category: complete
          ? Object.fromEntries(
              CATEGORIES.map((category) => [
                category,
                summarize(quality.filter((row) => row.category === category)),
              ]),
            )
          : null,
        ranking_repeatable_tasks: quality.filter(
          (row) => row.ranking_repeatable === true,
        ).length,
        output_repeatable_tasks: quality.filter(
          (row) => row.output_repeatable === true,
        ).length,
      },
    },
    repositories,
    tasks: quality,
  };
  if (baselinePath) {
    try {
      const baseline = await readJson(resolve(baselinePath));
      report.cross_tool_comparison = await compareSembleToZg(
        baseline,
        report,
        suite,
      );
      report.cross_tool_comparison.baseline_input = {
        path: resolve(baselinePath),
        sha256: await fileHash(resolve(baselinePath)),
      };
    } catch (error) {
      report.comparison_error = error.message;
    }
  }
  await writeFile(
    join(directory, "scores.jsonl"),
    observations.map((row) => JSON.stringify(row)).join("\n") + "\n",
  );
  await writeJson(join(directory, "report.json"), report);
  await writeFile(join(directory, "report.md"), markdownSembleReport(report));
  return report;
}

function validateQualityRows(report, suite, label) {
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
    report.expected_task_ids?.slice().sort(),
    suite.lock.tasks.map((task) => task.task_id).sort(),
    `${label}: task set mismatch`,
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
  for (const task of suite.lock.tasks) {
    const row = rows.find((item) => item.task_id === task.task_id),
      gold = suite.gold[task.task_id];
    assert.ok(row, `${label}: missing ${task.task_id}`);
    assert.equal(
      row.repository,
      task.repository,
      `${label}: repository mismatch`,
    );
    assert.equal(row.category, task.category, `${label}: category mismatch`);
    assert.equal(
      row.gold_status,
      gold.status,
      `${label}: Gold status mismatch`,
    );
    assert.equal(row.repetition, 5, `${label}: quality repetition mismatch`);
    assert.equal(
      row.language,
      suite.semble_gold[task.task_id].language,
      `${label}: language mismatch`,
    );
    if (row.execution_status === "product_error")
      assert.equal(
        row.items?.length ?? 0,
        0,
        `${label}: product errors cannot provide official retrieval credit`,
      );
    same(
      row.semble_official,
      {
        targets: suite.semble_gold[task.task_id].targets,
        ...scoreSembleMetric(
          row.items ?? [],
          suite.semble_gold[task.task_id].targets,
        ),
      },
      `${label}: Semble official metric differs from public items or frozen projection`,
    );
    assert.equal(
      row.quality_observation,
      true,
      `${label}: not quality observation`,
    );
    assert.ok(
      ["success", "product_error"].includes(row.execution_status),
      `${label}: invalid execution status`,
    );
    assert.equal(
      row.status,
      row.execution_status === "success" ? "scored" : "product_error",
      `${label}: invalid scoring status`,
    );
    const rank = row.first_hit_rank;
    assert.ok(
      rank === "not_in_top10" ||
        (Number.isInteger(rank) && rank >= 1 && rank <= 10),
      `${label}: invalid first rank`,
    );
    for (const cutoff of [1, 5, 10])
      assert.equal(
        row[`hit_at_${cutoff}`],
        Number(Number.isInteger(rank) && rank <= cutoff),
        `${label}: rank/Hit mismatch`,
      );
    assert.equal(
      row.rr_at_10,
      Number.isInteger(rank) ? 1 / rank : 0,
      `${label}: rank/RR mismatch`,
    );
    for (const metric of ["ndcg_at_5", "ndcg_at_10"]) {
      assert.equal(
        row[metric] === null,
        !gold.ndcg.enabled,
        `${label}: nDCG eligibility mismatch`,
      );
      if (gold.ndcg.enabled) {
        assert.ok(
          Number.isFinite(row[metric]) && row[metric] >= 0 && row[metric] <= 1,
          `${label}: invalid nDCG`,
        );
        if (rank === "not_in_top10")
          assert.equal(row[metric], 0, `${label}: nDCG without accepted hit`);
      }
    }
    if (row.execution_status === "product_error")
      assert.equal(rank, "not_in_top10", `${label}: product error cannot hit`);
  }
  return rows;
}

/** Compare common Gold quality only. Engine protocol identities deliberately remain different. */
export async function compareSembleToZg(
  baseline,
  candidate,
  suite = undefined,
) {
  suite ??= await loadSuite();
  if (baseline.previews) {
    compareReports(baseline, baseline);
    const zgPreviews = {};
    for (const preview of ["short", "full"])
      zgPreviews[preview] = await compareSembleToZg(
        selectPreviewReport(baseline, preview),
        candidate,
        suite,
      );
    return {
      ...zgPreviews.short,
      primary_preview: "short",
      zg_previews: zgPreviews,
    };
  }
  compareReports(baseline, baseline); // Existing zg schema/score checks, without rewriting either protocol.
  assert.equal(
    baseline.suite.protocol,
    suite.identity.protocol,
    "baseline: zg protocol mismatch",
  );
  assert.equal(candidate.engine, "semble", "candidate is not Semble");
  same(
    candidate.protocol,
    SEMBLE_PROTOCOL,
    "candidate Semble protocol mismatch",
  );
  assert.equal(
    candidate.suite.protocol,
    objectHash(SEMBLE_PROTOCOL),
    "candidate Semble protocol hash mismatch",
  );
  assert.notEqual(
    candidate.suite.protocol,
    baseline.suite.protocol,
    "cross-tool protocols must retain their distinct identities",
  );
  const before = validateQualityRows(baseline, suite, "zg baseline");
  const after = validateQualityRows(candidate, suite, "Semble candidate");
  const tasks = suite.lock.tasks.map((task) => {
    const oldRow = before.find((row) => row.task_id === task.task_id),
      newRow = after.find((row) => row.task_id === task.task_id);
    const view = (row) => ({
      ...Object.fromEntries(
        [
          "first_hit_rank",
          "execution_status",
          "repository",
          "language",
          "semble_official",
          ...METRICS,
        ].map((key) => [key, row[key]]),
      ),
      ...goldFilePresence(row, suite.gold[task.task_id]),
    });
    return {
      task_id: task.task_id,
      category: task.category,
      repository: task.repository,
      zg: view(oldRow),
      semble: view(newRow),
      semble_official_delta: Object.fromEntries(
        ["ndcg_at_5", "ndcg_at_10"].map((key) => [
          key,
          newRow.semble_official[key] - oldRow.semble_official[key],
        ]),
      ),
      delta: Object.fromEntries(
        METRICS.map((key) => [
          key,
          oldRow[key] === null ? null : newRow[key] - oldRow[key],
        ]),
      ),
    };
  });
  const summarizePair = (pairs) => {
    const zg = summarize(pairs.map((row) => row.zg)),
      semble = summarize(pairs.map((row) => row.semble));
    return {
      zg,
      semble,
      delta: Object.fromEntries(
        Object.keys(zg).map((key) => [
          key,
          zg[key] === null ? null : semble[key] - zg[key],
        ]),
      ),
    };
  };
  return {
    schema_version: 1,
    kind: "cross-tool-quality-observation",
    zg_preview: baseline.preview ?? "short",
    delta_direction: "Semble minus zg",
    source: suite.identity.source,
    gold: suite.identity.gold,
    semble_gold: suite.identity.semble_gold,
    protocols: {
      zg: baseline.suite.protocol,
      semble: candidate.suite.protocol,
    },
    quality_gate:
      "report-only; no causal attribution to a single retrieval component",
    summary: summarizePair(tasks),
    semble_official: {
      zg: summarizeSembleOfficial(tasks.map((row) => row.zg)),
      semble: summarizeSembleOfficial(tasks.map((row) => row.semble)),
    },
    diagnostics: {
      gold_file_presence_at_10: {
        zg_count: tasks.filter(
          (row) => row.zg.gold_file_presence_at_10 === true,
        ).length,
        semble_count: tasks.filter(
          (row) => row.semble.gold_file_presence_at_10 === true,
        ).length,
        planned_tasks: tasks.length,
        rule: FILE_PRESENCE_RULE,
      },
    },
    by_category: Object.fromEntries(
      CATEGORIES.map((category) => [
        category,
        summarizePair(tasks.filter((task) => task.category === category)),
      ]),
    ),
    tasks,
    differences: {
      endpoint: {
        zg: "zg install generated public stdio MCP / zvec_grep_search",
        semble: "Semble native stdio MCP / search",
      },
      model: {
        zg: suite.protocol.model,
        semble: SEMBLE_PROTOCOL.model,
        qualification:
          "Same named model family does not establish identical artifacts, tokenization, embeddings, or runtime. Recorded model inventories remain separate.",
      },
      content: {
        zg:
          baseline.preview === "full"
            ? "Native complete available retrieved source content plus available outline; no whole-file expansion"
            : "Native bounded outline plus short source excerpt",
        semble:
          "Native complete chunk text, no outline; fifth-call results verified against the official SDK",
      },
      filtering: {
        zg: "zg native scan policy",
        semble:
          "content=code; Semble CODE extensions, native scanning and exclusions; zg uses the same fixed extension allowlist but native scanner exclusions may still differ",
      },
      retrieval: {
        zg: "Native zg hybrid",
        semble:
          "Native Semble BM25/vector hybrid and default reranking; no query rewrite or subquery",
      },
      freshness: {
        zg: "autoUpdate=false, freshness=eventual",
        semble:
          "No public auto-update disable switch; frozen corpus/index verified before and after",
      },
      environment: {
        zg: baseline.repositories.map((run) => ({
          repository: run.repository,
          environment: run.environment,
        })),
        semble: candidate.environment,
      },
      latency:
        "No cross-environment timing ratio or speed winner is computed. Index/MCP timings are observations only; first query includes engine-specific load costs.",
    },
    warnings: [
      "Same original queries, repository commits, partial Gold and Hit/MRR/grouped-nDCG semantics; endpoint, representation, filtering, model runtime and environment are not controlled identically.",
      "Semble official nDCG uses the SWE-QA accepted-file projection, not Semble's original benchmark annotations. Both engines use the same first-target-rank algorithm and aggregation. Supplementary anchor scores also depend on native rendering and frozen anchors; zero anchor score does not prove absence of relevant code.",
      ...[baseline, candidate].flatMap((report, i) =>
        report.product_error_calls
          ? [
              `${i ? "Semble" : "zg"}: product-error zeros remain in the denominator; delta may include delivery failures.`,
            ]
          : [],
      ),
    ],
  };
}

export function markdownSembleReport(report) {
  const summary = report.modes.hybrid.summary;
  const lines = [
    "# Semble Retrieval-only — SWE-QA original queries",
    "",
    `Scope: **${cell(report.scope)}**. Calls: **${report.observed_calls}**. Integrity: **${report.integrity_passed ? "PASS" : "FAIL"}**.`,
    "",
    "Quality uses repetition 5; five repeats measure stability, not independent questions. Semble official file-target nDCG is reported first; source-anchor Hit/MRR and grouped nDCG remain separately labeled supplementary metrics. No query rewrite, subquery, result deduplication or invisible source completion.",
    "",
    `Semble ${cell(report.tool?.version)}, commit \`${cell(report.tool?.source_commit)}\`. Native stdio MCP search; content=code; top_k=10; max_snippet_lines=null. Model: ${SEMBLE_PROTOCOL.model}.`,
    ...markdownSembleOfficialTable(report.modes),
    "",
    "## Supplementary source-anchor metrics",
    "",
    "| Mode | Scored / planned | Hit@1 | Hit@5 | Hit@10 | MRR@10 | nDCG@5 | nDCG@10 | nDCG tasks |",
    "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    summary
      ? `| hybrid | ${summary.scored_tasks}/${summary.planned_tasks} | ${summary.hit_at_1_count}/${summary.scored_tasks} | ${summary.hit_at_5_count}/${summary.scored_tasks} | ${summary.hit_at_10_count}/${summary.scored_tasks} | ${number(summary.mrr_at_10)} | ${number(summary.ndcg_at_5)} | ${number(summary.ndcg_at_10)} | ${summary.ndcg_tasks} |`
      : "| hybrid | **Invalid experiment — aggregate withheld** | | | | | | | |",
    "",
    "## Per task",
    "",
    "| Task | Official nDCG@5 / @10 | Official target ranks | Anchor first rank | Anchor RR@10 / grouped nDCG@10 | Gold file present (diagnostic) | Repeat anchor ranks | Same rank / text | Raw |",
    "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ...report.tasks.map(
      (row) =>
        `| ${cell(row.task_id)} | ${number(row.semble_official?.ndcg_at_5)} / ${number(row.semble_official?.ndcg_at_10)} | ${row.semble_official?.target_ranks.map((rank) => rank ?? "not found").join(", ") ?? "N/A"} | ${cell(row.first_hit_rank)} | ${number(row.rr_at_10)} / ${number(row.ndcg_at_10)} | ${cell(row.gold_file_presence_at_10)} | ${row.repeat_ranks.map(cell).join(", ")} | ${cell(row.ranking_repeatable)} / ${cell(row.output_repeatable)} | [response](${row.raw_path}) |`,
    ),
    "",
    `Gold file presence at Top 10 (diagnostic only): **${cell(report.modes.hybrid.diagnostics.gold_file_presence_at_10.count)}/${report.modes.hybrid.diagnostics.gold_file_presence_at_10.planned_tasks}**. ${FILE_PRESENCE_RULE}`,
    "",
    "The public result may show a related file or a target function's body/docstring while omitting the frozen declaration anchor. Supplementary anchor scores measure retrieval, native rendering and anchor annotation together; a zero anchor score does not establish absence of relevant code. File presence alone cannot establish that the returned source text is relevant. Semble official nDCG uses the separately frozen file-target projection.",
    "",
    "## Preparation and latency",
    "",
    "| Repository | Preparation | Index seconds | MCP connect ms | Post-run integrity | Fifth-call SDK parity |",
    "| --- | --- | --- | --- | --- | --- |",
    ...report.repositories.map(
      (run) =>
        `| ${cell(run.repository)} | ${cell(run.preparation_status)} | ${number(run.index_seconds)} | ${number(run.mcp_connect_ms)} | ${cell(run.post_run_integrity)} | ${cell(run.sdk_parity_verified)} |`,
    ),
    "",
    "Individual call latency and session-first-query flags are retained in scores.jsonl. Timings are observations for this environment; no cross-environment speed comparison is made. Five repeats do not characterize tail latency. Semble has no public auto-update disable switch; before/after corpus, model and index inventories must match. Native snippets are independently source-audited; the scoring input is only the public response.",
  ];
  if (report.cross_tool_comparison) {
    const comparison = report.cross_tool_comparison;
    const variants = comparison.zg_previews
      ? [
          ["zg MCP short", comparison.zg_previews.short, "zg"],
          ["zg MCP full", comparison.zg_previews.full, "zg"],
          ["Semble MCP full chunk", comparison, "semble"],
        ]
      : [
          ["zg", comparison, "zg"],
          ["Semble", comparison, "semble"],
        ];
    lines.push(
      "",
      "## Cross-tool quality comparison",
      "",
      "Delta = Semble minus zg. Different engine protocol identities are retained; source/Gold/task coverage and scoring eligibility match.",
      "",
      "| Arm | Official nDCG@5 (repo macro) | Official nDCG@10 (repo macro) | Anchor Hit@1 | Anchor Hit@5 | Anchor Hit@10 | Anchor MRR@10 | Grouped anchor nDCG@5 | Grouped anchor nDCG@10 | Grouped questions |",
      "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
      ...variants.map(([label, entry, engine]) => {
        const s = entry.summary[engine];
        const official = entry.semble_official[engine].repository_macro;
        return `| ${label} | ${number(official.ndcg_at_5)} | ${number(official.ndcg_at_10)} | ${s.hit_at_1_count}/${s.scored_tasks} | ${s.hit_at_5_count}/${s.scored_tasks} | ${s.hit_at_10_count}/${s.scored_tasks} | ${number(s.mrr_at_10)} | ${number(s.ndcg_at_5)} | ${number(s.ndcg_at_10)} | ${s.ndcg_tasks} |`;
      }),
      ...markdownSembleOfficialTable(
        Object.fromEntries(
          variants.map(([label, entry, engine]) => [
            label,
            { semble_official: entry.semble_official[engine] },
          ]),
        ),
        { title: "Cross-tool Semble official metric" },
      ),
      "",
      "Supplementary source-anchor comparison:",
      "",
      "| Engine | Hit@1 | Hit@5 | Hit@10 | MRR@10 | nDCG@10 |",
      "| --- | --- | --- | --- | --- | --- |",
    );
    for (const [label, entry, engine] of variants) {
      const s = entry.summary[engine];
      lines.push(
        `| ${label} | ${s.hit_at_1_count}/${s.scored_tasks} | ${s.hit_at_5_count}/${s.scored_tasks} | ${s.hit_at_10_count}/${s.scored_tasks} | ${number(s.mrr_at_10)} | ${number(s.ndcg_at_10)} |`,
      );
    }
    lines.push(
      "",
      `Separate Gold file presence diagnostic: zg **${comparison.diagnostics.gold_file_presence_at_10.zg_count}/${comparison.diagnostics.gold_file_presence_at_10.planned_tasks}**; Semble **${comparison.diagnostics.gold_file_presence_at_10.semble_count}/${comparison.diagnostics.gold_file_presence_at_10.planned_tasks}**. Both are recomputed from parsed public Top-10 items using the same exact accepted-file rule; neither alters primary scores.`,
      "",
      ...(comparison.zg_previews
        ? [
            "The per-question table below uses zg short. Both independently validated arm comparisons, deltas and per-question rows are retained in report.json under cross_tool_comparison.zg_previews.",
            "",
          ]
        : []),
      "| Task | zg official nDCG@5 / @10 | Semble official nDCG@5 / @10 | Δofficial nDCG@5 / @10 | zg / Semble target ranks | zg / Semble anchor rank | Δanchor RR@10 | Δgrouped nDCG@10 |",
      "| --- | --- | --- | --- | --- | --- | --- | --- |",
      ...comparison.tasks.map(
        (row) =>
          `| ${cell(row.task_id)} | ${number(row.zg.semble_official.ndcg_at_5)} / ${number(row.zg.semble_official.ndcg_at_10)} | ${number(row.semble.semble_official.ndcg_at_5)} / ${number(row.semble.semble_official.ndcg_at_10)} | ${number(row.semble_official_delta.ndcg_at_5)} / ${number(row.semble_official_delta.ndcg_at_10)} | ${row.zg.semble_official.target_ranks.map((rank) => rank ?? "not found").join(", ")} / ${row.semble.semble_official.target_ranks.map((rank) => rank ?? "not found").join(", ")} | ${cell(row.zg.first_hit_rank)} / ${cell(row.semble.first_hit_rank)} | ${number(row.delta.rr_at_10)} | ${number(row.delta.ndcg_at_10)} |`,
      ),
      "",
      ...comparison.warnings.map((warning) => `- ${warning}`),
      "- Endpoint, visible content, native filtering, model runtime, freshness behavior and execution environment differ. The full disclosures are in report.json. Cross-environment latency ratios are intentionally absent.",
    );
  }
  if (report.comparison_error)
    lines.push(
      "",
      `Cross-tool comparison withheld: ${cell(report.comparison_error)}`,
    );
  if (report.product_error_calls)
    lines.push(
      "",
      `Product errors: **${report.product_error_calls}**. Reviewed quality zeros remain in the denominator; operational integrity fails.`,
    );
  if (report.integrity_errors.length)
    lines.push(
      "",
      "## Invalid experiment",
      "",
      ...report.integrity_errors.map((error) => `- ${cell(error)}`),
    );
  return lines.join("\n") + "\n";
}

if (
  process.argv[1] &&
  import.meta.url === pathToFileURL(process.argv[1]).href
) {
  aggregateSemble(process.argv[2], { baselinePath: process.argv[3] })
    .then((report) => {
      if (!report.integrity_passed || report.comparison_error)
        process.exitCode = 1;
    })
    .catch((error) => {
      console.error(error);
      process.exitCode = 1;
    });
}
