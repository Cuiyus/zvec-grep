#!/usr/bin/env node
/** Replay a frozen, recorded backend request; no LLM or query rewriting. */
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { parseArgs } from "node:util";
import { createRuntime, loadProduction } from "./readonly-search.mjs";

const FIELDS = new Set([
  "root",
  "query",
  "queries",
  "routes",
  "fuse",
  "limit",
  "preferSymbol",
  "symbolTypes",
  "globs",
  "insensitiveGlobs",
  "fileTypes",
  "excludedFileTypes",
  "hidden",
  "noIgnore",
  "ignoreFiles",
  "maxDepth",
  "maxFileSizeBytes",
  "follow",
  "embeddingConcurrency",
  "modifiedAfter",
  "modifiedBefore",
  "autoUpdate",
  "trace",
]);

export function validateReplayUnit(unit, root) {
  if (!unit || !/^[a-z0-9-]+$/.test(unit.unit_id ?? ""))
    throw new Error("Replay unit needs a stable identifier");
  const request = unit.request;
  if (!request || typeof request !== "object" || Array.isArray(request))
    throw new Error("Missing recorded backend request");
  for (const key of Object.keys(request))
    if (!FIELDS.has(key)) throw new Error(`Unsupported recorded field: ${key}`);
  if (request.root !== root)
    throw new Error("Recorded root differs from frozen corpus");
  if (request.autoUpdate !== false || request.trace !== true)
    throw new Error("Replay requires recorded autoUpdate=false and trace=true");
  const strings = [];
  if (request.query !== undefined) strings.push(request.query);
  if (request.queries !== undefined) {
    if (!Array.isArray(request.queries))
      throw new Error("queries must be an array");
    strings.push(...request.queries);
  }
  if (request.routes !== undefined) {
    if (!Array.isArray(request.routes))
      throw new Error("routes must be an array");
    for (const route of request.routes) {
      if (!route || !["fts", "vector"].includes(route.mode))
        throw new Error(
          "Only recorded indexed FTS/vector routes are supported",
        );
      strings.push(route.query);
    }
  }
  if (
    !strings.length ||
    strings.some((q) => typeof q !== "string" || !q.trim())
  )
    throw new Error(
      "Recorded query strings must be nonempty; never repair them",
    );
  if (
    request.limit !== undefined &&
    (!Number.isInteger(request.limit) || request.limit < 1)
  )
    throw new Error("Invalid recorded limit");
  return structuredClone(request);
}

export async function replayUnit(runtime, unit, repetitions, emit) {
  if (!Number.isInteger(repetitions) || repetitions < 1)
    throw new Error("Invalid repetition count");
  const request = validateReplayUnit(unit, runtime.root);
  let failures = 0;
  for (let repetition = 1; repetition <= repetitions; repetition++) {
    const metadata = {
      origin: "query-aware-retrieval-replay",
      unit_id: unit.unit_id,
      kind: unit.kind,
      mode: unit.mode ?? null,
      repetition,
      repetitions,
    };
    try {
      const { event } = await runtime.search(
        structuredClone(request),
        metadata,
      );
      await emit(event);
    } catch (error) {
      failures++;
      await emit({
        ...metadata,
        status: "error",
        error: {
          name: error.name,
          message: String(error.message ?? error),
        },
      });
    }
  }
  return failures;
}

export async function main(argv = process.argv.slice(2)) {
  const { values } = parseArgs({
    args: argv,
    options: {
      root: { type: "string" },
      snapshot: { type: "string" },
      log: { type: "string" },
      "package-dir": { type: "string" },
      "request-file": { type: "string" },
      "embedding-model": { type: "string" },
      "model-cache-dir": { type: "string" },
      repetitions: { type: "string", default: "5" },
    },
  });
  for (const k of ["root", "snapshot", "log", "package-dir", "request-file"])
    if (!values[k]) throw new Error(`--${k} is required`);
  const unit = JSON.parse(await readFile(values["request-file"], "utf8"));
  validateReplayUnit(unit, values.root);
  const production = await loadProduction(values["package-dir"]);
  const runtime = await createRuntime(
    {
      command: "replay",
      root: values.root,
      snapshot: values.snapshot,
      log: values.log,
      workingCopy: true,
      embeddingModel: values["embedding-model"],
      modelCacheDir: values["model-cache-dir"],
    },
    production,
  );
  try {
    const errors = await replayUnit(
      runtime,
      unit,
      Number(values.repetitions),
      (event) => process.stdout.write(`${JSON.stringify(event)}\n`),
    );
    if (errors) process.exitCode = 1;
  } finally {
    await runtime.close();
  }
}

if (
  process.argv[1] &&
  resolve(process.argv[1]) === fileURLToPath(import.meta.url)
)
  main().catch((error) => {
    process.stderr.write(`${error.stack ?? error}\n`);
    process.exitCode = 1;
  });
