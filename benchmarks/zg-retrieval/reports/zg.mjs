import { ZG_MODES } from "./validation.mjs";

const display = (value) => (Number.isFinite(value) ? value.toFixed(4) : "N/A");

export function markdownReport(report) {
  const modeEntries = ZG_MODES.map((mode) => [
    `zg-${mode}`,
    report.modes[mode],
  ]);
  const queryCount =
    report.expected_task_ids?.length ??
    new Set(report.tasks.map((row) => row.task_id)).size;
  const repoCount = new Set(report.repositories.map((repo) => repo.repository))
    .size;
  const lines = [
    "# zg Retrieval-only",
    "",
    `Status: **${report.integrity_passed ? "PASS" : "FAIL"}**. Dataset: **${queryCount} original questions / ${repoCount} repositories**. Quality: **fifth call per question and mode; Rust MCP default presentation**. Five repetitions are stability observations, not additional questions.`,
    "",
    "| Arm | File Hit@1 | File Hit@5 | File Hit@10 | File MRR@10 | nDCG@10 | Output mean (KiB) | Latency P50 (ms) |",
    "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ...modeEntries.map(([label, entry]) => {
      const file = entry?.file_retrieval,
        ndcg = entry?.ndcg?.repository_macro;
      return file && ndcg
        ? `| ${label} | ${file.hit_at_1_count}/${file.scored_tasks} | ${file.hit_at_5_count}/${file.scored_tasks} | ${file.hit_at_10_count}/${file.scored_tasks} | ${display(file.mrr_at_10)} | ${display(ndcg.ndcg_at_10)} | ${display(entry?.measurements?.output_bytes_mean == null ? null : entry.measurements.output_bytes_mean / 1024)} | ${display(entry?.measurements?.latency_ms_p50)} |`
        : `| ${label} | N/A — invalid experiment | N/A | N/A | N/A | N/A | N/A | N/A |`;
    }),
    "",
    "File Hit@1/5/10 and MRR@10 give every original question equal weight, including misses and product-error zeros. nDCG@10 first averages questions within each repository, then weights repositories equally. All five metrics use the same frozen accepted-file targets and native result ranks; repeated chunks consume ranks without file deduplication. Finding a file does not establish sufficient answer evidence.",
    "",
    "Hybrid, fts and vector use the Rust public MCP default presentation on the same frozen repository index with the original query and Top-10 limit. The benchmark does not send a preview override because the current Rust MCP schema does not expose one. Presentation text and output length do not affect the five file-ranking metrics. Quality thresholds are report-only.",
  ];
  lines.push(
    "",
    "<details>",
    "<summary>Per-question results and captured responses</summary>",
    "",
    "| Question / arm | File Hit@1 | File Hit@5 | File Hit@10 | File RR@10 | nDCG@10 | Status | Response |",
    "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ...report.tasks.map(
      (row) =>
        `| ${row.task_id} / zg-${row.mode} | ${row.file_retrieval?.hit_at_1 ?? "N/A"} | ${row.file_retrieval?.hit_at_5 ?? "N/A"} | ${row.file_retrieval?.hit_at_10 ?? "N/A"} | ${display(row.file_retrieval?.rr_at_10)} | ${display(row.ndcg?.ndcg_at_10)} | ${row.status} | [response](${row.raw_path}) |`,
    ),
    "",
    "</details>",
    "",
    ...modeEntries.map(
      ([label, entry]) =>
        `${label}: output uses **${entry?.measurements?.output_sample_count ?? 0}** successful fifth-call responses; latency P50 uses **${entry?.measurements?.latency_sample_count ?? 0}** successful valid calls. Output is public UTF-8 bytes / 1024, not model tokens. Latency includes engine/session load and fixed-order cache effects; no cross-environment speed claim is made.`,
    ),
    "",
    "Raw run metadata, public result items, target ranks, preparation evidence, corpus/model/index identities and per-call observations are retained in the JSON artifacts. Incomplete or invalid experiments withhold aggregate quality; product failures retain zero credit and fail operational integrity.",
  );
  if (report.product_error_calls)
    lines.push("", `Product-error calls: **${report.product_error_calls}**.`);
  if (report.integrity_errors.length)
    lines.push(
      "",
      "Invalid experiment:",
      "",
      ...report.integrity_errors.map(
        (reason) => `- ${reason.replaceAll("\n", " ")}`,
      ),
    );
  return lines.join("\n") + "\n";
}
