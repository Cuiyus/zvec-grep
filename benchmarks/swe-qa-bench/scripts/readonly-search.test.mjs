import test from "node:test";
import assert from "node:assert/strict";
import {
  mkdtemp,
  mkdir,
  readFile,
  realpath,
  rm,
  symlink,
  writeFile,
} from "node:fs/promises";
import { execFileSync } from "node:child_process";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  assertFreshInfo,
  compareSnapshots,
  createRuntime,
  isWithin,
  retrievalRequest,
  sourceIdentity,
} from "./readonly-search.mjs";

async function fixture(t) {
  const directory = await realpath(
    await mkdtemp(join(tmpdir(), "zg-readonly-test-")),
  );
  t.after(() => rm(directory, { recursive: true, force: true }));
  const root = join(directory, "source");
  const home = join(root, ".zvec-grep");
  await mkdir(join(home, "files.zvec"), { recursive: true });
  await mkdir(join(home, "index.zvec"));
  await writeFile(join(root, "source.txt"), "The answer lives here.\n");
  await writeFile(join(home, "manifest.json"), '{"identity":"frozen"}\n');
  await writeFile(join(home, "files.zvec", "data"), "immutable-files");
  await writeFile(join(home, "index.zvec", "data"), "immutable-entities");
  for (const args of [
    ["init", "--quiet"],
    ["add", "source.txt"],
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
  const info = {
    root,
    home,
    indexed: true,
    source: "index",
    indexPolicy: "enabled",
    workspaceIndex: {
      id: "fixed-index",
      path: home,
      indexVersion: 1,
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
      filesStored: 1,
      entitiesIndexed: 1,
    },
  };
  const requests = [];
  const document = {
    entity: { id: "e1", content: "The answer lives here." },
    file: { id: "f1" },
    vector: new Float32Array(256),
  };
  const production = {
    packageIdentity: {
      name: "@zvec/zvec-grep",
      version: "0.2.2",
      package_json_sha256: "p",
      entry_sha256: "e",
    },
    createZvecGrep: async () => ({
      info: async () => info,
      context: async (request) => {
        requests.push(request);
        return {
          root,
          source: "index",
          items: [{ entityId: "e1" }],
          diagnostics: {},
          query:
            request.query ?? request.queries?.[0] ?? request.routes?.[0]?.query,
        };
      },
      close: async () => {},
      index: () => {
        throw new Error("MUST NEVER INDEX");
      },
    }),
    openNativeCollection: (path, options) => {
      assert.equal(options.readOnly, true);
      return {
        iterDocsSync: (projection) => {
          assert.deepEqual(projection, { includeVector: true });
          const docs = path.endsWith("files.zvec")
            ? [{ id: "f1", fields: document.file, vectors: {} }]
            : [
                {
                  id: "e1",
                  fields: document.entity,
                  vectors: { embedding: document.vector },
                },
              ];
          const iterator = docs[Symbol.iterator]();
          iterator.closeSync = () => {};
          return iterator;
        },
        closeSync() {},
      };
    },
    formatAgentContextResult: (result, options) => {
      assert.deepEqual(options, { preview: "short" });
      return `#1 source.txt:1\nsource:\n1: The answer lives here.\nquery: ${result.query}`;
    },
  };
  const options = {
    command: "preflight",
    root,
    snapshot: join(directory, "snapshot.json"),
    log: join(directory, "trace.jsonl"),
    embeddingModel: "local/potion-code-16m-v2",
    runId: "test",
  };
  return {
    directory,
    root,
    home,
    info,
    production,
    options,
    requests,
    document,
  };
}

test("pure vector/FTS requests never accidentally add a hybrid query", () => {
  for (const mode of ["fts", "vector"]) {
    const request = retrievalRequest("/repo", "question", mode, 10);
    assert.equal(request.query, undefined);
    assert.deepEqual(request.routes, [{ mode, query: "question" }]);
    assert.equal(request.autoUpdate, false);
    assert.equal(request.trace, true);
  }
  assert.equal(
    retrievalRequest("/repo", "question", "hybrid", 10).routes,
    undefined,
  );
  assert.throws(() => retrievalRequest("/repo", "", "vector", 10), /non-empty/);
  assert.throws(() => retrievalRequest("/repo", "x", "unknown", 10), /mode/);
});

test("preflight rejects missing, stale, empty or mismatched indexes", async (t) => {
  const f = await fixture(t);
  assertFreshInfo(f.info, f.root, f.options.embeddingModel);
  assert.throws(
    () => assertFreshInfo({ ...f.info, indexed: false }, f.root),
    /existing index/,
  );
  assert.throws(
    () => assertFreshInfo(f.info, f.root, "local/different"),
    /mismatch/,
  );
  assert.throws(
    () =>
      assertFreshInfo(
        { ...f.info, status: { ...f.info.status, filesModified: 1 } },
        f.root,
      ),
    /not complete and fresh/,
  );
  assert.throws(
    () => assertFreshInfo({ ...f.info, status: null }, f.root),
    /not available/,
  );
  assert.throws(
    () =>
      assertFreshInfo(
        { ...f.info, status: { ...f.info.status, entitiesIndexed: 0 } },
        f.root,
      ),
    /no indexed evidence/,
  );
  f.info.indexed = false;
  await assert.rejects(
    createRuntime(f.options, f.production),
    /existing index/,
  );
  assert.equal(f.requests.length, 0);
});

test("normal query is read-only, target-free, and records exact visible text", async (t) => {
  const f = await fixture(t);
  const preflight = await createRuntime(f.options, f.production);
  await preflight.close();
  const runtime = await createRuntime(
    { ...f.options, command: "serve" },
    f.production,
  );
  const { event } = await runtime.search({
    root: f.root,
    queries: ["question"],
    autoUpdate: true,
    freshness: "wait_for_fresh",
  });
  assert.equal(f.requests.length, 1);
  assert.equal(f.requests[0].autoUpdate, false);
  assert.equal(f.requests[0].trace, true);
  assert.equal(f.requests[0].freshness, undefined);
  assert.equal(f.requests[0].trackEntityId, undefined);
  assert.equal(
    event.text,
    "freshness: fresh\n#1 source.txt:1\nsource:\n1: The answer lives here.\nquery: question",
  );
  assert.equal(event.text_bytes, Buffer.byteLength(event.text));
  assert.equal(event.origin, "agent-mcp");
  assert.deepEqual(event.result.items, [{ entityId: "e1" }]);
  await assert.rejects(
    runtime.search({ root: f.directory, query: "escape" }),
    /frozen workspace root/,
  );
  await assert.rejects(
    runtime.search({ root: f.root, query: "x", trackEntityId: "e1" }),
    /normal indexed search/,
  );
  await runtime.close();
  const events = (await readFile(f.options.log, "utf8"))
    .trim()
    .split("\n")
    .map(JSON.parse);
  assert.equal(
    events.filter((e) => e.event === "search" && e.status === "error").length,
    2,
  );
  assert.equal(events.at(-1).integrity, "passed");
});

test("frozen snapshot cannot be overwritten and content changes fail startup", async (t) => {
  const f = await fixture(t);
  const preflight = await createRuntime(f.options, f.production);
  await preflight.close();
  await assert.rejects(createRuntime(f.options, f.production), /EEXIST/);
  await writeFile(
    join(f.root, "source.txt"),
    "Changed without changing git HEAD.\n",
  );
  await assert.rejects(
    createRuntime({ ...f.options, command: "serve" }, f.production),
    /source.sha256/,
  );
  assert.equal(f.requests.length, 0);
});

test("logical document mutation and any physical storage byte drift fail final integrity", async (t) => {
  const f = await fixture(t);
  const preflight = await createRuntime(f.options, f.production);
  await preflight.close();
  const runtime = await createRuntime(
    { ...f.options, command: "serve" },
    f.production,
  );
  await writeFile(join(f.home, "index.zvec", "runtime-cache"), "cache-only");
  await assert.rejects(runtime.close(), /index.storage/);
  const events = (await readFile(f.options.log, "utf8"))
    .trim()
    .split("\n")
    .map(JSON.parse);
  const check = events.filter((e) => e.event === "integrity").at(-1);
  assert.equal(check.unchanged, false);
  assert.equal(check.physical_storage_unchanged, false);
  await rm(join(f.home, "index.zvec", "runtime-cache"));
  const changed = await createRuntime(
    { ...f.options, command: "serve" },
    f.production,
  );
  f.document.entity.content = "corrupted document";
  await assert.rejects(changed.close(), /index.documents/);
});

test("manifest changes and daemon activation fail before query execution", async (t) => {
  const f = await fixture(t);
  const preflight = await createRuntime(f.options, f.production);
  await preflight.close();
  const runtime = await createRuntime(
    { ...f.options, command: "serve" },
    f.production,
  );
  await mkdir(join(f.home, "locks"));
  await writeFile(join(f.home, "locks", "daemon.json"), "{}");
  await assert.rejects(runtime.search({ query: "x" }), /Daemon lease/);
  await rm(join(f.home, "locks", "daemon.json"));
  await writeFile(join(f.home, "manifest.json"), "changed");
  await assert.rejects(runtime.search({ query: "x" }), /manifest changed/);
  assert.equal(f.requests.length, 0);
  await assert.rejects(runtime.close(), /manifest_sha256/);
});

test("snapshot and trace cannot be written into the readable source", async (t) => {
  const f = await fixture(t);
  await assert.rejects(
    createRuntime(
      { ...f.options, log: join(f.root, "secret.jsonl") },
      f.production,
    ),
    /outside/,
  );
  const link = join(f.directory, "logs-link");
  await symlink(f.root, link);
  await assert.rejects(
    createRuntime(
      { ...f.options, log: join(link, "secret.jsonl") },
      f.production,
    ),
    /outside/,
  );
  await assert.rejects(
    createRuntime(
      { ...f.options, log: join(link, "new-directory", "secret.jsonl") },
      f.production,
    ),
    /outside/,
  );
  assert.equal(isWithin("/app", "/application/secret"), false);
});

test("source digest detects tracked content drift and does not ingest index bytes", async (t) => {
  const f = await fixture(t);
  const initial = await sourceIdentity(f.root);
  await writeFile(join(f.home, "index.zvec", "data"), "new-cache");
  assert.equal((await sourceIdentity(f.root)).sha256, initial.sha256);
  await writeFile(join(f.root, "source.txt"), "different source");
  assert.notEqual((await sourceIdentity(f.root)).sha256, initial.sha256);
});

test("snapshot comparison does not treat an unavailable identity as a match", () => {
  const result = compareSnapshots({}, {});
  assert.equal(result.unchanged, false);
  assert.ok(result.mismatches.includes("snapshot schema"));
});

test("working copy reports physical drift but rejects vector-only mutations", async (t) => {
  const f = await fixture(t);
  const options = { ...f.options, workingCopy: true };
  const preflight = await createRuntime(options, f.production);
  await preflight.close();
  const runtime = await createRuntime(
    { ...options, command: "serve" },
    f.production,
  );
  await writeFile(
    join(f.home, "index.zvec", "native-metadata"),
    "runtime change",
  );
  await runtime.close();
  const events = (await readFile(f.options.log, "utf8"))
    .trim()
    .split("\n")
    .map(JSON.parse);
  const integrity = events
    .filter((event) => event.event === "integrity")
    .at(-1);
  assert.equal(integrity.semantic_unchanged, true);
  assert.equal(integrity.physical_storage_unchanged, false);
  assert.deepEqual(integrity.changed_storage_files, [
    "index.zvec/native-metadata",
  ]);
  assert.equal(events.at(-1).integrity, "semantic_unchanged");
  const changed = await createRuntime(
    { ...options, command: "serve" },
    f.production,
  );
  f.document.vector[0] = 1;
  await assert.rejects(changed.close(), /index.vectors/);
});

test("working copy refuses missing vector projections", async (t) => {
  const f = await fixture(t);
  f.document.vector = [];
  await assert.rejects(
    createRuntime({ ...f.options, workingCopy: true }, f.production),
    /complete 256-dimension embedding/,
  );
});
