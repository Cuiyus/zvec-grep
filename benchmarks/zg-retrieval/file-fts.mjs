import assert from "node:assert/strict";
import { lstat, mkdir, realpath, writeFile } from "node:fs/promises";
import { basename, dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { parseArgs } from "node:util";
import { runCli } from "./core/cli.mjs";
import { prepareCorpus, repositorySlug } from "./core/corpus.mjs";
import {
  fileHash,
  inside,
  objectHash,
  readJson,
  run,
  writeJson,
} from "./core/io.mjs";
import { relativePath } from "./core/response.mjs";
import {
  loadSuite,
  validateGoldSources,
  validateQaGoldSources,
} from "./core/suite.mjs";
import { indexSelectionArguments } from "./engines/zg/run.mjs";
import { loadPilot, targetsForTask } from "./expansion/datasets.mjs";
import { verifyPilotGoldSources } from "./expansion/gold.mjs";
import { QA_METRIC_FIELDS, scoreQaRetrieval } from "./metrics/entities.mjs";
import { scoreFileRetrieval } from "./metrics/files.mjs";
import { summarizeMeasurements } from "./metrics/measurements.mjs";
import { ndcgAtK } from "./metrics/ndcg.mjs";
import { rankingSignature } from "./metrics/repetitions.mjs";

const TEST_ARGS = ["--ignored", "--exact", "storage::file_fts_benchmark::run"];
const FTS_MODES = ["fragment_fts", "file_fts"];
const HYBRID_MODES = ["file_vector", "file_hybrid"];
const REPETITIONS = [1, 2, 3, 4, 5];
const LIMIT = 10;

async function sweqa() {
  const suite = await loadSuite({ embedding: "local" });
  return {
    model: suite.protocol.model,
    identity: {
      source: suite.identity.source,
      file_gold: suite.identity.file_gold,
      qa_gold: suite.qa_gold,
    },
    groups: suite.lock.repositories.map((repo) => ({
      id: repositorySlug(repo.repository),
      repo,
      commit: repo.commit,
      indexArgs: indexSelectionArguments(suite.protocol),
      tasks: suite.lock.tasks
        .filter((task) => task.repository === repo.repository)
        .map((task) => ({
          id: task.task_id,
          query: task.query,
          targets: suite.file_gold[task.task_id].targets,
          qa: suite.qa_pairs[task.task_id],
          source: task,
        })),
      async verify(corpus, root, tasks) {
        assert.equal(await prepareCorpus(repo, corpus), root);
        const originals = tasks.map(({ source }) => source);
        await validateGoldSources(root, originals, suite.gold);
        await validateQaGoldSources(root, originals, suite.qa_pairs);
      },
    })),
  };
}

async function pilotSuite(name) {
  const pilot = await loadPilot(name, { embedding: "local" });
  function group(id, subdirectory, expectedFiles, tasks, repo) {
    const extension = repo
      ? pilot.config.fileExtensions[tasks[0].language]
      : "md";
    return {
      id,
      subdirectory,
      expectedFiles,
      repo,
      commit: repo?.commit,
      indexArgs: ["--max-filesize", "1000000", "--iglob", `*.${extension}`],
      tasks: tasks.map((task) => ({
        id: task.id,
        query: task.query,
        targets: targetsForTask(pilot, task),
      })),
      async verify(corpus, root, selectedTasks) {
        if (repo) assert.equal(await prepareCorpus(repo, corpus), root);
        await verifyPilotGoldSources(pilot.gold, {
          root,
          tasks: selectedTasks,
        });
      },
    };
  }
  let groups;
  if (name === "beir") {
    groups = pilot.lock.datasets.flatMap(({ id, corpus_documents }) => {
      const tasks = pilot.lock.tasks.filter((task) => task.dataset === id);
      return id === "arguana"
        ? tasks.map((task, index) =>
            group(
              `arguana-${index + 1}`,
              `arguana-queries/${index + 1}`,
              corpus_documents - 1,
              [task],
            ),
          )
        : [group(id, id, corpus_documents, tasks)];
    });
  } else if (name === "quarry") {
    groups = pilot.lock.tasks.map((task, index) => {
      const id = `task-${index + 1}`;
      const repo = {
        repository: `${task.repository}/${id}`,
        url: `https://github.com/${task.repository}.git`,
        commit: task.revision,
      };
      return group(
        id,
        repositorySlug(repo.repository),
        undefined,
        [task],
        repo,
      );
    });
  } else {
    groups = [
      group(
        "duretrieval",
        "duretrieval",
        pilot.lock.corpus_documents,
        pilot.lock.tasks,
      ),
    ];
  }
  return {
    model: pilot.model,
    identity: { source: objectHash(pilot.lock), gold: pilot.gold.identity },
    groups,
  };
}

function selectGroups(groups, rawTasks) {
  if (rawTasks === undefined) return groups;
  const requested = new Set(rawTasks.split(","));
  const selected = groups
    .map((group) => ({
      ...group,
      tasks: group.tasks.filter(({ id }) => requested.delete(id)),
    }))
    .filter(({ tasks }) => tasks.length);
  assert.equal(
    requested.size,
    0,
    `Unknown tasks: ${[...requested].join(", ")}`,
  );
  return selected;
}

function scoreCall(items, targets) {
  const score = scoreFileRetrieval(items, targets);
  const ranks = score.target_ranks.filter((rank) => rank !== null);
  return {
    ...score,
    ...Object.fromEntries(
      [1, 5, 10].map((k) => [
        `recall_at_${k}`,
        ranks.filter((rank) => rank <= k).length / targets.length,
      ]),
    ),
    ndcg_at_10: ndcgAtK(ranks, targets.length, LIMIT),
  };
}

function recordsForGroup(raw, group, modes) {
  for (const field of ["indexed_files", "files", "fragments"])
    assert.ok(
      Number.isSafeInteger(raw[field]) && raw[field] > 0,
      `Invalid ${field} count`,
    );
  if (group.expectedFiles !== undefined)
    assert.equal(
      raw.indexed_files,
      group.expectedFiles,
      "Indexed file count differs from the source lock",
    );
  assert.ok(
    raw.files <= raw.indexed_files,
    "Searchable files exceed indexed files",
  );
  assert.ok(Array.isArray(raw.task_results), "Missing native call records");
  const tasks = new Map(group.tasks.map((task) => [task.id, task]));
  const calls = new Map();
  for (const row of raw.task_results) {
    const task = tasks.get(row.id);
    const key = `${row.id}/${row.mode}/${row.repetition}`;
    assert.ok(
      task && modes.includes(row.mode) && REPETITIONS.includes(row.repetition),
      `Unexpected call ${key}`,
    );
    assert.ok(!calls.has(key), `Duplicate call ${key}`);
    assert.ok(
      Number.isFinite(row.elapsed_ms) && row.elapsed_ms >= 0,
      `Invalid latency ${key}`,
    );
    assert.ok(
      Array.isArray(row.items) && row.items.length <= LIMIT,
      `Invalid result limit ${key}`,
    );
    for (const [index, item] of row.items.entries()) {
      assert.equal(item.rank, index + 1, `Invalid native rank ${key}`);
      assert.ok(
        relativePath(item.path) && Number.isFinite(item.score),
        `Invalid result ${key}`,
      );
    }
    if (row.mode !== "fragment_fts")
      assert.equal(
        new Set(row.items.map(({ path }) => path)).size,
        row.items.length,
        `Duplicate file result ${key}`,
      );
    calls.set(key, {
      ...row,
      file: scoreCall(row.items, task.targets),
      qa_file: task.qa ? scoreQaRetrieval(row.items, task.qa).file : null,
    });
  }
  return group.tasks.flatMap((task) =>
    modes.map((mode) => {
      const repeats = REPETITIONS.map((n) => {
        const key = `${task.id}/${mode}/${n}`;
        assert.ok(calls.has(key), `Missing call ${key}`);
        return calls.get(key);
      });
      return {
        id: task.id,
        mode,
        stable_top_10_files:
          new Set(repeats.map(({ items }) => rankingSignature(items))).size ===
          1,
        mean_unique_files: average(
          repeats.map(
            ({ items }) => new Set(items.map(({ path }) => path)).size,
          ),
        ),
        calls: repeats,
      };
    }),
  );
}

const average = (values) =>
  values.length
    ? values.reduce((sum, value) => sum + value, 0) / values.length
    : null;

function quality(observations, key) {
  const scored = observations.filter(({ calls }) =>
    calls.every((call) => call[key] !== null),
  );
  return {
    scored_tasks: scored.length,
    ...Object.fromEntries(
      QA_METRIC_FIELDS.map((field) => [
        field === "rr_at_10" ? "mrr_at_10" : field,
        average(
          scored.map(({ calls }) =>
            average(calls.map((call) => call[key][field])),
          ),
        ),
      ]),
    ),
  };
}

function summarize(observations, modes, qa) {
  return Object.fromEntries(
    modes.map((mode) => {
      const selected = observations.filter((row) => row.mode === mode);
      const timing = summarizeMeasurements(
        selected.flatMap(({ calls }) =>
          calls.map((call) => ({
            execution_status: "success",
            latency_ms: call.elapsed_ms,
          })),
        ),
      );
      return [
        mode,
        {
          file: quality(selected, "file"),
          ...(qa ? { qa_file: quality(selected, "qa_file") } : {}),
          stable_top_10_files: selected.filter((row) => row.stable_top_10_files)
            .length,
          mean_unique_files: average(
            selected.map((row) => row.mean_unique_files),
          ),
          latency_ms_mean: timing.latency_ms_mean,
          latency_ms_p50: timing.latency_ms_p50,
          latency_sample_count: timing.latency_sample_count,
        },
      ];
    }),
  );
}

function markdown(report) {
  const percent = (value) =>
    value === null ? "N/A" : `${(100 * value).toFixed(2)}%`;
  const ms = (value) => (value === null ? "N/A" : value.toFixed(3));
  const rows = Object.entries(report.summary).flatMap(([mode, summary]) => {
    const labels = [["File Gold", summary.file]];
    if (summary.qa_file)
      labels.push(["Diagnostic QA File Gold", summary.qa_file]);
    return labels.map(([label, scores]) =>
      [
        mode,
        label,
        `${scores.scored_tasks}/${report.planned_tasks}`,
        ...[
          "hit_at_1",
          "hit_at_5",
          "hit_at_10",
          "recall_at_1",
          "recall_at_5",
          "recall_at_10",
          "mrr_at_10",
          "ndcg_at_10",
        ].map((field) => percent(scores[field])),
        `${summary.stable_top_10_files}/${report.completed_tasks}`,
        ms(summary.mean_unique_files),
        ms(summary.latency_ms_p50),
      ].join(" | "),
    );
  });
  return [
    `# File FTS retrieval experiment (${report.suite})`,
    "",
    `${report.completed_tasks}/${report.planned_tasks} questions completed. Each score averages five calls per question at native Top 10, then averages questions equally.`,
    "",
    "| Mode | Labels | Scored questions | Hit@1 | Hit@5 | Hit@10 | Recall@1 | Recall@5 | Recall@10 | MRR@10 | nDCG@10 | Stable Top 10 paths | Mean unique files | P50 query ms |",
    "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ...rows.map((row) => `| ${row} |`),
    "",
    "Timings cover the native query paths in the experiment and exclude index construction. The reports retain unsuccessful groups and their logs.",
    "",
    ...report.groups
      .filter(({ error }) => error)
      .map(({ id, error }) => `- ${id} failed. ${error.split("\n")[0]}`),
    "",
  ].join("\n");
}

async function runGroup(
  group,
  { corpus, testBin, zg, output, modelCache, model, modes, hybrid },
) {
  const root =
    zg && group.repo
      ? await prepareCorpus(group.repo, corpus)
      : await realpath(join(corpus, group.subdirectory ?? group.id));
  assert.ok(
    !inside(root, output) && !inside(root, modelCache),
    "Output and model cache must be outside the source root",
  );
  await group.verify(corpus, root, group.tasks);
  const directory = join(output, group.id);
  const indexPath = join(root, ".zvec-grep/index");
  if (zg) {
    await buildIndex(
      zg,
      root,
      indexPath,
      directory,
      model,
      modelCache,
      group.indexArgs,
    );
  }
  const index = await realpath(indexPath);
  const input = join(directory, "input.json");
  const observations = join(directory, "observations.json");
  await writeJson(input, {
    root,
    index,
    collection: join(directory, "file-index"),
    model_cache: modelCache,
    model,
    hybrid,
    tasks: group.tasks.map(({ id, query }) => ({ id, query })),
    output: observations,
  });
  try {
    const log = await run(testBin, TEST_ARGS, {
      cwd: root,
      env: { ...process.env, ZG_FILE_FTS_BENCH_INPUT: input },
      timeout: 2_400_000,
    });
    await writeJson(join(directory, "native-log.json"), log);
  } catch (error) {
    await writeJson(
      join(directory, "native-log.json"),
      error.result ?? { error: error.message },
    );
    const detail = error.result?.stdout
      .split("\n")
      .find((line) => line.startsWith("Error: "));
    if (detail) throw new Error(detail, { cause: error });
    throw error;
  }
  const raw = await readJson(observations);
  return {
    indexed_files: raw.indexed_files,
    files: raw.files,
    fragments: raw.fragments,
    ...(raw.file_index_ms === undefined
      ? {}
      : { file_index_ms: raw.file_index_ms }),
    trials: recordsForGroup(raw, group, modes),
  };
}

async function buildIndex(
  zg,
  root,
  indexPath,
  directory,
  model,
  modelCache,
  selection,
) {
  try {
    await lstat(indexPath);
    throw new Error(`Fresh index required for --zg: ${indexPath}`);
  } catch (error) {
    if (error.code !== "ENOENT") throw error;
  }
  const runtimeHome = join(directory, "indexer-home");
  await mkdir(runtimeHome, { recursive: true });
  const args = [
    "--index",
    root,
    "--mode",
    "direct",
    "--embedding",
    model,
    "--device",
    "cpu",
    "--model-cache",
    modelCache,
    ...selection,
    "--debug",
  ];
  const log = join(directory, "index-log.json");
  console.log(`Indexing ${root}`);
  try {
    const result = await run(zg, args, {
      cwd: root,
      env: {
        ...process.env,
        ZVEC_GREP_HOME: runtimeHome,
        NO_COLOR: "1",
        FORCE_COLOR: "0",
      },
      timeout: 18_000_000,
    });
    await writeJson(log, { args, runtime_home: runtimeHome, ...result });
  } catch (error) {
    await writeJson(log, {
      args,
      runtime_home: runtimeHome,
      ...(error.result ?? { error: error.message }),
    });
    throw error;
  }
}

async function main() {
  const { values } = parseArgs({
    options: {
      corpus: { type: "string" },
      "test-bin": { type: "string" },
      zg: { type: "string" },
      "model-cache": { type: "string" },
      output: { type: "string" },
      help: { type: "boolean" },
      suite: { type: "string", default: "sweqa" },
      tasks: { type: "string" },
      hybrid: { type: "boolean", default: false },
    },
  });
  if (values.help) {
    console.log(`Usage: node benchmarks/zg-retrieval/file-fts.mjs \\
  --corpus PATH --test-bin PATH --model-cache PATH --output NEW_DIR \\
  [--zg PATH] [--suite sweqa|quarry|beir|duretrieval] [--tasks ID,ID] [--hybrid]

By default the runner reads existing corpus indexes from .zvec-grep/index.
Pass --zg to build fresh indexes with a native CLI and the suite's file selection.
It prepares pinned SWE-QA20 and Quarry checkouts when needed. Supply pre-materialized
BEIR and DuRetrieval corpora produced by the expansion suite.
The default tests fragment and file FTS. --hybrid also tests file vector and Hybrid.`);
    return;
  }
  for (const option of ["corpus", "test-bin", "model-cache", "output"])
    assert.ok(values[option], `Pass --${option}`);
  const { suite, hybrid } = values;
  assert.ok(
    ["sweqa", "beir", "duretrieval", "quarry"].includes(suite),
    `Unknown suite ${suite}`,
  );
  const loaded = await (suite === "sweqa" ? sweqa() : pilotSuite(suite));
  const groups = selectGroups(loaded.groups, values.tasks);
  const modes = hybrid ? [...FTS_MODES, ...HYBRID_MODES] : FTS_MODES;
  if (values.zg && groups.every(({ repo }) => repo))
    await mkdir(resolve(values.corpus), { recursive: true });
  const [corpus, testBin, modelCache, parent, zg] = await Promise.all([
    realpath(values.corpus),
    realpath(values["test-bin"]),
    realpath(values["model-cache"]),
    realpath(dirname(resolve(values.output))),
    values.zg ? realpath(values.zg) : null,
  ]);
  const output = join(parent, basename(resolve(values.output)));
  assert.ok(
    !inside(corpus, output),
    "Output must be outside the prepared corpus",
  );
  await mkdir(output);
  const manifest = {
    schema_version: 1,
    suite,
    source: loaded.identity,
    corpus,
    model: loaded.model,
    model_cache: modelCache,
    engine: { path: testBin, sha256: await fileHash(testBin), args: TEST_ARGS },
    ...(zg ? { indexer: { path: zg, sha256: await fileHash(zg) } } : {}),
    scorer_sha256: await fileHash(fileURLToPath(import.meta.url)),
    modes,
    limit: LIMIT,
    repetitions: REPETITIONS,
    planned_tasks: groups.reduce((sum, group) => sum + group.tasks.length, 0),
  };
  await writeJson(join(output, "run.json"), manifest);
  const results = [];
  const observations = [];
  for (const group of groups) {
    const metadata = {
      id: group.id,
      commit: group.commit,
      tasks: group.tasks.map(({ id }) => id),
    };
    try {
      console.log(`Searching ${group.id}`);
      const { trials, ...index } = await runGroup(group, {
        corpus,
        testBin,
        zg,
        output,
        modelCache,
        model: loaded.model,
        modes,
        hybrid,
      });
      observations.push(...trials);
      results.push({ ...metadata, ...index });
    } catch (error) {
      results.push({ ...metadata, error: error.message });
      console.error(`${group.id}: ${error.message}`);
    }
  }
  const report = {
    ...manifest,
    completed_tasks: observations.length / modes.length,
    groups: results,
    summary: summarize(observations, modes, suite === "sweqa"),
  };
  await writeJson(join(output, "scores.json"), observations);
  await writeJson(join(output, "report.json"), report);
  await writeFile(join(output, "report.md"), markdown(report));
  console.log(`Report: ${join(output, "report.md")}`);
  if (report.completed_tasks !== report.planned_tasks) process.exitCode = 1;
}

runCli(import.meta.url, main);
