import assert from "node:assert/strict";
import test from "node:test";
import { embeddingRuntime } from "../core/embedding.mjs";

test("remote embedding uses one explicit HTTPS endpoint for indexing and MCP consent", () => {
  const model = "qwen/qwen3.7-text-embedding";
  const endpoint = "https://example.test/compatible-mode/v1/embeddings";
  const runtime = embeddingRuntime(model, {
    ZVEC_GREP_API_KEY: "test-key",
    ZVEC_GREP_ENDPOINT: endpoint,
  });
  assert.equal(runtime.remote, true);
  assert.deepEqual(runtime.indexArguments, [
    "--endpoint",
    endpoint,
    "--allow-remote",
  ]);
  assert.deepEqual(runtime.grantArguments("/workspace"), [
    "auth",
    "grant",
    "/workspace",
    "--capability",
    "embedding",
    "--scope",
    "workspace",
    "--embedding",
    model,
    "--endpoint",
    endpoint,
  ]);
  assert.ok(!JSON.stringify(runtime).includes("test-key"));
});

test("remote embedding refuses missing credentials and insecure destinations", () => {
  const model = "qwen/qwen3.7-text-embedding";
  assert.throws(() => embeddingRuntime(model, {}), /ZVEC_GREP_API_KEY/);
  assert.throws(
    () => embeddingRuntime(model, { ZVEC_GREP_API_KEY: "test-key" }),
    /ZVEC_GREP_ENDPOINT/,
  );
  assert.throws(
    () => embeddingRuntime(model, {
      ZVEC_GREP_API_KEY: "test-key",
      ZVEC_GREP_ENDPOINT: "http://example.test/embeddings",
    }),
    /HTTPS/,
  );
});
