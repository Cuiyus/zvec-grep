import assert from "node:assert/strict";
import { mkdir, readFile, realpath, writeFile } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { fileHash, inside, readJson, run, sha256 } from "../core/io.mjs";
import { prepareCorpus } from "../core/corpus.mjs";

const data = join(dirname(fileURLToPath(import.meta.url)), "data");
const MODES = ["hybrid", "fts", "vector"];

export async function loadPilot(name) {
  const files = {
    beir: "beir-scifact10.json",
    quarry: "quarry10.json",
    duretrieval: "duretrieval10.json",
  };
  assert.ok(Object.hasOwn(files, name), `unknown pilot: ${name}`);
  const lock = await readJson(join(data, files[name]));
  assert.equal(lock.schema_version, 1);
  assert.equal(lock.tasks.length, 10);
  assert.equal(new Set(lock.tasks.map((task) => task.id)).size, 10);
  assert.equal(
    lock.model,
    name === "quarry"
      ? "local/potion-code-16m-v2"
      : "local/potion-multilingual-128m",
  );
  for (const task of lock.tasks) {
    assert.ok(typeof task.query === "string" && task.query.trim());
    if (name !== "quarry") {
      assert.ok(task.qrels.length > 0);
      assert.ok(task.qrels.every((row) => row.relevance > 0));
    } else {
      assert.match(task.revision, /^[a-f0-9]{40}$/);
      assert.ok(task.positive_units.length > 0);
    }
  }
  return { name, lock, modes: MODES };
}

async function download(url, destination, expectedHash) {
  await mkdir(dirname(destination), { recursive: true });
  await run("curl", [
    "--fail",
    "--location",
    "--silent",
    "--show-error",
    "--retry",
    "3",
    "--retry-delay",
    "1",
    url,
    "--output",
    destination,
  ]);
  assert.equal(
    await fileHash(destination),
    expectedHash,
    `source hash mismatch: ${url}`,
  );
}

function jsonl(content) {
  return content
    .trimEnd()
    .split("\n")
    .map((line) => JSON.parse(line));
}

function qrels(content) {
  const rows = content.trimEnd().split(/\r?\n/);
  assert.equal(rows.shift(), "query-id\tcorpus-id\tscore");
  const byQuery = new Map();
  for (const row of rows) {
    const [query, document_id, grade] = row.split("\t");
    assert.ok(query && document_id && /^\d+$/.test(grade));
    const relevant = byQuery.get(query) ?? [];
    relevant.push({ document_id, relevance: Number(grade) });
    byQuery.set(query, relevant);
  }
  return byQuery;
}

async function mirrorRows(lock, config, split, count) {
  const pages = Array.from({ length: Math.ceil(count / 100) }, (_, index) => ({
    offset: index * 100,
    length: Math.min(100, count - index * 100),
  }));
  const rows = [];
  for (let index = 0; index < pages.length; index += 4) {
    const batch = await Promise.all(
      pages.slice(index, index + 4).map(async ({ offset, length }) => {
        const url = new URL("https://datasets-server.huggingface.co/rows");
        url.search = new URLSearchParams({
          dataset: lock.mirror.dataset,
          revision: lock.mirror.revision,
          config,
          split,
          offset: String(offset),
          length: String(length),
        });
        const response = await run("curl", [
          "--fail",
          "--location",
          "--silent",
          "--show-error",
          "--retry",
          "3",
          "--retry-delay",
          "1",
          String(url),
        ]);
        const page = JSON.parse(response.stdout);
        assert.equal(page.num_rows_total, count);
        assert.equal(page.rows.length, length);
        assert.equal(page.partial, false);
        return page.rows.map((entry, position) => {
          assert.equal(entry.row_idx, offset + position);
          assert.deepEqual(entry.truncated_cells, []);
          return entry.row;
        });
      }),
    );
    rows.push(...batch.flat());
  }
  return rows;
}

export async function prepareBeir(pilot, directory) {
  const { lock } = pilot;
  const documents = await mirrorRows(
    lock,
    "corpus",
    "corpus",
    lock.corpus_documents,
  );
  const corpusIdentity = sha256(
    [...documents]
      .sort((left, right) =>
        left._id < right._id ? -1 : left._id > right._id ? 1 : 0,
      )
      .map(
        (document) =>
          `${document._id}\t${sha256(`${document.title}\0${document.text}`)}\n`,
      )
      .join(""),
  );
  assert.equal(
    corpusIdentity,
    lock.mirror.corpus_sha256,
    "mirror corpus differs from the pinned BEIR archive",
  );
  const queries = new Map(
    (await mirrorRows(lock, "queries", "queries", 1109)).map((query) => [
      query._id,
      query.text,
    ]),
  );
  const qrelPath = join(data, "scifact-test.tsv");
  assert.equal(await fileHash(qrelPath), lock.mirror.qrels_test_sha256);
  const judgments = qrels(await readFile(qrelPath, "utf8"));
  assert.equal(documents.length, lock.corpus_documents);
  const ids = new Set();
  const root = join(directory, "corpus", "scifact");
  await mkdir(join(root, "docs"), { recursive: true });
  for (const document of documents) {
    assert.match(document._id, /^\d+$/);
    assert.ok(
      !ids.has(document._id),
      `duplicate SciFact document ${document._id}`,
    );
    ids.add(document._id);
    assert.equal(typeof document.title, "string");
    assert.equal(typeof document.text, "string");
    await writeFile(
      join(root, "docs", `${document._id}.md`),
      `${document.title}\n\n${document.text}\n`,
    );
  }
  const tasks = lock.tasks.map((task) => {
    assert.equal(
      queries.get(task.id),
      task.query,
      `changed BEIR query ${task.id}`,
    );
    assert.deepEqual(
      judgments.get(task.id),
      task.qrels,
      `changed BEIR qrels ${task.id}`,
    );
    for (const row of task.qrels)
      assert.ok(
        ids.has(row.document_id),
        `missing BEIR document ${row.document_id}`,
      );
    return {
      id: task.id,
      query: task.query,
      targets: task.qrels.map((row) => ({
        path: `docs/${row.document_id}.md`,
      })),
    };
  });
  return [
    {
      id: "scifact",
      root,
      tasks,
      indexGlob: "*.md",
      expectedIndexedFiles: documents.length,
    },
  ];
}

export async function prepareDuRetrieval(pilot, directory) {
  const { lock } = pilot;
  const source = join(directory, "source", "duretrieval");
  for (const [name, file] of Object.entries(lock.source.files)) {
    await download(
      `https://huggingface.co/datasets/${file.dataset}/resolve/${file.revision}/${file.path}`,
      join(source, `${name}.parquet`),
      file.sha256,
    );
  }
  const root = join(directory, "corpus", "duretrieval");
  await run(
    "python3",
    [
      join(dirname(fileURLToPath(import.meta.url)), "prepare-duretrieval.py"),
      "--lock",
      join(data, "duretrieval10.json"),
      "--source",
      source,
      "--root",
      root,
    ],
    { timeout: 900_000 },
  );
  return [
    {
      id: "duretrieval",
      root,
      tasks: lock.tasks.map((task) => ({
        id: task.id,
        query: task.query,
        targets: task.qrels.map((row) => ({
          path: `docs/${row.document_id}.md`,
        })),
      })),
      indexGlob: "*.md",
      expectedIndexedFiles: lock.corpus_documents,
    },
  ];
}

export async function verifyQuarrySource(pilot, directory) {
  const { lock } = pilot;
  const base = `${lock.source_url}/resolve/${lock.source_revision}/data`;
  const queryPath = join(directory, "source", "quarry-queries.jsonl");
  const goldPath = join(directory, "source", "quarry-gold-preimage.jsonl");
  await download(`${base}/queries.jsonl`, queryPath, lock.queries_sha256);
  await download(
    `${base}/gold-preimage.jsonl`,
    goldPath,
    lock.gold_preimage_sha256,
  );
  const queries = new Map(
    jsonl(await readFile(queryPath, "utf8")).map((row) => [row.query_id, row]),
  );
  const gold = new Map(
    jsonl(await readFile(goldPath, "utf8")).map((row) => [row.query_id, row]),
  );
  for (const task of lock.tasks) {
    const query = queries.get(task.id);
    const annotation = gold.get(task.id);
    assert.equal(query?.query, task.query, `changed Quarry query ${task.id}`);
    assert.equal(query?.task_id, task.source_task_id);
    assert.equal(query?.revision, task.revision);
    assert.equal(annotation?.image_stage, "preimage");
    assert.deepEqual(
      annotation?.positive_units,
      task.positive_units,
      `changed Quarry gold ${task.id}`,
    );
  }
}

export async function prepareQuarryTask(pilot, task, directory) {
  const index = pilot.lock.tasks.findIndex((row) => row.id === task.id);
  assert.ok(index >= 0);
  const repo = {
    repository: `quic-go/task-${index + 1}`,
    url: `https://github.com/${pilot.lock.repository}.git`,
    commit: task.revision,
  };
  const root = await prepareCorpus(repo, join(directory, "corpus"));
  const targets = [];
  const seen = new Set();
  for (const unit of task.positive_units) {
    assert.equal(unit.revision, task.revision);
    assert.equal(unit.image_stage, "preimage");
    assert.ok(typeof unit.path === "string" && !unit.path.startsWith("/"));
    const path = resolve(root, unit.path);
    assert.ok(
      inside(root, path) && inside(root, await realpath(path)),
      `unsafe gold path ${unit.path}`,
    );
    const lines = (await readFile(path, "utf8")).split(/\r?\n/);
    assert.ok(unit.start_line >= 1 && unit.end_line >= unit.start_line);
    assert.ok(
      unit.end_line <= lines.length,
      `stale Quarry line span ${unit.path}`,
    );
    if (!seen.has(unit.path)) {
      targets.push({ path: unit.path });
      seen.add(unit.path);
    }
  }
  return {
    id: `task-${index + 1}`,
    root,
    tasks: [{ id: task.id, query: task.query, targets }],
    indexGlob: "*.go",
  };
}
