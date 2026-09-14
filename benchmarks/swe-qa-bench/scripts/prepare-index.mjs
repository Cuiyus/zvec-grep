#!/usr/bin/env node
/** Build a normal production index during benchmark setup, before any QA runs. */
import { createHash } from "node:crypto";
import { lstat, mkdir, readFile, realpath, writeFile } from "node:fs/promises";
import { basename, dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { parseArgs } from "node:util";
import {
  assertFreshInfo,
  isWithin,
  loadProduction,
  sourceIdentity,
  withBenchmarkRemoteEmbeddingAuthorization,
} from "./readonly-search.mjs";

async function exists(path) {
  try {
    await lstat(path);
    return true;
  } catch (error) {
    if (error.code === "ENOENT") return false;
    throw error;
  }
}

// Check both lexical paths and symlink ancestors before creating directories.
async function externalPath(root, path, label) {
  const absolute = resolve(path);
  if (isWithin(root, absolute))
    throw new Error(`${label} must be outside source`);
  let ancestor = absolute;
  const suffix = [];
  while (!(await exists(ancestor))) {
    suffix.unshift(basename(ancestor));
    ancestor = dirname(ancestor);
  }
  if (isWithin(root, join(await realpath(ancestor), ...suffix))) {
    throw new Error(`${label} resolves inside source`);
  }
  return absolute;
}

function errorRecord(error) {
  return {
    name: error.name ?? "Error",
    message: String(error.message ?? error),
    code: error.code,
    context: error.context,
    cause: error.cause ? String(error.cause.message ?? error.cause) : undefined,
  };
}

export async function prepareIndex(options, production) {
  if (options.maxFileSizeBytes !== undefined &&
      (!Number.isSafeInteger(options.maxFileSizeBytes) || options.maxFileSizeBytes < 1)) {
    throw new Error("maxFileSizeBytes must be a positive safe integer");
  }
  const startedAt = new Date().toISOString();
  const started = performance.now();
  const root = await realpath(options.root);
  const log = await externalPath(root, options.log, "Setup log");
  const modelCacheDir = await externalPath(
    root,
    options.modelCacheDir,
    "Model cache",
  );
  if (await exists(log))
    throw new Error(
      "Setup log already exists; use a new preparation output path",
    );
  await mkdir(dirname(log), { recursive: true });
  const report = {
    schema_version: 1,
    phase: "index-preparation",
    started_at: startedAt,
    status: "running",
    root,
    embedding_model: options.embeddingModel,
    model_cache_dir: modelCacheDir,
    package: production.packageIdentity,
    setup_script_sha256: createHash("sha256")
      .update(await readFile(fileURLToPath(import.meta.url)))
      .digest("hex"),
    index_options: {
      root,
      selection: options.maxFileSizeBytes === undefined
        ? "production-defaults" : "production-defaults-with-uniform-file-size-cap",
      ...(options.maxFileSizeBytes === undefined ? {} : { maxFileSizeBytes: options.maxFileSizeBytes }),
      query_or_gold_provided: false,
    },
    index_initially_present: await exists(
      join(root, ".zvec-grep", "manifest.json"),
    ),
    progress_events: 0,
    progress_emitted: 0,
  };
  let service;
  let failure;
  let lastProgressAt = -Infinity;
  let lastProgressStage;
  try {
    report.source_before = await sourceIdentity(root);
    service = await production.createZvecGrep({
      root,
      embedding: options.embeddingModel,
      modelCacheDir,
    });
    const buildStarted = performance.now();
    try {
      // One production build, optionally with a uniform resource cap. No custom
      // chunks, selected paths, query hints, or reference answers are supplied.
      report.result = await withBenchmarkRemoteEmbeddingAuthorization(
        { root, embeddingModel: options.embeddingModel },
        production,
        () =>
          service.index({
            root,
            ...(options.maxFileSizeBytes === undefined ? {} : { maxFileSizeBytes: options.maxFileSizeBytes }),
            onProgress(progress) {
              report.progress_events++;
              report.last_progress = progress;
              const current = performance.now();
              const stage = `${progress.phase}:${progress.embedding?.stage ?? ""}`;
              if (
                stage === lastProgressStage &&
                current - lastProgressAt < 1000 &&
                progress.phase !== "done" &&
                progress.embedding?.stage !== "warning"
              )
                return;
              lastProgressAt = current;
              lastProgressStage = stage;
              report.progress_emitted++;
              const event = {
                event: "index-progress",
                at: new Date().toISOString(),
                ...progress,
              };
              (
                options.onProgress ??
                ((value) => process.stderr.write(`${JSON.stringify(value)}\n`))
              )(event);
            },
          }),
      );
    } finally {
      report.build_duration_ms = performance.now() - buildStarted;
    }
    report.info = await service.info({ root, includeStatus: true });
    assertFreshInfo(report.info, root, options.embeddingModel);
    report.fresh = true;
    report.status = "completed";
  } catch (error) {
    failure = error;
    report.status = "failed";
    report.fresh = false;
    report.error = errorRecord(error);
  } finally {
    if (service) {
      try {
        await service.close();
      } catch (error) {
        failure ??= error;
        report.status = "failed";
        report.fresh = false;
        report.close_error = errorRecord(error);
      }
    }
    if (report.source_before) {
      try {
        report.source_after = await sourceIdentity(root);
        report.source_unchanged =
          report.source_before.sha256 === report.source_after.sha256 &&
          report.source_before.git_commit === report.source_after.git_commit;
        if (!report.source_unchanged)
          throw new Error("Tracked source changed during index preparation");
      } catch (error) {
        failure ??= error;
        report.status = "failed";
        report.fresh = false;
        report.source_error = errorRecord(error);
      }
    }
    report.finished_at = new Date().toISOString();
    report.duration_ms = performance.now() - started;
    await writeFile(log, `${JSON.stringify(report, null, 2)}\n`, {
      flag: "wx",
      mode: 0o600,
    });
  }
  if (failure) throw failure;
  return report;
}

export async function main(argv = process.argv.slice(2)) {
  const { values } = parseArgs({
    args: argv,
    options: {
      root: { type: "string" },
      "package-dir": { type: "string" },
      "embedding-model": {
        type: "string",
        default: "local/potion-code-16m-v2",
      },
      "model-cache-dir": { type: "string" },
      "max-file-size-bytes": { type: "string" },
      log: { type: "string" },
    },
  });
  if (
    !values.root ||
    !values["package-dir"] ||
    !values["model-cache-dir"] ||
    !values.log
  ) {
    throw new Error(
      "Usage: prepare-index.mjs --root /app --package-dir ZG_0.2.2_DIR --embedding-model local/potion-code-16m-v2 --model-cache-dir /models --log /logs/index-build.json",
    );
  }
  const maxFileSizeBytes = values["max-file-size-bytes"] === undefined
    ? undefined : Number(values["max-file-size-bytes"]);
  if (maxFileSizeBytes !== undefined &&
      (!/^\d+$/.test(values["max-file-size-bytes"]) ||
       !Number.isSafeInteger(maxFileSizeBytes) || maxFileSizeBytes < 1)) {
    throw new Error("--max-file-size-bytes must be a positive safe integer");
  }
  const production = await loadProduction(values["package-dir"]);
  const report = await prepareIndex(
    {
      root: values.root,
      embeddingModel: values["embedding-model"],
      modelCacheDir: values["model-cache-dir"],
      log: values.log,
      maxFileSizeBytes,
    },
    production,
  );
  process.stdout.write(
    `${JSON.stringify({ status: report.status, fresh: report.fresh, source_unchanged: report.source_unchanged, build_duration_ms: report.build_duration_ms, duration_ms: report.duration_ms, info: report.info, log: resolve(values.log) })}\n`,
  );
}

if (
  process.argv[1] &&
  resolve(process.argv[1]) === fileURLToPath(import.meta.url)
) {
  main().catch((error) => {
    process.stderr.write(
      `${JSON.stringify({ status: "failed", error: errorRecord(error) })}\n`,
    );
    process.exitCode = 1;
  });
}
