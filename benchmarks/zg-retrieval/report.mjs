import assert from "node:assert/strict";
import { readdir, readFile, writeFile } from "node:fs/promises";
import { join, resolve, isAbsolute, extname } from "node:path";
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
import { scoreSembleMetric } from "./semble-metrics.mjs";
import {
  FILE_RETRIEVAL_CONTRACT,
  fileRetrievalForRow,
  summarizeFileRetrieval,
} from "./file-retrieval-metrics.mjs";

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

export function summarizeSembleOfficial(rows) {
  const metrics = ["ndcg_at_5", "ndcg_at_10"];
  const mean = (values) =>
    Object.fromEntries(
      metrics.map((key) => [key, average(values.map((value) => value[key]))]),
    );
  const byRepository = Object.fromEntries(
    [...new Set(rows.map((row) => row.repository))].sort().map((repository) => {
      const selected = rows.filter((row) => row.repository === repository);
      const languages = [...new Set(selected.map((row) => row.language))];
      assert.equal(
        languages.length,
        1,
        "one repository must have one benchmark language",
      );
      return [
        repository,
        {
          language: languages[0],
          query_count: selected.length,
          ...mean(selected.map((row) => row.semble_official)),
        },
      ];
    }),
  );
  const byLanguage = Object.fromEntries(
    [...new Set(rows.map((row) => row.language))].sort().map((language) => {
      const selected = Object.values(byRepository).filter(
        (repo) => repo.language === language,
      );
      return [
        language,
        { repository_count: selected.length, ...mean(selected) },
      ];
    }),
  );
  return {
    dataset:
      "SWE-QA accepted-file projection; not the original Semble benchmark dataset",
    metric:
      "Semble official first-target-rank binary nDCG; all projected targets; no complementary-group substitution",
    quality_repetition: 5,
    query_count: rows.length,
    repository_count: Object.keys(byRepository).length,
    language_count: Object.keys(byLanguage).length,
    query_mean: mean(rows.map((row) => row.semble_official)),
    repository_macro: mean(Object.values(byRepository)),
    language_macro: mean(Object.values(byLanguage)),
    by_repository: byRepository,
    by_language: byLanguage,
  };
}

export function markdownSembleOfficialTable(
  modes,
  { title = "Semble official metric — SWE-QA accepted-file projection" } = {},
) {
  const lines = [
    "",
    `## ${title}`,
    "",
    "The algorithm and aggregation follow Semble; these are scores on this SWE-QA projection, not scores on Semble's original dataset. Quality uses the fifth native result list. Each distinct accepted file is one target; bridge-only files are excluded. No grouped-anchor nDCG is substituted.",
    "",
    "| Mode / aggregation | Queries | Repositories | Languages | nDCG@5 | nDCG@10 |",
    "| --- | --- | --- | --- | --- | --- |",
  ];
  for (const [mode, entry] of Object.entries(modes)) {
    const official = entry.semble_official;
    if (!official) {
      lines.push(
        `| ${mode} | **Invalid experiment — official aggregate withheld** | | | | |`,
      );
      continue;
    }
    for (const [key, label] of [
      ["query_mean", "query mean"],
      ["repository_macro", "repository macro (JSON summary)"],
      ["language_macro", "language macro (terminal Avg)"],
    ])
      lines.push(
        `| ${mode} / ${label} | ${official.query_count} | ${official.repository_count} | ${official.language_count} | ${display(official[key].ndcg_at_5)} | ${display(official[key].ndcg_at_10)} |`,
      );
  }
  lines.push(
    "",
    "Repository means weight their queries equally; repository macro weights repositories equally; language macro first averages repositories within each language, then weights languages equally. This suite contains only Python repositories, so its repository and language macros coincide.",
  );
  return lines;
}

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

/** Compare only public retrieval identities; preview-dependent text is deliberately excluded. */
export function summarizePreviewPairs(
  observations,
  {
    primaryPreview = "short",
    comparisonPreview = "full",
    qualityRepetition = 5,
  } = {},
) {
  const identity = (row) =>
    objectHash(
      row.items.map((item) => ({
        rank: item.rank,
        path: item.path,
        range: item.range,
        matched_range: item.matched_range ?? null,
        matched_by: item.matched_by ?? null,
      })),
    );
  const keyed = new Map();
  for (const row of observations) {
    if (![primaryPreview, comparisonPreview].includes(row.preview)) continue;
    const key = `${row.task_id}/${row.mode}/${row.repetition}`;
    if (!keyed.has(key)) keyed.set(key, {});
    keyed.get(key)[row.preview] = row;
  }
  const pairs = [...keyed.values()].map((pair) => {
    const primary = pair[primaryPreview],
      comparison = pair[comparisonPreview];
    const row = primary ?? comparison;
    const valid = [primary, comparison].every(
      (entry) =>
        entry?.execution_status === "success" &&
        entry.status !== "harness_invalid" &&
        entry.semble_official != null &&
        Array.isArray(entry.items),
    );
    return {
      task_id: row.task_id,
      mode: row.mode,
      repetition: row.repetition,
      quality_observation: row.repetition === qualityRepetition,
      status: valid ? "compared" : "unavailable",
      ranking_equal: valid ? identity(primary) === identity(comparison) : null,
      official_ndcg_equal: valid
        ? ["ndcg_at_5", "ndcg_at_10"].every(
            (key) =>
              primary.semble_official[key] === comparison.semble_official[key],
          )
        : null,
      primary_first_anchor_rank: primary?.first_hit_rank ?? null,
      comparison_first_anchor_rank: comparison?.first_hit_rank ?? null,
      primary_output_bytes: primary?.visible_output_bytes ?? null,
      comparison_output_bytes: comparison?.visible_output_bytes ?? null,
    };
  });
  const counts = (rows) => ({
    observed_pairs: rows.length,
    compared_pairs: rows.filter((row) => row.status === "compared").length,
    same_ranking_pairs: rows.filter((row) => row.ranking_equal === true).length,
    different_ranking_pairs: rows.filter((row) => row.ranking_equal === false)
      .length,
    same_official_ndcg_pairs: rows.filter(
      (row) => row.official_ndcg_equal === true,
    ).length,
    different_official_ndcg_pairs: rows.filter(
      (row) => row.official_ndcg_equal === false,
    ).length,
  });
  return {
    primary_preview: primaryPreview,
    comparison_preview: comparisonPreview,
    identity_scope:
      "ordered rank/path/range/matched range/matched-by; excludes preview-dependent source locations, text and outline; hidden entity IDs unavailable",
    interpretation:
      "report-only presentation control; a ranking difference is an uncontrolled retrieval difference, not proof that preview changed retrieval; artifact validity is audited separately",
    ...counts(pairs),
    quality: counts(pairs.filter((row) => row.quality_observation)),
    pairs,
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
          const files = await readJson(
            join(runDirectory, `stages/${phase}/files.json`),
          );
          assert.ok(
            Array.isArray(files) && files.length > 0,
            "code-only index contains no files",
          );
          const selection = suite.protocol.index_selection;
          const extensions = new Set(selection.code_extensions);
          for (const file of files) {
            assert.ok(
              extensions.has(extname(file.relativePath).toLowerCase()),
              `non-code extension entered index: ${file.relativePath}`,
            );
            assert.ok(
              Number.isFinite(file.sizeBytes) &&
                file.sizeBytes >= 0 &&
                file.sizeBytes <= selection.max_file_size_bytes,
              `oversized or invalid file entered index: ${file.relativePath}`,
            );
          }
          assert.deepEqual(
            manifest.index_selection_audit,
            {
              content: "code",
              max_file_size_bytes: selection.max_file_size_bytes,
              indexed_files: files.length,
              verified: true,
            },
            "index selection audit differs from recorded files",
          );
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
    const previewSet = suite.protocol.previews;
    if (objectHash(manifest.previews ?? null) !== objectHash(previewSet))
      invalid.push("preview selection differs from frozen protocol");
    const plannedCount =
      manifest.tasks.length *
      modeSet.length *
      previewSet.length *
      suite.protocol.repetitions;
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
    if (
      objectHash(manifest.tasks) !==
      objectHash(
        suite.lock.tasks
          .filter(
            (task) =>
              task.repository === manifest.repository &&
              expected.includes(task.task_id),
          )
          .map((task) => task.task_id),
      )
    )
      invalid.push(
        "repository task coverage/order differs from frozen selection",
      );
    const plannedOrder = modeSet.flatMap((mode) =>
      manifest.tasks.flatMap((task_id) =>
        previewSet.flatMap((preview) =>
          Array.from({ length: suite.protocol.repetitions }, (_, index) => ({
            task_id,
            mode,
            preview,
            repetition: index + 1,
          })),
        ),
      ),
    );
    if (
      objectHash(
        calls.map(({ task_id, mode, preview, repetition }) => ({
          task_id,
          mode,
          preview,
          repetition,
        })),
      ) !== objectHash(plannedOrder)
    )
      invalid.push(
        "call order differs from mode-task-preview-repetition protocol",
      );
    for (const call of calls) {
      const task = suite.lock.tasks.find(
        (task) => task.task_id === call.task_id,
      );
      if (!task || !manifest.tasks.includes(call.task_id)) {
        errors.push(`unknown/unplanned task ${call.task_id}`);
        continue;
      }
      const key = `${call.task_id}/${call.mode}/${call.preview}/${call.repetition}`;
      if (seen.has(key)) {
        errors.push(`duplicate call: ${key}`);
        continue;
      }
      seen.add(key);
      const callInvalid = [...invalid];
      if (
        !modeSet.includes(call.mode) ||
        !previewSet.includes(call.preview) ||
        !Number.isInteger(call.repetition) ||
        call.repetition < 1 ||
        call.repetition > suite.protocol.repetitions
      )
        callInvalid.push("unplanned mode/preview/repetition");
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
        preview: call.preview,
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
      let visibleOutputBytes = null;
      try {
        assert.equal(
          call.raw_path,
          `raw/${task.task_slug}-${call.mode}-${call.preview}-${call.repetition}.json`,
          "raw response reused or mapped to the wrong call",
        );
        const path = join(runDirectory, call.raw_path);
        assert.ok(inside(runDirectory, path), "raw response path escapes run");
        assert.equal(
          await fileHash(path),
          call.raw_sha256,
          "raw response changed since capture",
        );
        const response = await readJson(path);
        if (
          Array.isArray(response?.content) &&
          response.content.every(
            (block) => block.type === "text" && typeof block.text === "string",
          )
        )
          visibleOutputBytes = Buffer.byteLength(
            response.content.map((block) => block.text).join("\n"),
            "utf8",
          );
        score = scoreResponse(response, suite.gold[call.task_id]);
      } catch (error) {
        callInvalid.push(error.message);
        score = {};
      }
      if (callInvalid.length) score = invalidate(score, callInvalid.join("; "));
      observations.push({
        ...call,
        ...score,
        visible_output_bytes: visibleOutputBytes,
        language: suite.semble_gold[call.task_id].language,
        semble_official:
          score.status === "harness_invalid"
            ? null
            : {
                targets: suite.semble_gold[call.task_id].targets,
                ...scoreSembleMetric(
                  score.items ?? [],
                  suite.semble_gold[call.task_id].targets,
                ),
              },
        category: task.category,
        repository: task.repository,
        raw_path: `${runDirectory.slice(directory.length + 1)}/${call.raw_path}`,
      });
    }
    for (const taskId of manifest.tasks) {
      for (const mode of modeSet) {
        for (const preview of previewSet) {
          for (
            let repetition = 1;
            repetition <= suite.protocol.repetitions;
            repetition++
          ) {
            if (!seen.has(`${taskId}/${mode}/${preview}/${repetition}`))
              errors.push(
                `missing call: ${taskId}/${mode}/${preview}/${repetition}`,
              );
          }
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
    for (const preview of suite.protocol.previews)
      if (
        !observations.some(
          (row) =>
            row.task_id === id &&
            row.mode === "hybrid" &&
            row.preview === preview &&
            row.repetition === suite.protocol.quality_repetition,
        )
      )
        errors.push(`missing quality observation: ${id}/${preview}`);
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
  for (const row of observations) row.file_retrieval = fileRetrievalForRow(row);
  const quality = observations
    .filter((row) => row.repetition === suite.protocol.quality_repetition)
    .map((row) => {
      const repeats = observations.filter(
        (item) =>
          item.task_id === row.task_id &&
          item.mode === row.mode &&
          item.preview === row.preview,
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
  const previewReports = Object.fromEntries(
    suite.protocol.previews.map((preview) => {
      const tasks = quality.filter((row) => row.preview === preview);
      return [
        preview,
        {
          modes: Object.fromEntries(
            modes.map((mode) => {
              const rows = tasks.filter((row) => row.mode === mode);
              const outputBytes = rows
                .filter(
                  (row) =>
                    row.execution_status === "success" &&
                    row.status !== "harness_invalid",
                )
                .map((row) => row.visible_output_bytes)
                .filter((value) => typeof value === "number");
              return [
                mode,
                {
                  summary: complete ? summarize(rows) : null,
                  file_retrieval: complete
                    ? summarizeFileRetrieval(rows)
                    : null,
                  semble_official: complete
                    ? summarizeSembleOfficial(rows)
                    : null,
                  by_category: complete
                    ? Object.fromEntries(
                        ["what", "where", "how", "why"].map((category) => [
                          category,
                          summarize(
                            rows.filter((row) => row.category === category),
                          ),
                        ]),
                      )
                    : null,
                  ranking_repeatable_tasks: rows.filter(
                    (row) => row.ranking_repeatable === true,
                  ).length,
                  output_repeatable_tasks: rows.filter(
                    (row) => row.output_repeatable === true,
                  ).length,
                  output_size: {
                    unit: "UTF-8 bytes of the public text response, not model tokens",
                    measured_quality_responses: outputBytes.length,
                    quality_mean_bytes: average(outputBytes),
                    quality_total_bytes: outputBytes.reduce(
                      (sum, value) => sum + value,
                      0,
                    ),
                  },
                },
              ];
            }),
          ),
          tasks,
        },
      ];
    }),
  );
  const report = {
    schema_version: 2,
    file_retrieval_contract: FILE_RETRIEVAL_CONTRACT,
    primary_preview: suite.protocol.primary_preview,
    quality_repetition: suite.protocol.quality_repetition,
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
      "quality repetition 5 separately for each preview; file Hit and MRR use equal weight per original question and the same frozen targets as Semble official nDCG; nDCG has query/repository/language means; legacy anchor diagnostics retain their declared subset",
    modes: previewReports[suite.protocol.primary_preview].modes,
    previews: previewReports,
    paired_preview_comparison: summarizePreviewPairs(observations, {
      primaryPreview: suite.protocol.primary_preview,
      comparisonPreview: "full",
      qualityRepetition: suite.protocol.quality_repetition,
    }),
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
  const previewModes = Object.fromEntries(
    Object.entries(
      report.previews ?? { short: { modes: report.modes } },
    ).flatMap(([preview, entry]) =>
      Object.entries(entry.modes).map(([mode, value]) => [
        `${mode} / ${preview}`,
        value,
      ]),
    ),
  );
  const previewComparison = [
    "",
    "## Short/full comparison",
    "",
    "Both previews use the same frozen index, MCP session, original query and native top-10 limit. Full displays all stored source content of each returned retrieval unit, not the entire file; outline-only units remain outlines. The two arms are scored separately, with the fifth call per arm used for quality. Each row still contains the same original questions.",
    "",
    "| Mode / preview | Official nDCG@5 | Official nDCG@10 | File Hit@1 | File Hit@5 | File Hit@10 | File MRR@10 | Mean output bytes |",
    "| --- | --- | --- | --- | --- | --- | --- | --- |",
  ];
  for (const [label, entry] of Object.entries(previewModes)) {
    const s = entry.file_retrieval,
      official = entry.semble_official?.repository_macro;
    previewComparison.push(
      s && official
        ? `| ${label} | ${display(official.ndcg_at_5)} | ${display(official.ndcg_at_10)} | ${s.hit_at_1_count}/${s.scored_tasks} | ${s.hit_at_5_count}/${s.scored_tasks} | ${s.hit_at_10_count}/${s.scored_tasks} | ${display(s.mrr_at_10)} | ${display(entry.output_size?.quality_mean_bytes)} |`
        : `| ${label} | **Invalid experiment — aggregate quality withheld** | | | | | | |`,
    );
  }
  previewComparison.push(
    "",
    "Official nDCG uses the repository macro; file Hit/MRR uses equal weight per scored original question. All headline metrics use the same frozen accepted-file targets and upstream path matching, preserve native chunk ranks, and ignore source/outline visibility. File Hit@K is 1 if the first matching file is within K; RR@10 is 1/r for its first native rank r, or 0 for a Top-10 miss; MRR is the query mean including misses and product errors. These measure file localization, not sufficient answer evidence. Output size is UTF-8 bytes of the public MCP text, not a model token estimate.",
  );
  if (report.paired_preview_comparison) {
    const pair = report.paired_preview_comparison;
    previewComparison.push(
      "",
      `Paired retrieval identities: **${pair.same_ranking_pairs}/${pair.compared_pairs}** equal across all successful paired repetitions; quality repetition: **${pair.quality.same_ranking_pairs}/${pair.quality.compared_pairs}**. Official nDCG is equal in **${pair.same_official_ndcg_pairs}/${pair.compared_pairs}** paired repetitions. Pairs unavailable for comparison: **${pair.observed_pairs - pair.compared_pairs}**.`,
      "",
      "Pairing compares ordered rank, path, range, matched range and match type. Source text, source locations and outline text are excluded because the preview is expected to change them. Hidden entity IDs are unavailable. Any ranking mismatch is reported as an uncontrolled retrieval difference; it does not by itself invalidate the saved experiment.",
    );
    if (pair.different_ranking_pairs)
      previewComparison.push(
        "",
        `**Uncontrolled ranking differences: ${pair.different_ranking_pairs} paired repetitions.** Inspect paired_preview_comparison.pairs in report.json before attributing anchor changes solely to source visibility.`,
      );
  }
  const lines = [
    "# zg Retrieval-only — SWE-QA original queries",
    "",
    `Scope: **${report.scope}**. Calls: **${report.observed_calls}**. Integrity: **${report.integrity_passed ? "PASS" : "FAIL"}**.`,
    "",
    "Quality uses repetition 5; five repeats measure stability, not five independent questions. File Hit/MRR and Semble official file-target nDCG are the retrieval metrics. Legacy strict source-anchor Hit/MRR and grouped nDCG are output-visibility diagnostics, not retrieval or answer accuracy. Quality thresholds are report-only.",
    ...previewComparison,
    ...markdownSembleOfficialTable(previewModes),
    "",
    "## Legacy strict-anchor visibility diagnostics",
    "",
    "These historical scores mix native retrieval, output rendering and agent-authored anchor selection. They are retained for diagnosis and continuity, not as a cross-tool retrieval quality score. Grouped anchor nDCG uses only reviewed complementary groups (12 of the full 20-question suite).",
    "",
    "| Mode | Scored / planned | Hit@1 | Hit@5 | Hit@10 | MRR@10 | nDCG@5 | nDCG@10 | nDCG tasks |",
    "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
  ];
  for (const [mode, result] of Object.entries(previewModes)) {
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
    "| Task / mode / preview | Official nDCG@5 / @10 | File first rank / RR@10 | Official target ranks | Anchor status / first rank | Anchor RR@10 / grouped nDCG@10 | Repeat anchor ranks | Same rank / text | Raw |",
    "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
  );
  for (const task of report.tasks) {
    lines.push(
      `| ${task.task_id} / ${task.mode} / ${task.preview ?? report.primary_preview ?? "short"} | ${display(task.semble_official?.ndcg_at_5)} / ${display(task.semble_official?.ndcg_at_10)} | ${task.file_retrieval?.first_hit_rank ?? "N/A"} / ${display(task.file_retrieval?.rr_at_10)} | ${task.semble_official?.target_ranks.map((rank) => rank ?? "not found").join(", ") ?? "N/A"} | ${task.status} / ${task.first_hit_rank ?? "N/A"} | ${display(task.rr_at_10)} / ${display(task.ndcg_at_10)} | ${task.repeat_ranks.join(", ")} | ${task.ranking_repeatable ?? "N/A"} / ${task.output_repeatable ?? "N/A"} | [response](${task.raw_path}) |`,
    );
  }
  lines.push(
    "",
    "## Legacy anchor diagnostics by category",
    "",
    "| Mode / category | Scored / planned | Hit@10 | MRR@10 |",
    "| --- | --- | --- | --- |",
  );
  for (const [mode, result] of Object.entries(previewModes))
    for (const [category, s] of Object.entries(result.by_category ?? {}))
      lines.push(
        `| ${mode} / ${category} | ${s.scored_tasks}/${s.planned_tasks} | ${s.hit_at_10_count}/${s.scored_tasks} | ${display(s.mrr_at_10)} |`,
      );
  lines.push(
    "",
    "## Preparation and latency",
    "",
    "Index timing includes CLI startup and model loading/download where necessary; no index cache is restored. Individual MCP latencies, session-first-query flags and freshness values are in report.json/scores.jsonl. Each query runs five short calls followed by five full calls; cache/order effects prevent treating this as an unbiased short/full speed comparison. Five repetitions are insufficient to characterize tail latency.",
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
