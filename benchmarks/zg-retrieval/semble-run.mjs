import assert from "node:assert/strict";
import { appendFile, mkdir, readFile, lstat, realpath } from "node:fs/promises";
import { createWriteStream } from "node:fs";
import { platform, arch, cpus } from "node:os";
import { dirname, join, resolve, delimiter } from "node:path";
import { parseArgs } from "node:util";
import { pathToFileURL } from "node:url";
import {
  loadSuite,
  suiteDirectory,
  run,
  prepareCorpus,
  validateGoldSources,
  corpusManifest,
  directoryManifest,
  fileHash,
  readJson,
  writeJson,
  repositorySlug,
  inside,
  objectHash,
} from "./lib.mjs";
import { scoreSembleResponse } from "./semble-scoring.mjs";
import { SEMBLE_PROTOCOL, aggregateSemble } from "./semble-report.mjs";

const SOURCE_COMMIT = "0051e000fcaac69a9c5d081ebbc8d4cb8508160b";

async function evidence(directory, path, value) {
  await writeJson(join(directory, path), value);
  return { path, sha256: await fileHash(join(directory, path)) };
}

export async function auditVisibleSource(items, root, chunks) {
  const errors = [];
  for (const item of items) {
    assert.ok(
      inside(await realpath(root), await realpath(join(root, item.path))),
      "visible source escapes corpus",
    );
    const visible = item.source_content;
    const carriers = chunks.filter(
      (chunk) =>
        chunk.file_path === item.path &&
        chunk.start_line === item.range.start_line &&
        chunk.end_line === item.range.end_line &&
        chunk.content === visible,
    );
    if (!carriers.length)
      errors.push(
        `rank ${item.rank}: full content not bound to a persisted native chunk`,
      );
    const source = (await readFile(join(root, item.path), "utf8"))
      .replace(/\r\n?/g, "\n")
      .split("\n");
    for (const [index, line] of item.source_lines.entries()) {
      const actual = source[line.line - 1];
      const matches =
        actual === line.text ||
        (index === 0 && actual?.endsWith(line.text)) ||
        (index === item.source_lines.length - 1 &&
          !visible.endsWith("\n") &&
          actual?.startsWith(line.text)) ||
        (item.source_lines.length === 1 &&
          !visible.endsWith("\n") &&
          carriers.length > 0 &&
          actual?.includes(line.text));
      if (!matches)
        errors.push(
          `rank ${item.rank} ${item.path}:${line.line}: visible line does not map to locked source`,
        );
    }
  }
  return errors;
}

/** Compare all native fields, including scores and final newlines, without Gold. */
export function compareSdkMcp(response, sdkQuery, expectedQuery) {
  try {
    assert.equal(
      sdkQuery.query,
      expectedQuery,
      "SDK query differs from original",
    );
    assert.ok(Array.isArray(sdkQuery.results), "SDK results missing");
    assert.notEqual(response?.isError, true, "MCP returned a product error");
    assert.equal(response?.content?.length, 1, "MCP text block count differs");
    assert.equal(response.content[0].type, "text", "MCP response is not text");
    const payload = JSON.parse(response.content[0].text);
    const expected = sdkQuery.results.length
      ? { query: expectedQuery, results: sdkQuery.results }
      : { error: "No results found." };
    assert.deepEqual(payload, expected, "MCP and SDK native results differ");
    return [];
  } catch (error) {
    return [`SDK/MCP parity: ${error.message}`];
  }
}

async function runRepository(suite, repo, options, experiment) {
  const tasks = suite.lock.tasks.filter(
    (task) => task.repository === repo.repository,
  );
  const output = join(options.output, repositorySlug(repo.repository));
  await mkdir(join(output, "raw"), { recursive: true });
  const manifest = {
    schema_version: 1,
    engine: "semble",
    repository: repo.repository,
    repository_commit: repo.commit,
    tasks: tasks.map((task) => task.task_id),
    modes: ["hybrid"],
    planned_calls: tasks.length * SEMBLE_PROTOCOL.repetitions,
    preparation_status: "pending",
    invalid_reasons: [],
    evidence: {},
    started_at: new Date().toISOString(),
  };
  const planned = tasks.flatMap((task) =>
    Array.from({ length: SEMBLE_PROTOCOL.repetitions }, (_, i) => ({
      task,
      repetition: i + 1,
    })),
  );
  const records = [],
    audits = [];
  let client,
    transport,
    stderr,
    root,
    phase = "corpus",
    failure;
  try {
    root = await prepareCorpus(repo, options.corpus);
    manifest.corpus_root = root;
    assert.ok(
      !inside(root, output) && !inside(root, options.model),
      "artifacts must remain outside corpus",
    );
    await validateGoldSources(root, tasks, suite.gold);
    const beforeCorpus = await corpusManifest(root);
    assert.deepEqual(
      await directoryManifest(options.model),
      options.modelManifest,
      "model changed since experiment preflight",
    );
    const env = {
      ...process.env,
      PATH: `${dirname(options.python)}${delimiter}${process.env.PATH ?? ""}`,
      SEMBLE_CACHE_LOCATION: join(output, "index-cache"),
      SEMBLE_MODEL_NAME: options.model,
      HF_HUB_OFFLINE: "1",
      TOKENIZERS_PARALLELISM: "false",
      PYTHONHASHSEED: "0",
      SEMBLE_MAX_FILE_BYTES: "1000000",
    };
    delete env.PYTHONPATH;
    delete env.PYTHONHOME;
    phase = "index";
    const start = performance.now();
    const prepared = await run(
      options.python,
      [
        join(suiteDirectory, "semble-prepare.py"),
        "index",
        "--repo",
        root,
        "--content",
        SEMBLE_PROTOCOL.content,
        "--output",
        join(output, "preparation.json"),
      ],
      { env, timeout: 1_200_000 },
    );
    manifest.index_seconds = (performance.now() - start) / 1000;
    phase = "preparation-audit";
    await writeJson(join(output, "preparation-log.json"), prepared);
    const preparation = await readJson(join(output, "preparation.json"));
    assert.equal(preparation.loaded_from_disk, false);
    assert.equal(preparation.source_mapping_verified, true);
    assert.deepEqual(preparation.content, [SEMBLE_PROTOCOL.content]);
    const trackedFiles = new Set(
      beforeCorpus.entries
        .filter((entry) => entry.kind === "file")
        .map((entry) => entry.path),
    );
    assert.ok(
      preparation.indexed_files.every((path) => trackedFiles.has(path)),
      "index includes files outside fixed tracked corpus",
    );
    assert.ok(inside(join(output, "index-cache"), preparation.index_directory));
    assert.deepEqual(
      await directoryManifest(options.model),
      options.modelManifest,
      "model changed during index build",
    );
    const chunks = await readJson(
      join(preparation.index_directory, "chunks.json"),
    );
    manifest.preparation = await evidence(
      output,
      "preparation.json",
      preparation,
    );
    manifest.evidence.before = {
      corpus: await evidence(output, "corpus-before.json", beforeCorpus),
      index: await evidence(
        output,
        "index-before.json",
        await directoryManifest(preparation.index_directory),
      ),
      model: await evidence(
        output,
        "model-before.json",
        await directoryManifest(options.model),
      ),
    };
    phase = "connect";
    const { Client } = await import("@modelcontextprotocol/client");
    const { StdioClientTransport } =
      await import("@modelcontextprotocol/client/stdio");
    transport = new StdioClientTransport({
      command: join(dirname(options.python), "semble"),
      args: ["--content", SEMBLE_PROTOCOL.content],
      env,
      stderr: "pipe",
      cwd: output,
    });
    stderr = createWriteStream(join(output, "mcp-stderr.log"));
    transport.stderr?.pipe(stderr);
    client = new Client({ name: "sweqa20-retrieval-only", version: "1.0.0" });
    const connecting = performance.now();
    await client.connect(transport);
    manifest.mcp_connect_ms = performance.now() - connecting;
    phase = "schema-audit";
    const tools = await client.listTools();
    await writeJson(join(output, "mcp-tools.json"), tools);
    const search = tools.tools.find((tool) => tool.name === "search");
    assert.ok(search, "native search tool missing");
    for (const field of [
      "repo",
      "query",
      "top_k",
      "max_snippet_lines",
      "content",
    ])
      assert.ok(
        search.inputSchema.properties[field],
        `search schema missing ${field}`,
      );
    manifest.preparation_status = "ready";
    phase = "query-evidence";
    for (const [i, { task, repetition }] of planned.entries()) {
      const request = {
        name: "search",
        arguments: {
          query: task.query,
          repo: root,
          top_k: SEMBLE_PROTOCOL.limit,
          max_snippet_lines: SEMBLE_PROTOCOL.max_snippet_lines,
          content: SEMBLE_PROTOCOL.content,
        },
      };
      const rawPath = `raw/${task.task_slug}-hybrid-${repetition}.json`;
      let response,
        transportError = null;
      const started = performance.now();
      try {
        response = await client.callTool(request, undefined, {
          timeout: 300_000,
        });
      } catch (error) {
        transportError = String(error);
        response = {
          isError: true,
          content: [{ type: "text", text: transportError }],
        };
      }
      const latency = performance.now() - started;
      await writeJson(join(output, rawPath), response);
      const record = {
        task_id: task.task_id,
        mode: "hybrid",
        repetition,
        quality_observation: repetition === SEMBLE_PROTOCOL.quality_repetition,
        session_first_query: i === 0,
        latency_ms: latency,
        request,
        raw_path: rawPath,
        raw_sha256: await fileHash(join(output, rawPath)),
        transport_error: transportError,
      };
      records.push(record);
      await appendFile(
        join(output, "calls.jsonl"),
        `${JSON.stringify(record)}\n`,
      );
      const scored = scoreSembleResponse(response, suite.gold[task.task_id], {
        expectedQuery: task.query,
      });
      const errors =
        scored.status === "harness_invalid"
          ? [scored.invalid_reason]
          : await auditVisibleSource(scored.items, root, chunks);
      audits.push({
        raw_path: rawPath,
        raw_sha256: record.raw_sha256,
        errors,
        results_checked: scored.items.length,
      });
      manifest.invalid_reasons.push(...errors);
      if (repetition === SEMBLE_PROTOCOL.quality_repetition)
        console.log(
          `${task.task_id}: ${scored.status}, first rank ${scored.first_hit_rank}`,
        );
    }
    await client.close();
    client = null;
    phase = "sdk-parity";
    await writeJson(
      join(output, "sdk-queries.json"),
      tasks.map((task) => ({
        task_id: task.task_id,
        query: task.query,
      })),
    );
    const replay = await run(
      options.python,
      [
        join(suiteDirectory, "semble-prepare.py"),
        "sdk-replay",
        "--index-directory",
        preparation.index_directory,
        "--repo",
        root,
        "--queries",
        join(output, "sdk-queries.json"),
        "--top-k",
        String(SEMBLE_PROTOCOL.limit),
        "--content",
        SEMBLE_PROTOCOL.content,
        "--output",
        join(output, "sdk-replay.json"),
      ],
      { env, timeout: 1_200_000 },
    );
    await writeJson(join(output, "sdk-replay-log.json"), replay);
    const sdk = await readJson(join(output, "sdk-replay.json"));
    manifest.sdk_replay = await evidence(output, "sdk-replay.json", sdk);
    assert.equal(sdk.schema_version, 1);
    assert.equal(sdk.loaded_from_disk, true);
    assert.equal(sdk.index_directory, preparation.index_directory);
    assert.equal(sdk.corpus_root, root);
    assert.equal(sdk.model_path, options.model);
    assert.deepEqual(sdk.content, [SEMBLE_PROTOCOL.content]);
    assert.deepEqual(sdk.parameters, {
      top_k: SEMBLE_PROTOCOL.limit,
      alpha: null,
      rerank: null,
      filter_languages: null,
      filter_paths: null,
      max_snippet_lines: null,
    });
    assert.deepEqual(
      sdk.queries.map((row) => row.task_id),
      tasks.map((task) => task.task_id),
    );
    const parity = [];
    for (const [index, task] of tasks.entries()) {
      const record = records.find(
        (row) => row.task_id === task.task_id && row.quality_observation,
      );
      assert.ok(record, "missing quality MCP observation for SDK comparison");
      const response = await readJson(join(output, record.raw_path));
      const errors = compareSdkMcp(response, sdk.queries[index], task.query);
      parity.push({
        task_id: task.task_id,
        repetition: SEMBLE_PROTOCOL.quality_repetition,
        raw_path: record.raw_path,
        raw_sha256: record.raw_sha256,
        sdk_result_sha256: objectHash(sdk.queries[index]),
        matches: errors.length === 0,
        errors,
      });
      manifest.invalid_reasons.push(...errors);
    }
    manifest.sdk_parity = await evidence(output, "sdk-parity.json", {
      schema_version: 1,
      quality_repetition: SEMBLE_PROTOCOL.quality_repetition,
      sdk_replay_sha256: manifest.sdk_replay.sha256,
      calls: parity,
    });
    phase = "audit";
    await prepareCorpus(repo, options.corpus);
    manifest.evidence.after = {
      corpus: await evidence(
        output,
        "corpus-after.json",
        await corpusManifest(root),
      ),
      index: await evidence(
        output,
        "index-after.json",
        await directoryManifest(preparation.index_directory),
      ),
      model: await evidence(
        output,
        "model-after.json",
        await directoryManifest(options.model),
      ),
    };
    for (const kind of ["corpus", "index", "model"]) {
      const before = await readJson(
        join(output, manifest.evidence.before[kind].path),
      );
      const after = await readJson(
        join(output, manifest.evidence.after[kind].path),
      );
      assert.deepEqual(before, after, `${kind} changed during queries`);
    }
    assert.equal(
      manifest.invalid_reasons.length,
      0,
      "source or SDK/MCP parity audit failed",
    );
    manifest.post_run_integrity = "verified";
  } catch (error) {
    failure = String(error);
    manifest.preparation_error = { phase, message: failure };
    const productFailure =
      ["index", "connect", "query"].includes(phase) && error.result?.code !== 2;
    if (manifest.preparation_status !== "ready")
      manifest.preparation_status = productFailure
        ? "product_error"
        : "harness_invalid";
    if (!productFailure) manifest.invalid_reasons.push(failure);
    console.error(`${repo.repository}: ${phase}: ${failure}`);
  } finally {
    if (client) await client.close().catch(() => {});
    else if (transport) await transport.close().catch(() => {});
    stderr?.end();
  }
  for (const [i, { task, repetition }] of planned.entries()) {
    if (
      records.some(
        (r) => r.task_id === task.task_id && r.repetition === repetition,
      )
    )
      continue;
    const rawPath = `raw/${task.task_slug}-hybrid-${repetition}.json`;
    await writeJson(join(output, rawPath), {
      isError: true,
      content: [{ type: "text", text: failure ?? "preparation failed" }],
    });
    const record = {
      task_id: task.task_id,
      mode: "hybrid",
      repetition,
      quality_observation: repetition === SEMBLE_PROTOCOL.quality_repetition,
      session_first_query: i === 0,
      latency_ms: null,
      request: {
        name: "search",
        arguments: {
          query: task.query,
          repo: root ?? join(options.corpus, repositorySlug(repo.repository)),
          top_k: SEMBLE_PROTOCOL.limit,
          max_snippet_lines: SEMBLE_PROTOCOL.max_snippet_lines,
          content: SEMBLE_PROTOCOL.content,
        },
      },
      raw_path: rawPath,
      raw_sha256: await fileHash(join(output, rawPath)),
      transport_error: failure,
    };
    records.push(record);
    await appendFile(
      join(output, "calls.jsonl"),
      `${JSON.stringify(record)}\n`,
    );
    audits.push({
      raw_path: rawPath,
      raw_sha256: record.raw_sha256,
      errors: [],
      results_checked: 0,
    });
  }
  manifest.calls = {
    path: "calls.jsonl",
    sha256: await fileHash(join(output, "calls.jsonl")),
  };
  manifest.source_audit = await evidence(output, "source-audit.json", {
    schema_version: 1,
    calls: audits,
  });
  manifest.finished_at = new Date().toISOString();
  await writeJson(join(output, "run.json"), manifest);
  experiment.repository_runs.push({
    repository: repo.repository,
    path: `${repositorySlug(repo.repository)}/run.json`,
    sha256: await fileHash(join(output, "run.json")),
  });
}

export async function main(args = process.argv.slice(2)) {
  const { values } = parseArgs({
    args,
    options: {
      python: { type: "string" },
      source: { type: "string" },
      model: { type: "string" },
      "model-info": { type: "string" },
      output: { type: "string" },
      corpus: { type: "string" },
      repository: { type: "string" },
      baseline: { type: "string" },
    },
  });
  for (const name of [
    "python",
    "source",
    "model",
    "model-info",
    "output",
    "corpus",
  ])
    assert.ok(values[name], `--${name} is required`);
  const options = Object.fromEntries(
    Object.entries(values).map(([key, value]) => [
      key,
      key === "repository" ? value : resolve(value),
    ]),
  );
  await assert.rejects(
    lstat(options.output),
    { code: "ENOENT" },
    "output must be new",
  );
  await mkdir(options.output, { recursive: true });
  const suite = await loadSuite();
  assert.equal(
    (
      await run("git", ["-C", options.source, "rev-parse", "HEAD"])
    ).stdout.trim(),
    SOURCE_COMMIT,
  );
  assert.equal(
    (await run("git", ["-C", options.source, "status", "--porcelain"])).stdout,
    "",
    "Semble source must be pristine",
  );
  await run(options.python, [
    join(suiteDirectory, "semble-prepare.py"),
    "environment",
    "--output",
    join(options.output, "runtime.json"),
  ]);
  const runtime = await readJson(join(options.output, "runtime.json"));
  assert.equal(
    runtime.module_path,
    runtime.distribution_module_path,
    "imported Semble differs from installed distribution",
  );
  const expectedSource = (
    await run("git", ["-C", options.source, "ls-files", "src/semble"])
  ).stdout
    .trim()
    .split("\n")
    .filter((path) => path.endsWith(".py"))
    .map((path) => path.slice(4))
    .sort();
  assert.ok(expectedSource.length > 0);
  assert.deepEqual(
    runtime.installed_source.map((entry) => entry.path).sort(),
    expectedSource,
    "installed source inventory differs",
  );
  for (const entry of runtime.installed_source)
    assert.equal(
      await fileHash(join(options.source, "src", entry.path)),
      entry.sha256,
      `installed package differs from source: ${entry.path}`,
    );
  const modelInfo = await readJson(options["model-info"]);
  assert.equal(
    await realpath(modelInfo.directory),
    await realpath(options.model),
  );
  assert.equal(modelInfo.model, SEMBLE_PROTOCOL.model);
  assert.match(modelInfo.revision, /^[a-f0-9]{40}$/);
  options.modelManifest = await directoryManifest(options.model);
  await writeJson(
    join(options.output, "model-identity.json"),
    options.modelManifest,
  );
  const repositories = suite.lock.repositories.filter(
    (repo) => !options.repository || repo.repository === options.repository,
  );
  assert.ok(repositories.length, "unknown repository");
  const tasks = suite.lock.tasks.filter((task) =>
    repositories.some((repo) => repo.repository === task.repository),
  );
  const experiment = {
    schema_version: 1,
    engine: "semble",
    started_at: new Date().toISOString(),
    suite: {
      source: suite.identity.source,
      gold: suite.identity.gold,
      semble_gold: suite.identity.semble_gold,
      protocol: objectHash(SEMBLE_PROTOCOL),
    },
    protocol: SEMBLE_PROTOCOL,
    tool: {
      source_commit: SOURCE_COMMIT,
      version: runtime.version,
      runtime_sha256: await fileHash(join(options.output, "runtime.json")),
      model: modelInfo,
      model_sha256: options.modelManifest.sha256,
    },
    environment: {
      platform: platform(),
      architecture: arch(),
      node: process.version,
      python: runtime.python,
      cpus: cpus().length,
      cpu_model: cpus()[0]?.model,
      tokenizers_parallelism: false,
      pythonhashseed: 0,
      semble_max_file_bytes: 1000000,
    },
    scope: options.repository ? "explicit-subset" : "full-20-original-queries",
    expected_task_ids: tasks.map((task) => task.task_id),
    repository_runs: [],
    complete: false,
  };
  await writeJson(join(options.output, "experiment.json"), experiment);
  for (const repo of repositories) {
    console.log(`Preparing ${repo.repository}...`);
    await runRepository(suite, repo, options, experiment);
    await writeJson(join(options.output, "experiment.json"), experiment);
  }
  experiment.complete = true;
  assert.deepEqual(
    await directoryManifest(options.model),
    options.modelManifest,
    "model changed during complete experiment",
  );
  experiment.finished_at = new Date().toISOString();
  await writeJson(join(options.output, "experiment.json"), experiment);
  const report = await aggregateSemble(options.output, {
    baselinePath: options.baseline,
    allowSubset: Boolean(options.repository),
  });
  if (!report.integrity_passed || report.comparison_error) process.exitCode = 1;
}

if (
  process.argv[1] &&
  import.meta.url === pathToFileURL(resolve(process.argv[1])).href
) {
  main().catch((error) => {
    console.error(error);
    process.exitCode = 1;
  });
}
