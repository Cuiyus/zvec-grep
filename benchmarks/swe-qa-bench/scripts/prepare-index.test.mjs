import test from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import {
  mkdtemp,
  mkdir,
  readFile,
  realpath,
  rm,
  symlink,
  writeFile,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { main, prepareIndex } from "./prepare-index.mjs";

async function fixture(t) {
  const directory = await realpath(
    await mkdtemp(join(tmpdir(), "zg-prepare-test-")),
  );
  t.after(() => rm(directory, { recursive: true, force: true }));
  const root = join(directory, "source");
  await mkdir(root);
  await writeFile(join(root, "README.md"), "Source evidence.\n");
  for (const args of [
    ["init", "--quiet"],
    ["add", "README.md"],
    [
      "-c",
      "user.name=Test",
      "-c",
      "user.email=test@example.invalid",
      "commit",
      "--quiet",
      "-m",
      "fixture",
    ],
  ])
    execFileSync("git", ["-C", root, ...args]);
  const home = join(root, ".zvec-grep");
  const info = {
    root,
    home,
    indexed: true,
    source: "index",
    indexPolicy: "enabled",
    workspaceIndex: {
      path: home,
      rootPaths: [{ absolutePath: root }],
      embedding: {
        provider: "local",
        model: "potion-code-16m-v2",
        dimension: 256,
        metric: "cosine",
      },
    },
    status: {
      filesAdded: 0,
      filesModified: 0,
      filesDeleted: 0,
      filesPending: 0,
      filesFailed: 0,
      filesIndexed: 1,
      entitiesIndexed: 1,
    },
  };
  const calls = [];
  const service = {
    async index(options) {
      calls.push(["index", options]);
      options.onProgress({ phase: "done", filesIndexed: 1 });
      return { filesIndexed: 1, filesFailed: 0 };
    },
    async info(options) {
      calls.push(["info", options]);
      return info;
    },
    async close() {
      calls.push(["close"]);
    },
  };
  const production = {
    packageIdentity: { version: "0.2.2" },
    async createZvecGrep(options) {
      calls.push(["create", options]);
      return service;
    },
  };
  const progress = [];
  const options = {
    root,
    embeddingModel: "local/potion-code-16m-v2",
    modelCacheDir: join(directory, "models"),
    log: join(directory, "index-build.json"),
    onProgress: (event) => progress.push(event),
  };
  return {
    directory,
    root,
    info,
    calls,
    service,
    production,
    progress,
    options,
  };
}

test("setup awaits one normal production index, checks freshness, closes and preserves source", async (t) => {
  const f = await fixture(t);
  const result = await prepareIndex(f.options, f.production);
  assert.deepEqual(
    f.calls.map(([name]) => name),
    ["create", "index", "info", "close"],
  );
  assert.deepEqual(Object.keys(f.calls[1][1]).sort(), ["onProgress", "root"]);
  assert.deepEqual(f.calls[2][1], { root: f.root, includeStatus: true });
  assert.equal(f.calls[0][1].embedding, "local/potion-code-16m-v2");
  assert.equal(f.calls[0][1].modelCacheDir, f.options.modelCacheDir);
  assert.equal(result.status, "completed");
  assert.equal(result.fresh, true);
  assert.equal(result.source_unchanged, true);
  assert.equal(f.progress[0].event, "index-progress");
  assert.ok(result.duration_ms >= result.build_duration_ms);
  assert.equal(
    JSON.parse(await readFile(f.options.log, "utf8")).status,
    "completed",
  );
});

test("incomplete build fails even if indexing returned normally", async (t) => {
  const f = await fixture(t);
  f.info.status.filesPending = 1;
  await assert.rejects(
    prepareIndex(f.options, f.production),
    /not complete and fresh/,
  );
  const report = JSON.parse(await readFile(f.options.log, "utf8"));
  assert.equal(report.status, "failed");
  assert.equal(report.fresh, false);
  assert.equal(report.source_unchanged, true);
  assert.equal(f.calls.at(-1)[0], "close");
});

test("uniform resource cap reaches SDK and report without changing source or supplying selected paths", async (t) => {
  const f = await fixture(t);
  f.options.maxFileSizeBytes = 1048576;
  const report = await prepareIndex(f.options, f.production);
  const indexOptions = f.calls.find(([name]) => name === "index")[1];
  assert.deepEqual(Object.keys(indexOptions).sort(), ["maxFileSizeBytes", "onProgress", "root"]);
  assert.equal(indexOptions.maxFileSizeBytes, 1048576);
  assert.deepEqual(report.index_options, {
    root: f.root, selection: "production-defaults-with-uniform-file-size-cap",
    maxFileSizeBytes: 1048576, query_or_gold_provided: false,
  });
  assert.equal(report.source_unchanged, true);
});

test("invalid SDK or CLI resource caps fail before loading a package or indexing", async (t) => {
  const f = await fixture(t);
  for (const value of [0, -1, 1.5, true, "1048576", Number.MAX_SAFE_INTEGER + 1]) {
    await assert.rejects(prepareIndex({ ...f.options, maxFileSizeBytes: value }, f.production), /positive safe integer/);
  }
  for (const value of ["0", "-1", "1.5", "1e6", "NaN", "9007199254740992"]) {
    await assert.rejects(main(["--root", f.root, "--package-dir", "/must-not-load",
      "--model-cache-dir", f.options.modelCacheDir, "--log", f.options.log,
      "--max-file-size-bytes=" + value]), /positive safe integer/);
  }
  assert.equal(f.calls.length, 0);
});

test("index errors are preserved in an external report and service closes", async (t) => {
  const f = await fixture(t);
  f.service.index = async () => {
    throw new Error("test embedding unavailable");
  };
  await assert.rejects(
    prepareIndex(f.options, f.production),
    /embedding unavailable/,
  );
  const report = JSON.parse(await readFile(f.options.log, "utf8"));
  assert.equal(report.error.message, "test embedding unavailable");
  assert.equal(report.source_unchanged, true);
  assert.equal(f.calls.at(-1)[0], "close");
  assert.equal(f.calls.filter(([name]) => name === "info").length, 0);
});

test("tracked source changes invalidate preparation", async (t) => {
  const f = await fixture(t);
  f.service.index = async () => {
    await writeFile(join(f.root, "README.md"), "Changed.\n");
    return {};
  };
  await assert.rejects(
    prepareIndex(f.options, f.production),
    /Tracked source changed/,
  );
  const report = JSON.parse(await readFile(f.options.log, "utf8"));
  assert.equal(report.status, "failed");
  assert.equal(report.source_unchanged, false);
});

test("logs and model cache cannot resolve inside source", async (t) => {
  const f = await fixture(t);
  await assert.rejects(
    prepareIndex(
      { ...f.options, log: join(f.root, "new", "log.json") },
      f.production,
    ),
    /outside source/,
  );
  const linked = join(f.directory, "source-link");
  await symlink(f.root, linked);
  await assert.rejects(
    prepareIndex(
      { ...f.options, modelCacheDir: join(linked, "new", "models") },
      f.production,
    ),
    /inside source/,
  );
  assert.equal(f.calls.length, 0);
});

test("setup does not overwrite an existing evidence report", async (t) => {
  const f = await fixture(t);
  await writeFile(f.options.log, "prior report");
  await assert.rejects(prepareIndex(f.options, f.production), /already exists/);
  assert.equal(await readFile(f.options.log, "utf8"), "prior report");
  assert.equal(f.calls.length, 0);
});

test("remote index runs inside one explicit SDK permit without serializing credentials", async (t) => {
  const f = await fixture(t);
  const env = { ZG_QA_ALLOW_REMOTE_EMBEDDING: "1", ZVEC_GREP_ENDPOINT: "https://embedding.example.invalid/v1", QWEN_API_KEY: "offline-secret-do-not-log" };
  for (const [name, value] of Object.entries(env)) {
    const previous = process.env[name];
    process.env[name] = value;
    t.after(() => { if (previous === undefined) delete process.env[name]; else process.env[name] = previous; });
  }
  f.options.embeddingModel = "qwen/qwen3.7-text-embedding";
  Object.assign(f.info.workspaceIndex.embedding, { provider: "qwen", model: "qwen3.7-text-embedding" });
  let active = false;
  let entered = 0;
  f.production.createRemoteEmbeddingTarget = async (target) => {
    assert.deepEqual(target, { roots: [f.root], provider: "qwen", model: "qwen3.7-text-embedding", endpoint: env.ZVEC_GREP_ENDPOINT });
    return target;
  };
  f.production.createRemoteEmbeddingOperationPermit = (target, scope) => {
    assert.equal(scope, "once");
    return { target, scope };
  };
  f.production.withRemoteEmbeddingOperationPermit = async (_permit, operation) => {
    entered++;
    active = true;
    try { return await operation(); } finally { active = false; }
  };
  const index = f.service.index;
  f.service.index = async (options) => {
    assert.equal(active, true, "SDK indexing must inherit its operation permit");
    return await index(options);
  };
  const info = f.service.info;
  f.service.info = async (options) => {
    assert.equal(active, false, "Read-only info must not retain the indexing permit");
    return await info(options);
  };
  assert.equal((await prepareIndex(f.options, f.production)).status, "completed");
  assert.equal(entered, 1);
  assert.equal(active, false);
  assert.ok(!(await readFile(f.options.log, "utf8")).includes(env.QWEN_API_KEY));
});
