import assert from "node:assert/strict";
import { readFile, writeFile } from "node:fs/promises";
import { join, resolve, relative, isAbsolute } from "node:path";
import {
  loadSuite,
  readJson,
  writeJson,
  objectHash,
  fileHash,
  repositorySlug,
} from "../../core/lib.mjs";
import { compareSembleToZg } from "../../reports/cross-engine.mjs";
import { scoreSembleResponse } from "./parse.mjs";
import { scoreSembleMetric } from "../../metrics/ndcg.mjs";
import {
  FILE_RETRIEVAL_CONTRACT,
  fileRetrievalForRow,
  summarizeFileRetrieval,
} from "../../metrics/files.mjs";
import { summarizeSembleOfficial } from "../../metrics/summary.mjs";
import {
  summarizeMeasurements,
  validateCallLatency,
} from "../../metrics/measurements.mjs";
import {
  checkedArtifact,
  auditFrozenRun,
  auditSdkParity,
} from "./evidence.mjs";
import { markdownSembleReport } from "../../reports/semble.mjs";

import { SEMBLE_PROTOCOL } from "./protocol.mjs";

const same = (a, b, message) =>
  assert.equal(objectHash(a), objectHash(b), message);
const sha = (value, label) =>
  assert.match(value ?? "", /^[a-f0-9]{64}$/, label);
function invalidScore(score, reason) {
  return {
    ...score,
    status: "harness_invalid",
    execution_status: "harness_invalid",
    invalid_reason: reason,
  };
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
      let score = {},
        rawResponse;
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
        rawResponse = await readJson(rawPath);
        score = scoreSembleResponse(rawResponse, suite.gold[task.task_id], {
          expectedQuery: task.query,
        });
        if (score.status === "harness_invalid")
          throw new Error(score.invalid_reason ?? "invalid Semble response");
        validateCallLatency({
          latency_ms: call.latency_ms,
          execution_status: score.execution_status,
        });
        if (call.latency_ms === null)
          assert.ok(
            run.preparation_status === "product_error" || call.transport_error,
            "invalid call latency: null requires an unissued preparation or transport failure",
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
        visible_output_bytes:
          score.status === "scored" && score.execution_status === "success"
            ? Buffer.byteLength(
                rawResponse.content.map((block) => block.text).join("\n"),
                "utf8",
              )
            : null,
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
  for (const row of observations) row.file_retrieval = fileRetrievalForRow(row);
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
        ranking_repeatable: valid
          ? new Set(repeats.map((item) => item.ranking_sha256)).size === 1
          : null,
        output_repeatable: valid
          ? new Set(repeats.map((item) => item.visible_output_sha256)).size ===
            1
          : null,
        repeat_file_ranks: repeats.map(
          (item) => item.file_retrieval?.first_hit_rank ?? null,
        ),
        measurement_observations: repeats.map(
          ({
            repetition,
            status,
            execution_status,
            latency_ms,
            visible_output_bytes,
          }) => ({
            repetition,
            status,
            execution_status,
            latency_ms,
            visible_output_bytes,
          }),
        ),
      },
    ];
  });
  const productErrors = observations.filter(
    (row) => row.execution_status === "product_error",
  ).length;
  const complete = errors.length === 0;
  const report = {
    schema_version: 2,
    file_retrieval_contract: FILE_RETRIEVAL_CONTRACT,
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
      "repetition 5 only; file Hit/MRR average all original questions equally; nDCG@10 averages questions within each repository, then repositories equally; query/language means are retained in JSON",
    modes: {
      hybrid: {
        file_retrieval: complete ? summarizeFileRetrieval(quality) : null,
        measurements: complete ? summarizeMeasurements(observations) : null,
        semble_official: complete ? summarizeSembleOfficial(quality) : null,
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

export async function main() {
  const report = await aggregateSemble(process.argv[2], {
    baselinePath: process.argv[3],
  });
  if (!report.integrity_passed || report.comparison_error) process.exitCode = 1;
}
