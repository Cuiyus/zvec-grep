import assert from "node:assert/strict";
import { readdir, readFile, writeFile } from "node:fs/promises";
import { join, resolve, isAbsolute } from "node:path";
import { pathToFileURL } from "node:url";
import {
  loadSuite,
  readJson,
  writeJson,
  objectHash,
  inside,
  fileHash,
} from "./lib.mjs";
import { scoreResponse } from "./scoring.mjs";

const metricKeys = [
  "hit_at_1",
  "hit_at_5",
  "hit_at_10",
  "rr_at_10",
  "ndcg_at_5",
  "ndcg_at_10",
];
const average = (values) =>
  values.length
    ? values.reduce((sum, value) => sum + value, 0) / values.length
    : null;
const display = (value) =>
  typeof value === "number" ? value.toFixed(3) : "N/A";

function summarize(rows) {
  const scored = rows.filter((row) => row.hit_at_10 !== null);
  const ndcg = rows.filter((row) => row.ndcg_at_10 !== null);
  return {
    planned_tasks: rows.length,
    scored_tasks: scored.length,
    hit_at_1_count: scored.reduce((sum, row) => sum + row.hit_at_1, 0),
    hit_at_5_count: scored.reduce((sum, row) => sum + row.hit_at_5, 0),
    hit_at_10_count: scored.reduce((sum, row) => sum + row.hit_at_10, 0),
    ...Object.fromEntries(
      metricKeys.map((key) => [
        key === "rr_at_10" ? "mrr_at_10" : key,
        average(
          rows
            .map((row) => row[key])
            .filter((value) => typeof value === "number"),
        ),
      ]),
    ),
    ndcg_tasks: ndcg.length,
    product_errors: rows.filter(
      (row) => row.execution_status === "product_error",
    ).length,
    invalid_tasks: rows.filter((row) => row.status === "harness_invalid")
      .length,
  };
}

async function findRuns(directory) {
  const runs = [];
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    if (entry.isFile() && entry.name === "run.json") runs.push(directory);
    else if (
      entry.isDirectory() &&
      ![
        "consumer",
        "node_modules",
        "runtime-home",
        "model-cache",
        "raw",
        "stages",
        "installation",
      ].includes(entry.name)
    )
      runs.push(...(await findRuns(join(directory, entry.name))));
  }
  return runs;
}

function invalidate(score, reason) {
  return {
    ...score,
    status: "harness_invalid",
    execution_status: "harness_invalid",
    invalid_reason: reason,
    first_hit_rank: null,
    ...Object.fromEntries(metricKeys.map((key) => [key, null])),
  };
}

async function stageEvidence(directory, entry) {
  const observations = [];
  try {
    const files = await readJson(join(directory, "stages/before/files.json"));
    const scanned = new Set(
      (await readJson(join(directory, "stages/before/scan.json"))).files.map(
        (file) => file.relativePath,
      ),
    );
    const fragments = (
      await readFile(join(directory, "stages/before/fragments.jsonl"), "utf8")
    )
      .split("\n")
      .filter(Boolean)
      .map(JSON.parse);
    for (const target of entry.targets) {
      const file = files.find((file) => file.relativePath === target.path);
      const carriers = fragments.filter(
        (fragment) =>
          fragment.path === target.path &&
          target.anchors.some((anchor) => {
            if (
              !fragment.contiguous_source_from_range_start ||
              fragment.range?.kind !== "text" ||
              anchor.start_line < fragment.range.startLine ||
              anchor.end_line > fragment.range.endLine
            )
              return false;
            const content = (fragment.content ?? "").split(/\r?\n/);
            return anchor.text.split("\n").every((line, index) => {
              const offset =
                anchor.start_line - fragment.range.startLine + index;
              return (
                content[offset] === line ||
                (offset === 0 && content[offset] === line.trimStart())
              );
            });
          }),
      );
      observations.push({
        target_id: target.id,
        role: target.role,
        scanned: scanned.has(target.path),
        indexed_file: Boolean(file),
        file_error: file?.indexStatus?.error ?? null,
        stored_anchor_carriers: carriers.map((carrier) => carrier.id),
        earliest_observed_loss: !scanned.has(target.path)
          ? "not_scanned"
          : !file
            ? "not_in_persisted_files"
            : file.indexStatus?.error
              ? "index_file_error"
              : !carriers.length
                ? "no_verified_source_carrier_outline_mapping_inconclusive"
                : "unobserved_candidates_or_final_visibility",
      });
    }
  } catch (error) {
    return { status: "not_available", reason: error.message, targets: [] };
  }
  return {
    status: "available",
    targets: observations,
    interpretation:
      "per-target offline diagnostics; candidate pools and actual embedding inputs remain unobserved",
  };
}

export async function aggregate(directory, { expectedTasks } = {}) {
  directory = resolve(directory);
  const suite = await loadSuite();
  const expected =
    expectedTasks ?? suite.lock.tasks.map((task) => task.task_id);
  const errors = [],
    observations = [],
    manifests = [];
  const runDirectories = await findRuns(directory);
  const seen = new Set();
  const evidence = new Map();
  for (const runDirectory of runDirectories) {
    const manifest = await readJson(join(runDirectory, "run.json"));
    manifests.push(manifest);
    const invalid = [...(manifest.invalid_reasons ?? [])];
    if (objectHash(manifest.suite) !== objectHash(suite.identity))
      invalid.push("suite/protocol/gold hash differs from the scorer checkout");
    if (objectHash(manifest.protocol) !== objectHash(suite.protocol))
      invalid.push("protocol content differs from the frozen suite");
    const repo = suite.lock.repositories.find(
      (repo) => repo.repository === manifest.repository,
    );
    if (!repo || manifest.repository_commit !== repo.commit)
      invalid.push("repository identity mismatch");
    if (!manifest.finished_at) invalid.push("run did not finish");
    let calls = [];
    try {
      calls = (await readFile(join(runDirectory, "requests.jsonl"), "utf8"))
        .split("\n")
        .filter(Boolean)
        .map(JSON.parse);
    } catch (error) {
      invalid.push(`missing/invalid call log: ${error.message}`);
    }
    // A completed call log alone is not proof of a valid frozen experiment.
    if (manifest.preparation_status === "ready") {
      try {
        assert.equal(
          manifest.post_run_integrity,
          "verified",
          "post-run integrity not verified",
        );
        for (const phase of ["before", "after"]) {
          const snapshot = await readJson(
            join(runDirectory, `stages/${phase}/summary.json`),
          );
          assert.equal(
            snapshot.logical_content_sha256,
            manifest.index_content_sha256,
            "index identity drift",
          );
          assert.ok(
            snapshot.artifacts && Object.keys(snapshot.artifacts).length === 4,
            "missing snapshot artifact identities",
          );
          for (const [path, hash] of Object.entries(snapshot.artifacts)) {
            assert.ok(
              [
                "files.json",
                "scan.json",
                "manifest.json",
                "fragments.jsonl",
              ].includes(path),
            );
            assert.equal(
              await fileHash(join(runDirectory, `stages/${phase}`, path)),
              hash,
              `snapshot artifact drift: ${path}`,
            );
          }
        }
        for (const [path, hash] of [
          ["corpus.json", manifest.corpus_sha256],
          ["corpus-after.json", manifest.corpus_sha256],
          ["model-files.json", manifest.model_files_sha256],
          ["model-files-after.json", manifest.model_files_sha256],
        ]) {
          const inventory = await readJson(join(runDirectory, path));
          assert.match(hash, /^[a-f0-9]{64}$/);
          assert.equal(inventory.sha256, hash, `${path}: identity mismatch`);
          assert.equal(
            objectHash(inventory.entries),
            hash,
            `${path}: truncated/changed inventory`,
          );
        }
      } catch (error) {
        invalid.push(`frozen experiment audit: ${error.message}`);
      }
    } else if (
      !manifest.preparation_error ||
      calls.some((call) => !call.preparation_error)
    ) {
      invalid.push(
        "preparation did not succeed and no complete product preparation failure was recorded",
      );
    }
    const modeSet = manifest.modes ?? [];
    if (
      !modeSet.length ||
      modeSet[0] !== "hybrid" ||
      new Set(modeSet).size !== modeSet.length ||
      modeSet.some((mode) => !suite.protocol.available_modes.includes(mode))
    )
      invalid.push("invalid modes");
    const plannedCount =
      manifest.tasks.length * modeSet.length * suite.protocol.repetitions;
    if (
      calls.length !== plannedCount ||
      manifest.planned_calls !== plannedCount
    )
      invalid.push("incomplete planned call matrix");
    if (
      manifest.tasks.some(
        (id) =>
          !expected.includes(id) ||
          !suite.lock.tasks.some(
            (task) =>
              task.task_id === id && task.repository === manifest.repository,
          ),
      )
    )
      invalid.push("unexpected task selection");
    for (const call of calls) {
      const task = suite.lock.tasks.find(
        (task) => task.task_id === call.task_id,
      );
      if (!task || !manifest.tasks.includes(call.task_id)) {
        errors.push(`unknown/unplanned task ${call.task_id}`);
        continue;
      }
      const key = `${call.task_id}/${call.mode}/${call.repetition}`;
      if (seen.has(key)) {
        errors.push(`duplicate call: ${key}`);
        continue;
      }
      seen.add(key);
      const callInvalid = [...invalid];
      if (
        !modeSet.includes(call.mode) ||
        !Number.isInteger(call.repetition) ||
        call.repetition < 1 ||
        call.repetition > suite.protocol.repetitions
      )
        callInvalid.push("unplanned mode/repetition");
      const args = call.request?.arguments;
      const expectedRoot = manifest.corpus_root ?? "<unavailable>";
      if (expectedRoot !== "<unavailable>" && !isAbsolute(expectedRoot))
        callInvalid.push("non-absolute corpus root");
      if (expectedRoot === "<unavailable>" && !call.preparation_error)
        callInvalid.push("missing corpus root");
      const expectedArgs = {
        root: expectedRoot,
        ...(call.mode === "hybrid"
          ? { query: task.query }
          : { [call.mode]: [task.query] }),
        limit: suite.protocol.limit,
        ...suite.protocol.request,
      };
      if (
        call.request?.name !== "zvec_grep_search" ||
        objectHash(args) !== objectHash(expectedArgs)
      )
        callInvalid.push("request does not match the original-query protocol");
      if (
        call.quality_observation !==
        (call.repetition === suite.protocol.quality_repetition)
      )
        callInvalid.push("incorrect quality repetition");
      if (call.harness_error) callInvalid.push(call.harness_error);
      let score;
      try {
        assert.equal(
          call.raw_path,
          `raw/${task.task_slug}-${call.mode}-${call.repetition}.json`,
          "raw response reused or mapped to the wrong call",
        );
        const path = join(runDirectory, call.raw_path);
        assert.ok(inside(runDirectory, path), "raw response path escapes run");
        assert.equal(
          await fileHash(path),
          call.raw_sha256,
          "raw response changed since capture",
        );
        score = scoreResponse(await readJson(path), suite.gold[call.task_id]);
      } catch (error) {
        callInvalid.push(error.message);
        score = {};
      }
      if (callInvalid.length) score = invalidate(score, callInvalid.join("; "));
      observations.push({
        ...call,
        ...score,
        category: task.category,
        repository: task.repository,
        raw_path: `${runDirectory.slice(directory.length + 1)}/${call.raw_path}`,
      });
    }
    for (const taskId of manifest.tasks) {
      for (const mode of modeSet) {
        for (
          let repetition = 1;
          repetition <= suite.protocol.repetitions;
          repetition++
        ) {
          if (!seen.has(`${taskId}/${mode}/${repetition}`))
            errors.push(`missing call: ${taskId}/${mode}/${repetition}`);
        }
      }
      if (suite.gold[taskId])
        evidence.set(
          taskId,
          await stageEvidence(runDirectory, suite.gold[taskId]),
        );
    }
    errors.push(
      ...invalid.map((reason) => `${manifest.repository}: ${reason}`),
    );
  }
  for (const id of expected)
    if (
      !observations.some(
        (row) =>
          row.task_id === id && row.mode === "hybrid" && row.repetition === 1,
      )
    )
      errors.push(`missing quality observation: ${id}`);
  for (const field of ["tarball_sha256"])
    if (
      new Set(manifests.map((manifest) => manifest.package?.[field])).size !== 1
    )
      errors.push(`mixed candidate ${field}`);
  if (
    new Set(
      manifests
        .filter((manifest) => manifest.model_files_sha256)
        .map((manifest) => manifest.model_files_sha256),
    ).size > 1
  )
    errors.push("mixed model artifact hashes across repositories");
  if (
    new Set(manifests.map((manifest) => JSON.stringify(manifest.modes))).size >
    1
  )
    errors.push("mixed mode selections across repository shards");
  if (observations.some((row) => row.status === "harness_invalid"))
    errors.push("one or more observations are experimentally invalid");
  const quality = observations
    .filter((row) => row.repetition === suite.protocol.quality_repetition)
    .map((row) => {
      const repeats = observations.filter(
        (item) => item.task_id === row.task_id && item.mode === row.mode,
      );
      const valid =
        repeats.length === suite.protocol.repetitions &&
        repeats.every(
          (item) =>
            item.execution_status === "success" &&
            item.status !== "harness_invalid",
        );
      return {
        ...row,
        ranking_repeatable: valid
          ? new Set(repeats.map((item) => item.ranking_sha256)).size === 1
          : null,
        output_repeatable: valid
          ? new Set(repeats.map((item) => item.visible_output_sha256)).size ===
            1
          : null,
        repeat_ranks: repeats.map((item) => item.first_hit_rank),
        call_latencies_ms: repeats.map((item) => item.latency_ms),
        freshness_values: repeats.map((item) => item.freshness ?? null),
        stage_evidence: evidence.get(row.task_id) ?? {
          status: "not_available",
        },
      };
    });
  const productErrors = observations.filter(
    (row) => row.execution_status === "product_error",
  ).length;
  const complete = errors.length === 0;
  const modes = [...new Set(observations.map((row) => row.mode))];
  const report = {
    schema_version: 1,
    generated_at: new Date().toISOString(),
    suite: suite.identity,
    scope:
      expected.length === 20 ? "full-20-original-queries" : "explicit-subset",
    expected_task_ids: expected,
    observed_calls: observations.length,
    integrity_passed: complete && productErrors === 0,
    quality_score_valid: complete,
    integrity_errors: [...new Set(errors)],
    product_error_calls: productErrors,
    quality_gate: "report-only; no arbitrary quality threshold",
    aggregation:
      "equal weight per task; repetition 1 only; supplementary nDCG on its declared subset",
    modes: Object.fromEntries(
      modes.map((mode) => {
        const rows = quality.filter((row) => row.mode === mode);
        return [
          mode,
          {
            summary: complete ? summarize(rows) : null,
            by_category: complete
              ? Object.fromEntries(
                  ["what", "where", "how", "why"].map((category) => [
                    category,
                    summarize(rows.filter((row) => row.category === category)),
                  ]),
                )
              : null,
            ranking_repeatable_tasks: rows.filter(
              (row) => row.ranking_repeatable === true,
            ).length,
            output_repeatable_tasks: rows.filter(
              (row) => row.output_repeatable === true,
            ).length,
          },
        ];
      }),
    ),
    repositories: manifests,
    tasks: quality,
  };
  await writeFile(
    join(directory, "scores.jsonl"),
    observations.map((row) => JSON.stringify(row)).join("\n") + "\n",
  );
  await writeJson(join(directory, "report.json"), report);
  await writeFile(join(directory, "report.md"), markdownReport(report));
  return report;
}

export function markdownReport(report) {
  const lines = [
    "# zg Retrieval-only — SWE-QA original queries",
    "",
    `Scope: **${report.scope}**. Calls: **${report.observed_calls}**. Integrity: **${report.integrity_passed ? "PASS" : "FAIL"}**.`,
    "",
    "Quality uses repetition 1; five repeats measure stability, not five independent questions. Known source-entry positives are incomplete. Hit is OR; supplementary nDCG rewards distinct complementary groups and is not answer accuracy. Quality thresholds are report-only.",
    "",
    "| Mode | Scored / planned | Hit@1 | Hit@5 | Hit@10 | MRR@10 | nDCG@5 | nDCG@10 | nDCG tasks |",
    "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
  ];
  for (const [mode, result] of Object.entries(report.modes)) {
    const s = result.summary;
    lines.push(
      s
        ? `| ${mode} | ${s.scored_tasks}/${s.planned_tasks} | ${s.hit_at_1_count}/${s.scored_tasks} | ${s.hit_at_5_count}/${s.scored_tasks} | ${s.hit_at_10_count}/${s.scored_tasks} | ${display(s.mrr_at_10)} | ${display(s.ndcg_at_5)} | ${display(s.ndcg_at_10)} | ${s.ndcg_tasks} |`
        : `| ${mode} | **Invalid experiment — aggregate quality withheld** | | | | | | | |`,
    );
  }
  lines.push(
    "",
    "## Per task",
    "",
    "| Task / mode | Status | First rank | RR@10 | nDCG@10 | Repeat ranks | Same rank / text | Raw |",
    "| --- | --- | --- | --- | --- | --- | --- | --- |",
  );
  for (const task of report.tasks) {
    lines.push(
      `| ${task.task_id} / ${task.mode} | ${task.status} | ${task.first_hit_rank ?? "N/A"} | ${display(task.rr_at_10)} | ${display(task.ndcg_at_10)} | ${task.repeat_ranks.join(", ")} | ${task.ranking_repeatable ?? "N/A"} / ${task.output_repeatable ?? "N/A"} | [response](${task.raw_path}) |`,
    );
  }
  lines.push(
    "",
    "## Categories",
    "",
    "| Mode / category | Scored / planned | Hit@10 | MRR@10 |",
    "| --- | --- | --- | --- |",
  );
  for (const [mode, result] of Object.entries(report.modes))
    for (const [category, s] of Object.entries(result.by_category ?? {}))
      lines.push(
        `| ${mode} / ${category} | ${s.scored_tasks}/${s.planned_tasks} | ${s.hit_at_10_count}/${s.scored_tasks} | ${display(s.mrr_at_10)} |`,
      );
  lines.push(
    "",
    "## Preparation and latency",
    "",
    "Index timing includes CLI startup and model loading/download where necessary; no index cache is restored. Individual MCP latencies, session-first-query flags and freshness values are in report.json/scores.jsonl. Five repetitions are insufficient to characterize tail latency.",
    "",
    "| Repository | Preparation | Full index seconds | MCP connect ms | Post-run integrity |",
    "| --- | --- | --- | --- | --- |",
  );
  for (const repo of report.repositories)
    lines.push(
      `| ${repo.repository} | ${repo.preparation_status} | ${display(repo.index_seconds)} | ${display(repo.mcp_connect_ms)} | ${repo.post_run_integrity ?? "not_verified"} |`,
    );
  lines.push(
    "",
    "Persisted chunks, vector hashes, file inventories and replayed scanner output are in stages/. Actual embedding inputs and preselection/fusion candidates are **not observed**; no embedding root cause is inferred from a miss. Ranking repeatability compares visible result identities, not hidden entity IDs.",
  );
  if (report.product_error_calls)
    lines.push(
      "",
      `Product error calls: **${report.product_error_calls}**. Reviewed questions receive zero for undelivered entries; CI fails operational integrity.`,
    );
  if (report.integrity_errors.length)
    lines.push(
      "",
      "## Invalid experiment",
      "",
      ...report.integrity_errors.map(
        (reason) => `- ${reason.replaceAll("\n", " ")}`,
      ),
    );
  return lines.join("\n") + "\n";
}

if (
  process.argv[1] &&
  import.meta.url === pathToFileURL(process.argv[1]).href
) {
  aggregate(process.argv[2])
    .then((report) => {
      if (!report.integrity_passed) process.exitCode = 1;
    })
    .catch((error) => {
      console.error(error);
      process.exitCode = 1;
    });
}
