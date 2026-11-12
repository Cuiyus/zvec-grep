import assert from "node:assert/strict";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { join, resolve } from "node:path";
import { loadSuite, prepareCorpus, run } from "./core/lib.mjs";
import { indexSelectionArguments, packageCandidate } from "./engines/zg/run.mjs";

const [tarball, directory] = process.argv.slice(2);
const output = resolve(directory);
const evidence = join(output, "evidence");
await mkdir(evidence, { recursive: true });
const candidate = await packageCandidate(tarball, output);
const suite = await loadSuite();
const model = "local/potion-code-16m-v2";
assert.equal(suite.protocol.model, model);
const home = join(output, "runtime");
const modelCache = join(output, "model-cache");
await mkdir(home, { recursive: true });
const env = {
  ...process.env,
  HOME: home,
  ZVEC_GREP_HOME: join(home, ".zvec-grep"),
  ZVEC_GREP_DEVICE: "cpu",
  NO_COLOR: "1",
};
const targetRepos = new Set(["conan-io/conan", "pylint-dev/pylint"]);
const budgets = [1024, 32768];
const report = [];
for (const budget of budgets) {
  for (const repo of suite.lock.repositories.filter(item => targetRepos.has(item.repository))) {
    const root = await prepareCorpus(repo, join(output, String(budget)));
    const slug = repo.repository.replaceAll("/", "__");
    const command = [
      "--index", root, "--mode", "direct", "--embedding", model, "--device", "cpu",
      "--model-cache", modelCache, ...indexSelectionArguments(suite.protocol), "--debug",
    ];
    const index = await run(candidate.cli, command, {
      cwd: root, env: { ...env, ZG_FTS_PROBE_BUDGET: String(budget) }, timeout: 600_000,
    });
    await writeFile(join(evidence, `${slug}-${budget}-index.json`), JSON.stringify(index));
    const status = await run(candidate.cli, ["--status", root, "--mode", "direct", "--check-ready", "--debug"], { cwd: root, env });
    await writeFile(join(evidence, `${slug}-${budget}-status.json`), JSON.stringify(status));
    const tasks = suite.lock.tasks.filter(task => task.repository === repo.repository);
    for (const task of tasks) {
      for (const testPenalty of [true, false]) {
        const queryEnv = { ...env, ZG_FTS_PROBE_RANKS: "1" };
        if (!testPenalty) queryEnv.ZG_FTS_PROBE_NO_TEST_PENALTY = "1";
        const search = await run(candidate.cli, [
          "--fts", task.query, "--limit", "10", "--trace", "--preview", "none",
          "--refresh", "off", "--mode", "direct",
        ], { cwd: root, env: queryEnv });
        const candidates = search.stderr.split("\n")
          .filter(line => line.startsWith("ZG_FTS_PROBE:"))
          .map(line => JSON.parse(line.slice("ZG_FTS_PROBE:".length)));
        assert.ok(candidates.length >= 10, `Missing candidate evidence for ${task.task_id}`);
        const actual = search.stdout.split("\n").filter(line => /^#\d+ /.test(line));
        assert.equal(actual.length, 10, `Unexpected output ranks for ${task.task_id}`);
        report.push({ budget, model, task_id: task.task_id, query: task.query, repo: repo.repository,
          repo_commit: repo.commit, test_penalty: testPenalty, candidates, actual });
        await writeFile(join(evidence, "ranks.json"), JSON.stringify(report, null, 2));
        console.log(budget, task.task_id, testPenalty, candidates.length);
      }
    }
  }
}
