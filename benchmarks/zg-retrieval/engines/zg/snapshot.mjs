// Passive, offline audit of the candidate's persisted index. This is never a search adapter.
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import { readFile, writeFile } from "node:fs/promises";
import {
  readJson,
  writeJson,
  objectHash,
  inside,
  fileHash,
} from "../../core/lib.mjs";

export async function snapshotIndex({ packageRoot, root, output }) {
  const load = (path) =>
    import(pathToFileURL(join(packageRoot, "dist", path)).href);
  const { createWorkspaceIndexStorage } = await load("engine/storage/index.js");
  const { scanRootPaths } = await load(
    "engine/pipeline/indexing/scanner/index.js",
  );
  const { EMBEDDING_MODEL_CATALOG } = await load("engine/models/catalog.js");
  const manifest = await readJson(join(root, ".zvec-grep/manifest.json"));
  const storage = createWorkspaceIndexStorage({
    storagePath: manifest.path,
    readOnly: true,
  });
  let files;
  try {
    files = storage
      .listFiles()
      .sort((a, b) => a.relativePath.localeCompare(b.relativePath));
  } finally {
    storage.close();
  }
  for (const file of files) {
    assert.ok(inside(root, file.absolutePath), "indexed file outside corpus");
    assert.ok(
      !file.relativePath.split(/[\\/]/).includes(".zvec-grep"),
      "index indexed itself",
    );
  }
  const scan = await scanRootPaths(manifest.id, manifest.rootPaths);
  const require = createRequire(join(packageRoot, "package.json"));
  const { ZVecOpen } = require("@zvec/zvec");
  const collection = ZVecOpen(join(manifest.path, "index.zvec"), {
    readOnly: true,
  });
  const filePaths = new Map(files.map((file) => [file.id, file.relativePath]));
  const fragments = [];
  const sourceLines = new Map();
  try {
    // Iterator avoids silently truncating large repositories at topk's native cap.
    assert.equal(
      typeof collection.iterDocsSync,
      "function",
      "candidate zvec lacks the complete read-only snapshot iterator",
    );
    for (const doc of collection.iterDocsSync({ includeVector: true })) {
      const path = filePaths.get(doc.fields.file_id);
      assert.ok(path, `fragment references unknown file: ${doc.id}`);
      assert.ok(doc.vectors?.embedding?.length, "snapshot omitted vectors");
      const range = JSON.parse(doc.fields.range_json);
      let contiguousSource = false;
      if (range.kind === "text" && doc.fields.content_kind === "text") {
        if (!sourceLines.has(path))
          sourceLines.set(
            path,
            (await readFile(join(root, path), "utf8")).split(/\r?\n/),
          );
        const lines = String(doc.fields.text).split(/\r?\n/);
        if (lines.at(-1) === "") lines.pop();
        const source = sourceLines.get(path);
        contiguousSource =
          lines.length > 0 &&
          lines.length <= range.endLine - range.startLine + 1 &&
          lines.every((line, index) => {
            const expected = source[range.startLine - 1 + index];
            return (
              line === expected ||
              (index === 0 && line === expected?.trimStart())
            );
          });
      }
      fragments.push({
        id: doc.id,
        path,
        group: doc.fields.group ?? null,
        fragment_index: doc.fields.fragment_index,
        range,
        contiguous_source_from_range_start: contiguousSource,
        content: doc.fields.text,
        fields_sha256: objectHash(doc.fields),
        vector_sha256: objectHash(Array.from(doc.vectors.embedding)),
      });
    }
    assert.equal(
      fragments.length,
      collection.stats.docCount,
      "incomplete fragment snapshot",
    );
  } finally {
    collection.closeSync();
  }
  fragments.sort((a, b) => a.id.localeCompare(b.id));
  const identityFiles = files.map((file) => ({
    ...file,
    lastModifiedTime: undefined,
    indexStatus: file.indexStatus
      ? { ...file.indexStatus, indexedTime: undefined }
      : undefined,
  }));
  const identity = {
    embedding: manifest.embedding,
    embeddingRuntime: manifest.embeddingRuntime,
    indexVersion: manifest.indexVersion,
    rootPaths: manifest.rootPaths,
    files: identityFiles,
    fragments: fragments.map(({ id, fields_sha256, vector_sha256 }) => ({
      id,
      fields_sha256,
      vector_sha256,
    })),
  };
  const summary = {
    schema_version: 1,
    logical_content_sha256: objectHash(identity),
    files: files.length,
    fragments: fragments.length,
    failed_files: files
      .filter((file) => file.indexStatus?.error)
      .map((file) => ({
        path: file.relativePath,
        error: file.indexStatus.error,
      })),
    model_catalog:
      Object.values(EMBEDDING_MODEL_CATALOG).find(
        (model) =>
          model.reference ===
          `${manifest.embedding.provider}/${manifest.embedding.model}`,
      ) ?? null,
    identity_excludes: [
      "filesystem mtime",
      "manifest createdTime/updatedTime",
      "file indexedTime",
      "locks/logs/native storage layout",
    ],
    stages: {
      corpus_scan:
        "available: post-build replay of the unchanged candidate scan policy; excluded paths may have no individual reason",
      persisted_chunks_and_vectors: "available: complete read-only snapshot",
      actual_embedding_inputs: "not_available",
      preselection_candidates: "not_available",
      fusion_and_selection: "not_available",
      final_visible_output: "available_in_raw_responses",
    },
  };
  await writeJson(join(output, "manifest.json"), manifest);
  await writeJson(join(output, "files.json"), files);
  await writeJson(join(output, "scan.json"), scan);
  await writeFile(
    join(output, "fragments.jsonl"),
    fragments.map((fragment) => JSON.stringify(fragment)).join("\n") + "\n",
  );
  summary.artifacts = Object.fromEntries(
    await Promise.all(
      ["manifest.json", "files.json", "scan.json", "fragments.jsonl"].map(
        async (path) => [path, await fileHash(join(output, path))],
      ),
    ),
  );
  await writeJson(join(output, "summary.json"), summary);
  return summary;
}

if (
  process.argv[1] &&
  import.meta.url === pathToFileURL(process.argv[1]).href
) {
  const [packageRoot, root, output] = process.argv.slice(2);
  await snapshotIndex({ packageRoot, root, output });
}
