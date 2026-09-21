import assert from "node:assert/strict";
import { join } from "node:path";
import test from "node:test";
import {
  findSchemaVariant,
  isProductPreparationFailure,
  nativeCandidate,
  schemaAllowsType,
} from "../engines/zg/run.mjs";
import { parseNativeStatus } from "../engines/zg/snapshot.mjs";

test("the packed Rust npm metadata resolves to a native zg binary", () => {
  const root = "/tmp/candidate/node_modules/@zvec/zvec-grep";
  assert.deepEqual(
    nativeCandidate(root, "/tmp/candidate", { bin: { zg: "bin/zg" } }),
    {
      consumer: "/tmp/candidate",
      packageRoot: root,
      cli: join(root, "bin/zg"),
      runtime: "rust-native",
    },
  );
  assert.throws(
    () => nativeCandidate(root, "/tmp/candidate", { bin: { zg: "cli.mjs" } }),
    /native Rust CLI/,
  );
  assert.throws(
    () => nativeCandidate(root, "/tmp/candidate", { bin: { zg: "../zg" } }),
    /escapes/,
  );
});

test("public Rust status output supplies a stable ready-index audit", () => {
  const status = parseNativeStatus(`Workspace index: ready
Root: /tmp/corpus
Index path: /tmp/corpus/.zvec-grep
Nested Git repositories: excluded
Embedding: local/potion-code-16m-v2
FTS: tokenizer=unicode filters=lowercase
Files: scanned=12 indexed=10 pending=0 failed=0
Entities: 42
Indexed source size: 8192 bytes
`);
  assert.deepEqual(status, {
    state: "ready",
    root: "/tmp/corpus",
    index_path: "/tmp/corpus/.zvec-grep",
    embedding: "local/potion-code-16m-v2",
    files_scanned: 12,
    files_indexed: 10,
    files_pending: 0,
    files_failed: 0,
    entities_indexed: 42,
    indexed_source_bytes: 8192,
  });
  assert.throws(
    () =>
      parseNativeStatus(`Workspace index: ready
Root: /tmp/corpus
Index path: /tmp/corpus/.zvec-grep
Embedding: local/potion-code-16m-v2
Files: scanned=12 indexed=10 pending=1 failed=0
Entities: 42
Indexed source size: 8192 bytes
`),
    /pending/,
  );
});

test("Rust-generated optional and referenced JSON Schema variants are resolved", () => {
  const root = {
    $defs: {
      QueryList: {
        anyOf: [
          { type: "string" },
          { type: "array", items: { type: "string", maxLength: 4000 } },
        ],
      },
    },
    properties: {
      fts: {
        anyOf: [{ $ref: "#/$defs/QueryList" }, { type: "null" }],
      },
    },
  };
  const array = findSchemaVariant(
    root.properties.fts,
    root,
    (entry) => entry.type === "array",
  );
  assert.equal(array.items.maxLength, 4000);
});

test("Rust JSON Schema nullable type arrays retain their concrete type", () => {
  assert.equal(schemaAllowsType({ type: ["string", "null"] }, "string"), true);
  assert.equal(
    schemaAllowsType({ type: ["integer", "null"] }, "integer"),
    true,
  );
  assert.equal(schemaAllowsType({ type: ["string", "null"] }, "array"), false);
});

test("public index-readiness failures are product errors", () => {
  assert.equal(isProductPreparationFailure("index", new Error("failed")), true);
  assert.equal(
    isProductPreparationFailure(
      "snapshot",
      Object.assign(new Error("not ready"), { result: { code: 1 } }),
    ),
    true,
  );
  assert.equal(
    isProductPreparationFailure("snapshot", new Error("bad harness path")),
    false,
  );
  assert.equal(
    isProductPreparationFailure("mcp_contract", new Error("schema mismatch")),
    false,
  );
});
