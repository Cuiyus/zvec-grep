// Supplementary storage audit. Native inspection uses a disposable copy only.
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { cp, lstat, mkdtemp, readdir, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { basename, join, relative, resolve } from "node:path";
import { pathToFileURL } from "node:url";

let initialized = false;

export async function inventory(root) {
  const entries = [];
  async function visit(path) {
    const stat = await lstat(path);
    const type = stat.isDirectory() ? "directory" : stat.isFile() ? "file" : stat.isSymbolicLink() ? "symlink" : "other";
    entries.push({ path: relative(root, path) || ".", type,
      bytes: stat.size, allocated_bytes: stat.blocks * 512 });
    if (type === "directory")
      for (const name of (await readdir(path)).sort()) await visit(join(path, name));
  }
  await visit(root);
  return entries;
}

export function footprint(entries) {
  return {
    file_bytes: entries.filter(e => e.type === "file").reduce((n, e) => n + e.bytes, 0),
    allocated_bytes: entries.reduce((n, e) => n + e.allocated_bytes, 0),
    file_count: entries.filter(e => e.type === "file").length,
    directory_count: entries.filter(e => e.type === "directory").length,
    symlink_count: entries.filter(e => e.type === "symlink").length,
  };
}

function subtree(entries, prefix) {
  return entries.filter(e => e.path === prefix || e.path.startsWith(`${prefix}/`));
}

function role(name) {
  if (name === "files.zvec" || name === "files") return "files";
  if (name === "entities") return "entities";
  if (name === "directories") return "directories";
  if (name === "index.zvec" || name.startsWith("fragments_")) return "fragments";
  return null;
}

async function discover(root, activePath) {
  const paths = [];
  async function visit(path) {
    if (role(basename(path))) { paths.push(path); return; }
    for (const dirent of await readdir(path, { withFileTypes: true }))
      if (dirent.isDirectory()) await visit(join(path, dirent.name));
  }
  if (basename(activePath) === "index.zvec") {
    paths.push(activePath, join(root, "files.zvec"));
  } else await visit(activePath);
  return paths.sort();
}

export async function captureIndexStatistics({ status, output, sdkPath }) {
  const rustReader = process.env.INDEX_STATS_RUST_READER;
  assert.ok(sdkPath || rustReader, "a pinned native reader is required");
  const home = join(status.root, ".zvec-grep");
  const activePath = resolve(status.index_path);
  assert.ok(activePath.startsWith(`${home}/`), "index is outside the workspace metadata home");
  const entries = await inventory(home);
  const paths = await discover(home, activePath);
  assert.ok(paths.length >= 2, "index collections were not discovered");
  const sdk = rustReader ? null : await import(pathToFileURL(sdkPath).href);
  if (sdk && !initialized) {
    sdk.ZVecInitialize({ logLevel: sdk.ZVecLogLevel.WARN });
    initialized = true;
  }
  const copy = await mkdtemp(join(tmpdir(), "zg-index-reader-"));
  const collections = [];
  const files = [];
  let sdkVersion;
  try {
    sdkVersion = rustReader ? "zvec-rust@0.7.2" : JSON.parse(await readFile(resolve(sdkPath, "../../package.json"), "utf8")).version;
    for (let i = 0; i < paths.length; i++) {
      const path = paths[i];
      const destination = join(copy, String(i));
      await cp(path, destination, { recursive: true });
      if (rustReader) {
        const result = spawnSync(rustReader, [destination, role(basename(path))], { encoding: "utf8", maxBuffer: 32 * 1024 * 1024 });
        await writeFile(join(output, `native-reader-${i}.json`), JSON.stringify({ code: result.status, signal: result.signal, stdout: result.stdout, stderr: result.stderr }));
        assert.equal(result.status, 0, `Rust native reader failed: ${result.stderr}`);
        const line = result.stdout.split("\n").find(s => s.startsWith("ZG_INDEX_STATS_JSON:"));
        assert.ok(line, "Rust reader omitted JSON statistics");
        const native = JSON.parse(line.slice("ZG_INDEX_STATS_JSON:".length));
        const fragment = role(basename(path)) === "fragments";
        const descriptor = fragment ? JSON.parse(await readFile(join(path, "../schema.json"), "utf8")) : null;
        collections.push({ path: relative(home, path), role: role(basename(path)),
          schema_name: native.schema_name, doc_count: native.doc_count,
          index_completeness: native.index_completeness,
          vectors: descriptor ? descriptor.embeddings.map(e => ({ name: "embedding", dimension: e.dimension,
            data_type: "VectorFp32", metric: e.metric, index_params: { index_type: "HNSW", m: 16, ef_construction: 200 },
            parameter_source: "pinned Rust fragments_schema; dimension/metric from persisted schema.json" })) : [],
          footprint: footprint(subtree(entries, relative(home, path))) });
        files.push(...native.files);
        await rm(destination, { recursive: true, force: true });
        continue;
      }
      const collection = sdk.ZVecOpen(destination, { readOnly: true });
      try {
        const fields = collection.schema.fields();
        const vectors = collection.schema.vectors();
        const item = { path: relative(home, path), role: role(basename(path)),
          schema_name: collection.schema.name, doc_count: collection.stats.docCount,
          index_completeness: collection.stats.indexCompleteness,
          fields: fields.map(f => ({ name: f.name, data_type: f.dataType,
            nullable: f.nullable, index_params: f.indexParams })),
          vectors: vectors.map(v => ({ name: v.name, dimension: v.dimension,
            data_type: v.dataType, index_params: v.indexParams })),
          footprint: footprint(subtree(entries, relative(home, path))) };
        collections.push(item);
        if (item.role === "files") {
          const isRust = fields.some(f => f.name === "payload");
          const outputFields = isRust ? ["payload", "relative_path"] :
            ["file_id", "relative_path", "size_bytes", "entity_count", "error", "has_index_status"];
          for (const doc of collection.iterDocsSync({ outputFields, includeVector: false })) {
            const f = doc.fields;
            if (isRust) {
              const payload = JSON.parse(f.payload);
              files.push({ id: payload.id, relative_path: f.relative_path,
                size_bytes: payload.snapshot.size_bytes,
                entity_count: payload.index_status.entity_count ?? 0,
                indexed: payload.index_status.kind === "indexed" });
            } else files.push({ id: f.file_id, relative_path: f.relative_path,
              size_bytes: f.size_bytes, entity_count: f.entity_count,
              indexed: f.has_index_status === true && !f.error });
          }
        }
      } finally { collection.closeSync(); }
      await rm(destination, { recursive: true, force: true });
    }
  } finally { await rm(copy, { recursive: true, force: true }); }
  const indexedFiles = files.filter(f => f.indexed);
  const entities = indexedFiles.reduce((n, f) => n + f.entity_count, 0);
  assert.equal(indexedFiles.length, status.files_indexed, "persisted/public file counts differ");
  assert.equal(entities, status.entities_indexed, "persisted/public entity counts differ");
  const entityTables = collections.filter(c => c.role === "entities");
  if (entityTables.length)
    assert.equal(entityTables.reduce((n, c) => n + c.doc_count, 0), entities);
  const fragmentTables = collections.filter(c => c.role === "fragments");
  assert.ok(fragmentTables.length > 0 && fragmentTables.every(c => c.vectors.length > 0));
  const fragments = fragmentTables.reduce((n, c) => n + c.doc_count, 0);
  assert.ok(fragments >= entities, "fragment projection rows are below entity count");
  const sourceBytes = indexedFiles.reduce((n, f) => n + f.size_bytes, 0);
  const total = footprint(entries);
  const report = {
    schema_version: 1, captured_at: new Date().toISOString(), status,
    index_home: home, active_index_path: activePath,
    measurement: {
      scope: "entire workspace .zvec-grep directory, including metadata and active/inactive generations",
      excludes: ["source corpus", "embedding model cache", "consumer packages", "runtime home", "temporary reader copy"],
      logical: "sum of regular-file st_size; not archive size",
      allocated: "sum of st_blocks * 512 for files, directories and links (filesystem allocation)",
      fragment_definition: "persisted retrieval projection rows in index.zvec (Node) or fragments_* model collections (Rust)",
      inspection: "runtime-matched native reader, readOnly=true, disposable collection copies; original index never opened by reader",
      sdk_version: sdkVersion,
    },
    totals: { ...total, indexed_files: indexedFiles.length, entities, fragments,
      indexed_source_bytes: sourceBytes,
      fragments_per_file: fragments / indexedFiles.length,
      fragments_per_entity: fragments / entities,
      index_file_bytes_per_fragment: total.file_bytes / fragments,
      index_file_bytes_per_source_byte: total.file_bytes / sourceBytes },
    components: Object.fromEntries([...new Set(entries.filter(e => e.path !== ".").map(e => e.path.split("/")[0]))]
      .map(prefix => [prefix, footprint(subtree(entries, prefix))])),
    collections,
    files: files.sort((a, b) => String(a.relative_path).localeCompare(String(b.relative_path))),
    inventory: entries,
  };
  await writeFile(join(output, "index-statistics.json"), `${JSON.stringify(report, null, 2)}\n`);
  return report;
}
