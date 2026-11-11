import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { readFile, readdir, writeFile } from "node:fs/promises";
import { join, resolve } from "node:path";
import { promisify } from "node:util";
import { loadSuite } from "../core/suite.mjs";
import { loadPilot } from "../expansion/datasets.mjs";

const [suite, output, reader, candidateCommit] = process.argv.slice(2);
const expectedGroups = { sweqa: 11, beir: 6, duretrieval: 1, quarry: 20 };
assert.ok(Object.hasOwn(expectedGroups, suite));
assert.match(candidateCommit, /^[a-f0-9]{40}$/);
const model = suite === "sweqa"
  ? (await loadSuite()).protocol.model
  : (await loadPilot(suite)).model;

const run = promisify(execFile);
const read = async (file) => JSON.parse(await readFile(file, "utf8"));
const evidence = suite === "sweqa" ? output : join(output, "evidence");
const stage = suite === "sweqa" ? "stages/after/status.json" : "status-after/status.json";
const groups = [];

for (const entry of (await readdir(evidence, { withFileTypes: true })).sort((a, b) => a.name.localeCompare(b.name, "en"))) {
  if (!entry.isDirectory()) continue;
  let status;
  try {
    status = await read(join(evidence, entry.name, stage));
  } catch (error) {
    if (error.code === "ENOENT") continue;
    throw error;
  }
  assert.equal(status.embedding, model);
  assert.equal(status.files_pending, 0);
  assert.equal(status.files_failed, 0);
  assert.equal(resolve(status.index_path), resolve(status.root, ".zvec-grep/index"));
  const fragmentTables = (await readdir(status.index_path, { withFileTypes: true }))
    .filter((item) => item.isDirectory() && /^fragments_[0-9a-f]+$/.test(item.name))
    .map((item) => item.name)
    .sort();
  assert.ok(fragmentTables.length, `No fragment collections for ${entry.name}`);
  const tables = ["entities", ...fragmentTables];
  const library = join(output, "consumer/node_modules/@zvec/zvec-grep/lib");
  const { stdout } = await run(reader, tables.map((name) => join(status.index_path, name)), {
    env: { ...process.env, LD_LIBRARY_PATH: library },
    timeout: 120_000,
  });
  const counts = [...stdout.matchAll(/^ZG_COUNT:(\d+)$/gm)].map((match) => Number(match[1]));
  assert.equal(counts.length, tables.length);
  assert.ok(counts.every(Number.isSafeInteger));
  const [entities, ...fragmentCounts] = counts;
  const fragments = fragmentCounts.reduce((total, count) => total + count, 0);
  assert.equal(entities, status.entities_indexed, `Stored/public Entity count mismatch for ${entry.name}`);
  assert.ok(fragments >= entities, `Fewer fragments than Entities for ${entry.name}`);
  groups.push({
    id: entry.name,
    files: status.files_indexed,
    entities,
    fragments,
    collections: Object.fromEntries(tables.map((name, i) => [name, counts[i]])),
  });
}

assert.equal(groups.length, expectedGroups[suite], "Incomplete index evidence");
const totals = Object.fromEntries(["files", "entities", "fragments"].map((field) => [
  field, groups.reduce((total, group) => total + group[field], 0),
]));
await writeFile(join(output, "index-counts.json"), JSON.stringify({
  suite, model, candidate_commit: candidateCommit, groups, totals,
}, null, 2) + "\n");
const summary = `| Indexes | Files | Entities | Fragments |\n| ---: | ---: | ---: | ---: |\n| ${groups.length} | ${totals.files} | ${totals.entities} | ${totals.fragments} |\n`;
await writeFile(process.env.GITHUB_STEP_SUMMARY, `## ${suite} index counts\n\n${summary}`, { flag: "a" });
