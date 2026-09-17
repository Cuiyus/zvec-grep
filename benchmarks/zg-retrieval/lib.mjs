import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { spawn } from "node:child_process";
import { createReadStream } from "node:fs";
import {
  mkdir,
  readFile,
  writeFile,
  readdir,
  lstat,
  readlink,
  realpath,
} from "node:fs/promises";
import { dirname, join, resolve, relative, isAbsolute } from "node:path";
import { fileURLToPath } from "node:url";

export const suiteDirectory = dirname(fileURLToPath(import.meta.url));
export const sha256 = (value) =>
  createHash("sha256").update(value).digest("hex");
export const readJson = async (path) =>
  JSON.parse(await readFile(path, "utf8"));
export async function writeJson(path, value) {
  await mkdir(dirname(path), { recursive: true });
  await writeFile(path, `${JSON.stringify(value, null, 2)}\n`);
}
export function canonical(value) {
  if (Array.isArray(value)) return value.map(canonical);
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.keys(value)
        .sort()
        .map((key) => [key, canonical(value[key])]),
    );
  }
  return value;
}
export const objectHash = (value) => sha256(JSON.stringify(canonical(value)));
export async function fileHash(path) {
  const hash = createHash("sha256");
  for await (const part of createReadStream(path)) hash.update(part);
  return hash.digest("hex");
}
export function inside(root, path) {
  const rel = relative(resolve(root), resolve(path));
  return (
    rel === "" ||
    (!rel.startsWith(`..${process.platform === "win32" ? "\\" : "/"}`) &&
      rel !== ".." &&
      !isAbsolute(rel))
  );
}
export function run(
  command,
  args,
  { cwd, env = process.env, timeout = 600_000, log } = {},
) {
  return new Promise((resolvePromise, reject) => {
    const child = spawn(command, args, {
      cwd,
      env,
      stdio: ["ignore", "pipe", "pipe"],
    });
    let stdout = "",
      stderr = "",
      timedOut = false;
    child.stdout.on("data", (part) => {
      stdout += part;
      log?.(String(part));
    });
    child.stderr.on("data", (part) => {
      stderr += part;
      log?.(String(part));
    });
    const timer = setTimeout(() => {
      timedOut = true;
      child.kill("SIGKILL");
    }, timeout);
    child.on("error", (error) => {
      clearTimeout(timer);
      reject(error);
    });
    child.on("close", (code, signal) => {
      clearTimeout(timer);
      const result = { stdout, stderr, code, signal, timed_out: timedOut };
      if (code === 0) resolvePromise(result);
      else
        reject(
          Object.assign(
            new Error(
              `${command} ${args[0] ?? ""} failed (${timedOut ? "timeout" : code}): ${stderr.slice(-3000)}`,
            ),
            { result },
          ),
        );
    });
  });
}
export const repositorySlug = (repository) => repository.replaceAll("/", "__");

export async function loadSuite() {
  const lock = await readJson(join(suiteDirectory, "data/source.lock.json"));
  const protocol = await readJson(
    join(suiteDirectory, "configs/protocol.json"),
  );
  assert.equal(
    lock.tasks.length,
    20,
    "the frozen suite must contain 20 original questions",
  );
  assert.equal(new Set(lock.tasks.map((task) => task.task_id)).size, 20);
  assert.equal(lock.repositories.length, 11);
  const queries = (
    await readFile(join(suiteDirectory, "data/queries.jsonl"), "utf8")
  )
    .trimEnd()
    .split("\n")
    .map(JSON.parse);
  const gold = {};
  for (const [index, task] of lock.tasks.entries()) {
    assert.equal(
      sha256(task.query),
      task.query_sha256,
      `${task.task_id}: query hash mismatch`,
    );
    assert.equal(queries[index].task_id, task.task_id);
    assert.equal(
      queries[index].query,
      task.query,
      `${task.task_id}: rewritten query`,
    );
    assert.equal(
      task.repository_commit,
      lock.repositories.find((repo) => repo.repository === task.repository)
        ?.commit,
    );
    const entry = await readJson(
      join(suiteDirectory, "gold/v1", `${task.task_slug}.json`),
    );
    validateGold(task, entry);
    gold[task.task_id] = entry;
  }
  assert.equal(queries.length, lock.tasks.length);
  return {
    lock,
    protocol,
    gold,
    identity: {
      source: objectHash(lock),
      protocol: objectHash(protocol),
      gold: objectHash(gold),
    },
  };
}

export function validateGold(task, gold) {
  assert.equal(gold.schema_version, 1);
  assert.equal(gold.task_id, task.task_id);
  assert.equal(gold.question_sha256, task.query_sha256);
  assert.equal(gold.repository, task.repository);
  assert.equal(gold.repository_commit, task.repository_commit);
  assert.match(gold.gold_version, /^sweqa20-entry-v/);
  assert.ok(
    ["reviewed", "unknown", "disputed"].includes(gold.status),
    `${task.task_id}: unreviewed gold`,
  );
  if (gold.status === "reviewed") {
    assert.ok(
      gold.review?.proposer &&
        gold.review?.reviewer &&
        gold.review.proposer !== gold.review.reviewer,
      `${task.task_id}: independent review missing`,
    );
    assert.ok(gold.targets.some((target) => target.role === "accepted"));
  }
  const ids = new Set();
  for (const target of gold.targets) {
    assert.ok(target.id && !ids.has(target.id));
    ids.add(target.id);
    assert.ok(
      !isAbsolute(target.path) && !target.path.split(/[\\/]/).includes(".."),
    );
    assert.ok(["symbol", "code_span"].includes(target.kind));
    assert.ok(["accepted", "bridge"].includes(target.role));
    assert.match(target.source_sha256, /^[a-f0-9]{64}$/);
    assert.ok(target.relevance_reason && target.anchors.length > 0);
    for (const anchor of target.anchors) {
      assert.ok(Number.isInteger(anchor.start_line) && anchor.start_line > 0);
      assert.ok(
        Number.isInteger(anchor.end_line) &&
          anchor.end_line >= anchor.start_line,
      );
      assert.equal(
        anchor.text.split("\n").length,
        anchor.end_line - anchor.start_line + 1,
      );
      assert.equal(sha256(anchor.text), anchor.sha256);
    }
  }
  assert.equal(typeof gold.ndcg?.enabled, "boolean");
  if (gold.ndcg.enabled) {
    assert.ok(
      gold.ndcg.groups.length >= 2,
      "nDCG subset requires multiple complementary evidence groups",
    );
    assert.equal(
      new Set(gold.ndcg.groups.map((group) => group.id)).size,
      gold.ndcg.groups.length,
    );
    const seen = new Set();
    for (const group of gold.ndcg.groups) {
      assert.ok(group.target_ids.length > 0);
      for (const id of group.target_ids) {
        assert.ok(
          gold.targets.some(
            (target) => target.id === id && target.role === "accepted",
          ),
        );
        assert.ok(
          !seen.has(id),
          "an entry cannot represent two complementary groups",
        );
        seen.add(id);
      }
    }
  }
}

export async function validateGoldSources(root, tasks, gold) {
  for (const task of tasks) {
    for (const target of gold[task.task_id].targets) {
      const path = join(root, target.path);
      assert.ok(
        inside(await realpath(root), await realpath(path)),
        "gold source escapes corpus",
      );
      assert.equal(
        await fileHash(path),
        target.source_sha256,
        `${task.task_id}: stale gold file ${target.path}`,
      );
      const lines = (await readFile(path, "utf8")).split(/\r?\n/);
      for (const anchor of target.anchors) {
        assert.equal(
          lines.slice(anchor.start_line - 1, anchor.end_line).join("\n"),
          anchor.text,
          `${task.task_id}: stale source anchor ${target.id}`,
        );
      }
    }
  }
}

export async function prepareCorpus(repo, corpusDirectory) {
  const root = join(corpusDirectory, repositorySlug(repo.repository));
  await mkdir(root, { recursive: true });
  try {
    await lstat(join(root, ".git"));
  } catch {
    await run("git", ["init", "--quiet", root]);
    await run("git", ["-C", root, "remote", "add", "origin", repo.url]);
    await run("git", [
      "-C",
      root,
      "-c",
      "core.hooksPath=/dev/null",
      "fetch",
      "--depth=1",
      "origin",
      repo.commit,
    ]);
    await run("git", [
      "-C",
      root,
      "-c",
      "core.hooksPath=/dev/null",
      "checkout",
      "--detach",
      "FETCH_HEAD",
    ]);
  }
  assert.equal(
    (await run("git", ["-C", root, "rev-parse", "HEAD"])).stdout.trim(),
    repo.commit,
    `corpus commit mismatch: ${root}`,
  );
  const dirty = await run("git", [
    "-C",
    root,
    "status",
    "--porcelain",
    "--untracked-files=all",
    "--",
    ".",
    ":(exclude).zvec-grep",
  ]);
  assert.equal(dirty.stdout, "", `corpus must be pristine: ${root}`);
  return await realpath(root);
}

export async function corpusManifest(root) {
  const tracked = (await run("git", ["-C", root, "ls-files", "-z"])).stdout
    .split("\0")
    .filter(Boolean)
    .sort();
  const entries = [];
  for (const path of tracked) {
    const full = join(root, path),
      info = await lstat(full);
    if (info.isSymbolicLink())
      entries.push({ path, kind: "symlink", target: await readlink(full) });
    else if (info.isFile())
      entries.push({
        path,
        kind: "file",
        size: info.size,
        sha256: await fileHash(full),
      });
    else entries.push({ path, kind: "unmaterialized_submodule" });
  }
  return { sha256: objectHash(entries), entries };
}

export async function directoryManifest(root) {
  const entries = [];
  async function visit(directory) {
    for (const entry of (
      await readdir(directory, { withFileTypes: true })
    ).sort((a, b) => a.name.localeCompare(b.name))) {
      const path = join(directory, entry.name);
      if (entry.isDirectory()) await visit(path);
      else if (entry.isFile())
        entries.push({
          path: relative(root, path).split("\\").join("/"),
          sha256: await fileHash(path),
        });
      else throw new Error(`unsupported model artifact: ${path}`);
    }
  }
  await visit(root);
  return { sha256: objectHash(entries), entries };
}
