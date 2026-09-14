import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, readFile, readdir, realpath, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { withBenchmarkRemoteEmbeddingAuthorization } from "./readonly-search.mjs";

// CI downloads only the public release tarball. Import its real authorization
// modules without installing native/vector/model dependencies or calling a model.
assert.ok(process.env.ZG_QA_AUTH_TEST_PACKAGE_DIR,
  "Set ZG_QA_AUTH_TEST_PACKAGE_DIR to an unpacked @zvec/zvec-grep@0.2.2 release");
const packageDir = resolve(process.env.ZG_QA_AUTH_TEST_PACKAGE_DIR);
const pkg = JSON.parse(await readFile(join(packageDir, "package.json"), "utf8"));
assert.equal(pkg.name, "@zvec/zvec-grep");
assert.equal(pkg.version, "0.2.2");
const targetApi = await import(pathToFileURL(join(packageDir, "dist/authorization/target.js")));
const operationApi = await import(pathToFileURL(join(packageDir, "dist/authorization/operation.js")));
const ENDPOINT = "https://embedding.example.invalid/compatible-mode/v1";
const MODEL = "qwen/qwen3.7-text-embedding";
const REQUIRED = { code: "ZVEC_GREP.ENGINE.AUTH.REMOTE_EMBEDDING_REQUIRED" };

async function fixture(t) {
  const root = await realpath(await mkdtemp(join(tmpdir(), "zg-sdk-authorization-")));
  t.after(() => rm(root, { recursive: true, force: true }));
  const old = {};
  for (const [key, value] of Object.entries({
    ZG_QA_ALLOW_REMOTE_EMBEDDING: "1", ZVEC_GREP_ENDPOINT: ENDPOINT,
    QWEN_API_KEY: "offline-test-secret-never-serialize",
  })) {
    old[key] = process.env[key];
    process.env[key] = value;
  }
  t.after(() => {
    for (const [key, value] of Object.entries(old)) {
      if (value === undefined) delete process.env[key];
      else process.env[key] = value;
    }
  });
  const permits = [];
  const production = {
    packageIdentity: { version: pkg.version },
    createRemoteEmbeddingTarget: targetApi.createRemoteEmbeddingTarget,
    createRemoteEmbeddingOperationPermit(target, scope) {
      const permit = operationApi.createRemoteEmbeddingOperationPermit(target, scope);
      permits.push(permit);
      return permit;
    },
    withRemoteEmbeddingOperationPermit: operationApi.withRemoteEmbeddingOperationPermit,
  };
  const guard = operationApi.remoteEmbeddingAuthorizationGuard({
    store: { hasGrant() { throw new Error("Once permits must never consult persisted grants"); } },
  });
  const request = { provider: "qwen", model: "qwen3.7-text-embedding", endpoint: ENDPOINT, purpose: "document" };
  const options = { root, embeddingModel: MODEL };
  const run = (operation) => withBenchmarkRemoteEmbeddingAuthorization(options, production, operation);
  return { root, production, permits, guard, request, options, run };
}

test("released SDK rejects an unpermitted request; index and context receive distinct once permits", async (t) => {
  const f = await fixture(t);
  await assert.rejects(f.guard(f.request), REQUIRED);
  for (const purpose of ["document", "query"]) {
    const result = await f.run(async () => {
      await Promise.resolve();
      await f.guard({ ...f.request, purpose });
      return purpose;
    });
    assert.equal(result, purpose);
    await assert.rejects(f.guard(f.request), REQUIRED);
  }
  assert.equal(f.permits.length, 2);
  assert.notEqual(f.permits[0].operationId, f.permits[1].operationId);
  for (const permit of f.permits) {
    assert.equal(permit.scope, "once");
    assert.deepEqual(permit.target.workspaceRoots, [f.root]);
    assert.equal(permit.target.endpoint, ENDPOINT);
  }
  assert.ok(!JSON.stringify(f.permits).includes(process.env.QWEN_API_KEY));
  assert.deepEqual(await readdir(f.root), []);
});

test("released guard fails closed on provider, model and endpoint mismatch", async (t) => {
  const f = await fixture(t);
  for (const mismatch of [
    { provider: "other" }, { model: "different-model" },
    { endpoint: "https://other.example.invalid/v1" },
  ]) {
    await assert.rejects(f.run(() => f.guard({ ...f.request, ...mismatch })), REQUIRED);
  }
  await assert.rejects(f.guard(f.request), REQUIRED);
});

test("once authorization does not leak to concurrent or subsequent operations after failure", async (t) => {
  const f = await fixture(t);
  let entered;
  const started = new Promise((resolve) => { entered = resolve; });
  let release;
  const hold = new Promise((resolve) => { release = resolve; });
  const running = f.run(async () => {
    await f.guard(f.request);
    entered();
    await hold;
    throw new Error("Expected operation failure");
  });
  await started;
  await assert.rejects(f.guard(f.request), REQUIRED);
  release();
  await assert.rejects(running, /Expected operation failure/);
  await assert.rejects(f.guard(f.request), REQUIRED);
});

test("local Potion does not create a permit and remote calls require explicit opt-in", async (t) => {
  const f = await fixture(t);
  assert.equal(await withBenchmarkRemoteEmbeddingAuthorization(
    { ...f.options, embeddingModel: "local/potion-code-16m-v2" },
    f.production, async () => "local unchanged"), "local unchanged");
  assert.equal(f.permits.length, 0);
  delete process.env.ZG_QA_ALLOW_REMOTE_EMBEDDING;
  await assert.rejects(f.run(() => f.guard(f.request)), REQUIRED);
  assert.equal(f.permits.length, 0);
});

test("opt-in validates the exact model, endpoint and pinned authorization API before invoking SDK", async (t) => {
  const f = await fixture(t);
  const shouldNotRun = () => { assert.fail("Invalid authorization configuration reached SDK"); };
  await assert.rejects(withBenchmarkRemoteEmbeddingAuthorization(
    { ...f.options, embeddingModel: "qwen/wrong" }, f.production, shouldNotRun), /exact/);
  for (const endpoint of ["", "not-a-url", "file:///tmp/model", "https://secret@example.invalid/v1", "https://example.invalid/?key=secret", "https://example.invalid/#secret"]) {
    process.env.ZVEC_GREP_ENDPOINT = endpoint;
    await assert.rejects(f.run(shouldNotRun), /endpoint/);
  }
  process.env.ZVEC_GREP_ENDPOINT = ENDPOINT;
  await assert.rejects(withBenchmarkRemoteEmbeddingAuthorization(f.options,
    { ...f.production, packageIdentity: { version: "0.2.3" } }, shouldNotRun), /Pinned/);
  await assert.rejects(withBenchmarkRemoteEmbeddingAuthorization(f.options,
    { ...f.production, withRemoteEmbeddingOperationPermit: undefined }, shouldNotRun), /Pinned/);
  assert.equal(f.permits.length, 0);
});
