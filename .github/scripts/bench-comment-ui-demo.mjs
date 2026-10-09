// Test-branch-only fixture producer. This file is not part of the feature PR.
import { mkdir, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

const banner =
  "> **UI 联调演示：以下数值是固定测试数据，不是 Benchmark 实测。真实评测见本 PR 的另一条 running 评论。**";
export function withDemoBanner(github) {
  for (const method of ["createComment", "updateComment"]) {
    const original = github.rest.issues[method].bind(github.rest.issues);
    github.rest.issues[method] = (input) =>
      original({
        ...input,
        body: input.body
          .replace(/^(<!--[^\n]+-->)/, `$1\n${banner}\n`)
          .replace("## ZG Benchmark ·", "## ZG Benchmark · UI 演示 ·"),
      });
  }
}

async function main() {
  const directory = join(process.env.RUNNER_TEMP, "ui-fixture");
  await mkdir(directory, { recursive: true });
  const candidate = process.env.BENCH_CANDIDATE_SHA;
  const note =
    "**固定测试数据，仅验证回贴版式；未执行检索、模型推理或 Judge。**\n\n";
  const retrieval = process.env.BENCH_FIXTURE_KIND === "retrieval";
  const report = retrieval
    ? {
        schema_version: 1,
        candidate: { commit: candidate },
        status: "success",
        fixture: true,
      }
    : {
        schema_version: 2,
        gate: { passed: true, missing_tasks: [] },
        cases: Array.from({ length: 20 }, (_, i) => ({
          task_id: `fixture-${i}`,
        })),
        fixture: true,
      };
  const markdown = retrieval
    ? note +
      "| Suite | Mode | File Hit@1 | File Hit@5 | File Hit@10 | MRR@10 | nDCG@10 |\n| --- | --- | ---: | ---: | ---: | ---: | ---: |\n| SWE-QA20 | hybrid | 0.60 | 0.80 | 0.85 | 0.70 | 0.75 |\n| BEIR | hybrid | 0.55 | 0.75 | 0.80 | 0.65 | 0.70 |\n| DuRetrieval | hybrid | 0.50 | 0.70 | 0.80 | 0.60 | 0.65 |\n| Quarry | hybrid | 0.65 | 0.85 | 0.90 | 0.75 | 0.80 |\n"
    : note +
      "All cells use `baseline / zvec-grep / change`.\n\n| Case | Judge | input_token | toolcall | time (s) |\n| --- | ---: | ---: | ---: | ---: |\n| Aggregate | 7.50 / 8.00 / +0.50 | 100,000 / 80,000 / -20.00% | 100 / 80 / -20.00% | 600 / 480 / -20.00% |\n";
  await writeFile(
    join(directory, retrieval ? "summary.json" : "report.json"),
    JSON.stringify(report),
  );
  await writeFile(
    join(directory, retrieval ? "summary.md" : "report.md"),
    markdown,
  );
  if (!retrieval)
    await writeFile(
      join(directory, "candidate-manifest.json"),
      JSON.stringify({ candidate_commit: candidate, fixture: true }),
    );
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href)
  await main();
