#!/usr/bin/env node
/**
 * Benchmark-only, query-only bridge over a frozen, already-existing zg index.
 * No gold data is accepted. No daemon is started and no indexing API is called.
 * The runner must separately enforce source read-only permissions and isolation.
 */
import { createHash, randomUUID } from "node:crypto";
import { createReadStream } from "node:fs";
import {
  appendFile,
  lstat,
  mkdir,
  readFile,
  readdir,
  readlink,
  realpath,
  writeFile,
} from "node:fs/promises";
import { execFileSync } from "node:child_process";
import { createRequire } from "node:module";
import { dirname, isAbsolute, join, relative, resolve, sep } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { parseArgs } from "node:util";

const SCHEMA_VERSION = 1;
const SCRIPT_DIR = dirname(fileURLToPath(import.meta.url));
const DEFAULT_PACKAGE_DIR = resolve(SCRIPT_DIR, "../../..");
const sha256 = (value) => createHash("sha256").update(value).digest("hex");
const now = () => new Date().toISOString();

function stableJson(value) {
  if (typeof value === "bigint")
    return JSON.stringify({ $bigint: value.toString() });
  if (ArrayBuffer.isView(value))
    return stableJson({
      $typed: value.constructor.name,
      bytes: Buffer.from(
        value.buffer,
        value.byteOffset,
        value.byteLength,
      ).toString("base64"),
    });
  if (Array.isArray(value)) return `[${value.map(stableJson).join(",")}]`;
  if (value && typeof value === "object") {
    return `{${Object.keys(value)
      .sort()
      .filter((key) => value[key] !== undefined)
      .map((key) => `${JSON.stringify(key)}:${stableJson(value[key])}`)
      .join(",")}}`;
  }
  return JSON.stringify(value);
}

export function isWithin(root, path) {
  const rel = relative(root, path);
  return (
    rel === "" ||
    (!rel.startsWith(`..${sep}`) && rel !== ".." && !isAbsolute(rel))
  );
}

async function fileDigest(path) {
  const hash = createHash("sha256");
  for await (const chunk of createReadStream(path)) hash.update(chunk);
  return hash.digest("hex");
}

async function exists(path) {
  try {
    await lstat(path);
    return true;
  } catch (error) {
    if (error.code === "ENOENT") return false;
    throw error;
  }
}

/** Source scope is explicit: all tracked paths, not just files selected by zg. */
export async function sourceIdentity(root) {
  const paths = execFileSync(
    "git",
    ["-C", root, "ls-files", "--cached", "-z"],
    {
      encoding: "utf8",
      maxBuffer: 32 * 1024 * 1024,
    },
  )
    .split("\0")
    .filter(Boolean)
    .sort();
  if (paths.length === 0)
    throw new Error("Frozen QA source has no tracked files");
  const files = [];
  for (const path of paths) {
    const absolute = resolve(root, path);
    if (!isWithin(root, absolute))
      throw new Error(`Source path escapes root: ${path}`);
    const stat = await lstat(absolute);
    if (stat.isDirectory())
      throw new Error(`Git submodule must be frozen separately: ${path}`);
    if (stat.isSymbolicLink()) {
      const target = await realpath(absolute);
      if (!isWithin(root, target))
        throw new Error(`Source symlink escapes root: ${path}`);
      files.push({
        path,
        link: await readlink(absolute),
        target_sha256: await fileDigest(target),
      });
    } else if (stat.isFile()) {
      files.push({
        path,
        bytes: stat.size,
        sha256: await fileDigest(absolute),
      });
    } else {
      throw new Error(`Unsupported tracked source type: ${path}`);
    }
  }
  return {
    root,
    scope: "git-tracked-files",
    git_commit: execFileSync("git", ["-C", root, "rev-parse", "HEAD"], {
      encoding: "utf8",
    }).trim(),
    sha256: sha256(stableJson(files)),
    file_count: files.length,
    files,
  };
}

async function storageFiles(home) {
  const files = [];
  async function walk(path) {
    const stat = await lstat(path);
    if (stat.isSymbolicLink())
      throw new Error(`Index storage symlink is not frozen: ${path}`);
    if (stat.isDirectory()) {
      for (const name of (await readdir(path)).sort())
        await walk(join(path, name));
    } else if (stat.isFile()) {
      files.push({
        path: relative(home, path),
        bytes: stat.size,
        sha256: await fileDigest(path),
      });
    } else {
      throw new Error(`Unsupported index storage type: ${path}`);
    }
  }
  await walk(join(home, "files.zvec"));
  await walk(join(home, "index.zvec"));
  return files;
}

async function directoryDigest(directory) {
  const files = [];
  async function walk(path) {
    const stat = await lstat(path);
    if (stat.isDirectory()) {
      for (const name of (await readdir(path)).sort())
        await walk(join(path, name));
    } else if (stat.isFile()) {
      files.push([relative(directory, path), await fileDigest(path)]);
    } else {
      throw new Error(
        `Package code contains an unsupported file type: ${path}`,
      );
    }
  }
  await walk(directory);
  return sha256(stableJson(files));
}

async function dependencyVersion(require, name) {
  let current = dirname(require.resolve(name));
  for (;;) {
    const metadata = join(current, "package.json");
    if (await exists(metadata)) {
      const pkg = JSON.parse(await readFile(metadata, "utf8"));
      if (pkg.name === name) return pkg.version;
    }
    if (dirname(current) === current)
      throw new Error(`Cannot identify dependency ${name}`);
    current = dirname(current);
  }
}

export function assertFreshInfo(info, root, embeddingModel) {
  if (
    !info.indexed ||
    info.source !== "index" ||
    info.indexPolicy !== "enabled"
  ) {
    throw new Error(
      "An enabled, existing index is required; this runtime never creates one",
    );
  }
  if (resolve(info.root) !== root)
    throw new Error("Index belongs to a different workspace root");
  const workspace = info.workspaceIndex;
  if (!workspace?.embedding)
    throw new Error("Existing index has no embedding schema");
  if (!workspace.path || resolve(workspace.path) !== resolve(info.home)) {
    throw new Error("Index manifest storage path differs from its frozen home");
  }
  for (const entry of workspace.rootPaths ?? []) {
    if (!isWithin(root, resolve(entry.absolutePath))) {
      throw new Error(
        "Index rootPaths includes source outside the frozen workspace",
      );
    }
  }
  const reference = `${workspace.embedding.provider}/${workspace.embedding.model}`;
  if (embeddingModel && reference !== embeddingModel) {
    throw new Error(
      `Embedding model mismatch: index=${reference}, requested=${embeddingModel}`,
    );
  }
  if (!info.status) throw new Error("Index freshness status was not available");
  const drift = [
    "filesAdded",
    "filesModified",
    "filesDeleted",
    "filesPending",
    "filesFailed",
  ];
  if (drift.some((key) => info.status[key] !== 0)) {
    throw new Error(
      `Existing index is not complete and fresh: ${JSON.stringify(Object.fromEntries(drift.map((key) => [key, info.status[key]])))}`,
    );
  }
  if (!(info.status.filesIndexed > 0) || !(info.status.entitiesIndexed > 0)) {
    throw new Error("Existing index contains no indexed evidence");
  }
}

async function assertNoDaemon(root) {
  if (await exists(join(root, ".zvec-grep", "locks", "daemon.json"))) {
    throw new Error(
      "Daemon lease exists; stop the daemon and clear its lease before freezing the benchmark",
    );
  }
}

async function outsideSource(root, path, label) {
  const absolute = resolve(path);
  if (isWithin(root, absolute))
    throw new Error(`${label} must be outside the source workspace`);
  let ancestor = dirname(absolute);
  const suffix = [absolute.split(sep).at(-1)];
  while (!(await exists(ancestor))) {
    suffix.unshift(ancestor.split(sep).at(-1));
    ancestor = dirname(ancestor);
  }
  const physical = join(await realpath(ancestor), ...suffix);
  if (isWithin(root, physical))
    throw new Error(`${label} must be outside the source workspace`);
  if (await exists(absolute)) {
    if (isWithin(root, await realpath(absolute)))
      throw new Error(`${label} resolves inside the source workspace`);
  }
  await mkdir(dirname(absolute), { recursive: true });
  return absolute;
}

/** Import production APIs and formatter from the explicitly selected installed package. */
export async function loadProduction(packageDir) {
  const directory = await realpath(packageDir);
  const pkgBytes = await readFile(join(directory, "package.json"));
  const pkg = JSON.parse(pkgBytes);
  if (pkg.name !== "@zvec/zvec-grep" || pkg.version !== "0.2.2") {
    throw new Error(
      `This benchmark requires @zvec/zvec-grep@0.2.2, found ${pkg.name}@${pkg.version}`,
    );
  }
  const fromPackage = (path) =>
    import(pathToFileURL(join(directory, path)).href);
  const [api, format, mcp, storage] = await Promise.all([
    fromPackage("dist/index.js"),
    fromPackage("dist/cli/format/context.js"),
    fromPackage("dist/mcp/tools.js"),
    fromPackage("dist/engine/storage/index.js"),
  ]);
  const require = createRequire(join(directory, "package.json"));
  const { StdioServerTransport } = await import(
    pathToFileURL(require.resolve("@modelcontextprotocol/server/stdio")).href
  );
  return {
    ...api,
    ...format,
    ...mcp,
    createWorkspaceIndexStorage: storage.createWorkspaceIndexStorage,
    openNativeCollection: require("@zvec/zvec").ZVecOpen,
    StdioServerTransport,
    packageIdentity: {
      name: pkg.name,
      version: pkg.version,
      directory,
      package_json_sha256: sha256(pkgBytes),
      entry_sha256: await fileDigest(join(directory, "dist/index.js")),
      dist_sha256: await directoryDigest(join(directory, "dist")),
      bridge_sha256: await fileDigest(fileURLToPath(import.meta.url)),
      node_version: process.version,
      platform: process.platform,
      arch: process.arch,
      dependencies: Object.fromEntries(
        await Promise.all(
          [
            "@zvec/zvec",
            "@modelcontextprotocol/server",
            "@modelcontextprotocol/core",
            "@huggingface/transformers",
            "@huggingface/tokenizers",
            "zod",
          ].map(async (name) => [name, await dependencyVersion(require, name)]),
        ),
      ),
    },
  };
}

// The public entity list collapses fragments and omits vectors. Audit the native
// collections instead, with every scalar field and every vector projected.
async function documentIdentity(production, info) {
  const records = [];
  const vectors = [];
  const counts = {};
  const dimension = info.workspaceIndex.embedding.dimension;
  for (const name of ["files.zvec", "index.zvec"]) {
    const collection = production.openNativeCollection(join(info.home, name), {
      readOnly: true,
    });
    const seen = new Set();
    try {
      const iterator = collection.iterDocsSync({ includeVector: true });
      try {
        for (const doc of iterator) {
          if (seen.has(doc.id))
            throw new Error(
              `Duplicate native document id in ${name}: ${doc.id}`,
            );
          seen.add(doc.id);
          if (name === "index.zvec") {
            const vector = doc.vectors?.embedding;
            if (
              !(Array.isArray(vector) || ArrayBuffer.isView(vector)) ||
              vector.length !== dimension ||
              !Array.from(vector).every(Number.isFinite)
            ) {
              throw new Error(
                `Native snapshot did not return a complete ${dimension}-dimension embedding for ${doc.id}`,
              );
            }
            vectors.push([doc.id, sha256(stableJson(doc.vectors))]);
          }
          records.push([
            name,
            doc.id,
            sha256(
              stableJson({
                id: doc.id,
                fields: doc.fields,
                vectors: doc.vectors,
              }),
            ),
          ]);
        }
      } finally {
        iterator.closeSync();
      }
    } finally {
      collection.closeSync();
    }
    counts[name] = seen.size;
  }
  if (
    counts["files.zvec"] !== info.status.filesStored ||
    counts["index.zvec"] < info.status.entitiesIndexed
  )
    throw new Error(
      "Native snapshot does not cover the indexed document state",
    );
  records.sort((a, b) =>
    stableJson(a.slice(0, 2)).localeCompare(stableJson(b.slice(0, 2))),
  );
  vectors.sort((a, b) => a[0].localeCompare(b[0]));
  return {
    sha256: sha256(stableJson(records)),
    vector_sha256: sha256(stableJson(vectors)),
    files: counts["files.zvec"],
    fragments: counts["index.zvec"],
    vector_count: vectors.length,
    embedding_dimension: dimension,
    all_fields_included: true,
    all_vectors_included: true,
  };
}

export async function captureSnapshot({
  root,
  embeddingModel,
  modelCacheDir,
  workingCopy = false,
  service,
  production,
}) {
  await assertNoDaemon(root);
  const info = await service.info({ root, includeStatus: true });
  assertFreshInfo(info, root, embeddingModel);
  const manifest = await readFile(join(info.home, "manifest.json"));
  const source = await sourceIdentity(root);
  const documents = await documentIdentity(production, info);
  const physical = await storageFiles(info.home);
  return {
    schema_version: SCHEMA_VERSION,
    created_at: now(),
    package: production.packageIdentity,
    runtime: {
      model_cache_dir: modelCacheDir ?? null,
      working_copy: workingCopy,
    },
    source,
    index: {
      id: info.workspaceIndex.id,
      home: info.home,
      index_version: info.workspaceIndex.indexVersion,
      embedding: info.workspaceIndex.embedding,
      manifest_sha256: sha256(manifest),
      documents,
      storage_sha256: sha256(stableJson(physical)),
      storage_files: physical,
      status: info.status,
    },
  };
}

export function compareSnapshots(
  expected,
  actual,
  { workingCopy = false } = {},
) {
  const mismatches = [];
  const beforeFiles = new Map(
    (expected.index?.storage_files ?? []).map((file) => [file.path, file]),
  );
  const afterFiles = new Map(
    (actual.index?.storage_files ?? []).map((file) => [file.path, file]),
  );
  const changedStorageFiles = [
    ...new Set([...beforeFiles.keys(), ...afterFiles.keys()]),
  ]
    .sort()
    .filter(
      (path) => beforeFiles.get(path)?.sha256 !== afterFiles.get(path)?.sha256,
    );
  if (expected.schema_version !== SCHEMA_VERSION)
    mismatches.push("snapshot schema");
  for (const key of ["root", "git_commit", "sha256"]) {
    if (expected.source?.[key] !== actual.source?.[key])
      mismatches.push(`source.${key}`);
  }
  for (const key of ["id", "home", "index_version", "manifest_sha256"]) {
    if (expected.index?.[key] !== actual.index?.[key])
      mismatches.push(`index.${key}`);
  }
  if (
    stableJson(expected.index?.embedding) !==
    stableJson(actual.index?.embedding)
  )
    mismatches.push("index.embedding");
  if (expected.index?.documents?.sha256 !== actual.index?.documents?.sha256)
    mismatches.push("index.documents");
  if (
    expected.index?.documents?.vector_sha256 !==
    actual.index?.documents?.vector_sha256
  )
    mismatches.push("index.vectors");
  if (
    workingCopy &&
    (expected.index?.documents?.all_vectors_included !== true ||
      actual.index?.documents?.all_vectors_included !== true)
  )
    mismatches.push("index.vector_projection_unverified");
  // Working-copy mode accepts physical changes only as explicitly reported
  // native storage drift. It does not classify them as harmless metadata.
  if (
    !workingCopy &&
    expected.index?.storage_sha256 !== actual.index?.storage_sha256
  )
    mismatches.push("index.storage");
  for (const key of [
    "name",
    "version",
    "package_json_sha256",
    "entry_sha256",
    "dist_sha256",
    "bridge_sha256",
    "node_version",
    "platform",
    "arch",
  ]) {
    if (expected.package?.[key] !== actual.package?.[key])
      mismatches.push(`package.${key}`);
  }
  if (
    stableJson(expected.package?.dependencies) !==
    stableJson(actual.package?.dependencies)
  )
    mismatches.push("package.dependencies");
  if (stableJson(expected.runtime) !== stableJson(actual.runtime))
    mismatches.push("runtime");
  return {
    unchanged: mismatches.length === 0,
    semantic_unchanged:
      mismatches.filter((mismatch) => mismatch !== "index.storage").length ===
      0,
    integrity_policy: workingCopy
      ? "working-copy-all-documents-and-vectors"
      : "strict-storage-bytes",
    mismatches,
    physical_storage_unchanged:
      expected.index?.storage_sha256 === actual.index?.storage_sha256,
    changed_storage_files: changedStorageFiles,
    storage_changes: changedStorageFiles.map((path) => ({
      path,
      before_sha256: beforeFiles.get(path)?.sha256 ?? null,
      after_sha256: afterFiles.get(path)?.sha256 ?? null,
      before_bytes: beforeFiles.get(path)?.bytes ?? null,
      after_bytes: afterFiles.get(path)?.bytes ?? null,
    })),
  };
}

export function retrievalRequest(root, query, mode, limit) {
  if (!query?.trim()) throw new Error("--query must be non-empty");
  if (!["hybrid", "fts", "vector"].includes(mode))
    throw new Error("--mode must be hybrid, fts, or vector");
  if (!Number.isInteger(limit) || limit < 1)
    throw new Error("--limit must be a positive integer");
  return {
    root,
    ...(mode === "hybrid" ? { query } : { routes: [{ mode, query }] }),
    limit,
    autoUpdate: false,
    trace: true,
  };
}

function errorRecord(error) {
  return {
    name: error.name ?? "Error",
    message: String(error.message ?? error),
    code: error.code,
  };
}

export async function createRuntime(options, production) {
  const root = await realpath(options.root);
  const log = await outsideSource(root, options.log, "Trace log");
  const snapshotPath = await outsideSource(root, options.snapshot, "Snapshot");
  const runId = options.runId ?? randomUUID();
  const modelCacheDir = options.modelCacheDir
    ? resolve(options.modelCacheDir)
    : undefined;
  if (
    modelCacheDir &&
    (isWithin(root, modelCacheDir) ||
      ((await exists(modelCacheDir)) &&
        isWithin(root, await realpath(modelCacheDir))))
  )
    throw new Error("Model cache must be outside the source workspace");
  const service = await production.createZvecGrep({
    root,
    embedding: options.embeddingModel,
    modelCacheDir,
  });
  let snapshot;
  let sequence = 0;
  const record = async (value) =>
    appendFile(
      log,
      `${JSON.stringify({ schema_version: SCHEMA_VERSION, run_id: runId, ...value })}\n`,
      { mode: 0o600 },
    );
  try {
    const captured = await captureSnapshot({
      root,
      embeddingModel: options.embeddingModel,
      modelCacheDir,
      workingCopy: options.workingCopy,
      service,
      production,
    });
    if (options.command === "preflight") {
      // Never silently replace a previously frozen experimental identity.
      await writeFile(snapshotPath, `${JSON.stringify(captured, null, 2)}\n`, {
        flag: "wx",
        mode: 0o600,
      });
      snapshot = captured;
      await record({ event: "preflight", at: now(), snapshot: captured });
    } else {
      snapshot = JSON.parse(await readFile(snapshotPath, "utf8"));
      const integrity = compareSnapshots(snapshot, captured, {
        workingCopy: options.workingCopy,
      });
      await record({
        event: "integrity",
        stage: "start",
        at: now(),
        ...integrity,
      });
      if (!integrity.unchanged)
        throw new Error(
          `Frozen snapshot mismatch: ${integrity.mismatches.join(", ")}`,
        );
      await record({
        event: "start",
        at: now(),
        package: production.packageIdentity,
        source_identity: { ...snapshot.source, files: undefined },
        index_identity: {
          ...snapshot.index,
          storage_files: undefined,
          status: undefined,
        },
      });
    }
  } catch (error) {
    await record({
      event: "error",
      stage: "preflight",
      at: now(),
      error: errorRecord(error),
    });
    await service.close();
    throw error;
  }
  const identity = {
    source_identity: { ...snapshot.source, files: undefined },
    index_identity: {
      ...snapshot.index,
      storage_files: undefined,
      status: undefined,
    },
  };
  let queue = Promise.resolve();
  async function performSearch(input, metadata) {
    const startedAt = now();
    const started = performance.now();
    const currentSequence = ++sequence;
    // Only explicitly allowed public search fields can reach context().
    const {
      query,
      queries,
      routes,
      fuse,
      limit,
      preferSymbol,
      symbolTypes,
      globs,
      insensitiveGlobs,
      fileTypes,
      excludedFileTypes,
      hidden,
      noIgnore,
      ignoreFiles,
      maxDepth,
      maxFileSizeBytes,
      follow,
      embeddingConcurrency,
      modifiedAfter,
      modifiedBefore,
    } = input;
    const request = {
      root,
      query,
      queries,
      routes,
      fuse,
      limit,
      preferSymbol,
      symbolTypes,
      globs,
      insensitiveGlobs,
      fileTypes,
      excludedFileTypes,
      hidden,
      noIgnore,
      ignoreFiles,
      maxDepth,
      maxFileSizeBytes,
      follow,
      embeddingConcurrency,
      modifiedAfter,
      modifiedBefore,
      autoUpdate: false,
      trace: true,
    };
    const base = {
      event: "search",
      sequence: currentSequence,
      started_at: startedAt,
      ...metadata,
      ...identity,
      request,
    };
    try {
      if (input.root && resolve(input.root) !== root)
        throw new Error("Search root must equal the frozen workspace root");
      if (input.rg || input.trackEntityId || input.debug)
        throw new Error("Only normal indexed search is allowed");
      await assertNoDaemon(root);
      if (
        sha256(await readFile(join(snapshot.index.home, "manifest.json"))) !==
        snapshot.index.manifest_sha256
      )
        throw new Error("Frozen index manifest changed");
      const contextStarted = performance.now();
      const result = await service.context(request);
      const contextDuration = performance.now() - contextStarted;
      const text = `freshness: fresh\n${production.formatAgentContextResult(result, { preview: "short" })}`;
      const event = {
        ...base,
        status: "success",
        duration_ms: performance.now() - started,
        context_duration_ms: contextDuration,
        text,
        text_bytes: Buffer.byteLength(text),
        text_sha256: sha256(text),
        result,
      };
      await record(event);
      return { event, response: { root, freshness: "fresh", result } };
    } catch (error) {
      await record({
        ...base,
        status: "error",
        duration_ms: performance.now() - started,
        error: errorRecord(error),
      });
      throw error;
    }
  }
  return {
    root,
    snapshot,
    record,
    search(input, metadata = { origin: "agent-mcp" }) {
      const next = queue.then(() => performSearch(input, metadata));
      queue = next.catch(() => undefined);
      return next;
    },
    async close() {
      await queue;
      await service.close();
      const verifier = await production.createZvecGrep({
        root,
        embedding: options.embeddingModel,
        modelCacheDir,
      });
      try {
        const actual = await captureSnapshot({
          root,
          embeddingModel: options.embeddingModel,
          modelCacheDir,
          workingCopy: options.workingCopy,
          service: verifier,
          production,
        });
        const integrity = compareSnapshots(snapshot, actual, {
          workingCopy: options.workingCopy,
        });
        await record({
          event: "integrity",
          stage: "end",
          at: now(),
          ...integrity,
          actual_index_storage_sha256: actual.index.storage_sha256,
        });
        if (!integrity.unchanged)
          throw new Error(
            `Frozen source/index changed: ${integrity.mismatches.join(", ")}; storage files: ${integrity.changed_storage_files.join(", ")}`,
          );
        await record({
          event: "end",
          at: now(),
          searches: sequence,
          integrity: options.workingCopy ? "semantic_unchanged" : "passed",
        });
      } finally {
        await verifier.close();
      }
    },
  };
}

export async function serve(runtime, production) {
  // Reuses the actual product's single-tool agent registration, schema, routing
  // instructions and renderer. Its autoUpdate field is unconditionally ignored
  // by our backend. This is an explicit benchmark bridge, not a native daemon.
  const server = production.createZvecGrepMcpServer(
    {
      search: async (input) =>
        (await runtime.search(input, { origin: "agent-mcp" })).response,
    },
    production.packageIdentity.version,
    { toolset: "agent" },
  );
  let finish;
  const closed = new Promise((resolveClosed) => {
    finish = resolveClosed;
  });
  server.server.onclose = finish;
  server.server.onerror = (error) => {
    process.stderr.write(`${error.message}\n`);
  };
  const stop = () => {
    server
      .close()
      .catch((error) => {
        process.stderr.write(`${error.message}\n`);
      })
      .finally(finish);
  };
  process.once("SIGTERM", stop);
  process.once("SIGINT", stop);
  try {
    await server.connect(new production.StdioServerTransport());
    await closed;
  } finally {
    process.removeListener("SIGTERM", stop);
    process.removeListener("SIGINT", stop);
    await server.close();
  }
}

export async function main(argv = process.argv.slice(2)) {
  const { values, positionals } = parseArgs({
    args: argv,
    allowPositionals: true,
    options: {
      root: { type: "string" },
      snapshot: { type: "string" },
      log: { type: "string" },
      "package-dir": { type: "string", default: DEFAULT_PACKAGE_DIR },
      "embedding-model": { type: "string" },
      "model-cache-dir": { type: "string" },
      "working-copy": { type: "boolean", default: false },
      "run-id": { type: "string" },
      query: { type: "string" },
      mode: { type: "string", default: "hybrid" },
      repetitions: { type: "string", default: "5" },
      limit: { type: "string", default: "10" },
    },
  });
  const [command] = positionals;
  if (
    positionals.length !== 1 ||
    !["preflight", "serve", "retrieve", "verify"].includes(command)
  ) {
    throw new Error(
      "Usage: readonly-search.mjs preflight|serve|retrieve|verify --root ROOT --snapshot OUTSIDE.json --log OUTSIDE.jsonl --package-dir ZG_0.2.2_DIR [--embedding-model local/potion-code-16m-v2 --model-cache-dir /models --working-copy] [--query TEXT --mode hybrid|fts|vector --repetitions 5 --limit 10]",
    );
  }
  if (!values.root || !values.snapshot || !values.log)
    throw new Error("--root, --snapshot and --log are required");
  const repetitions = Number(values.repetitions);
  if (!Number.isInteger(repetitions) || repetitions < 1)
    throw new Error("--repetitions must be a positive integer");
  const production = await loadProduction(values["package-dir"]);
  const runtime = await createRuntime(
    {
      command,
      root: values.root,
      snapshot: values.snapshot,
      log: values.log,
      embeddingModel: values["embedding-model"],
      modelCacheDir: values["model-cache-dir"],
      workingCopy: values["working-copy"],
      runId: values["run-id"],
    },
    production,
  );
  try {
    if (command === "serve") await serve(runtime, production);
    if (command === "retrieve") {
      const request = retrievalRequest(
        runtime.root,
        values.query,
        values.mode,
        Number(values.limit),
      );
      for (let repetition = 1; repetition <= repetitions; repetition++) {
        try {
          const { event } = await runtime.search(request, {
            origin: "retrieval-only",
            mode: values.mode,
            repetition,
            repetitions,
          });
          process.stdout.write(`${JSON.stringify(event)}\n`);
        } catch (error) {
          process.stdout.write(
            `${JSON.stringify({ status: "error", repetition, error: errorRecord(error) })}\n`,
          );
          process.exitCode = 1;
        }
      }
    }
    if (command === "preflight" || command === "verify") {
      process.stdout.write(
        `${JSON.stringify({ status: "ok", command, snapshot: resolve(values.snapshot), source_sha256: runtime.snapshot.source.sha256, index_document_sha256: runtime.snapshot.index.documents.sha256, package: production.packageIdentity })}\n`,
      );
    }
  } finally {
    await runtime.close();
  }
}

if (
  process.argv[1] &&
  resolve(process.argv[1]) === fileURLToPath(import.meta.url)
) {
  main().catch((error) => {
    process.stderr.write(
      `${JSON.stringify({ status: "error", error: errorRecord(error) })}\n`,
    );
    process.exitCode = 1;
  });
}
