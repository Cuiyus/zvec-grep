import { SEMBLE_PROTOCOL } from "../engines/semble/protocol.mjs";

const cell = (value) =>
  String(value ?? "N/A")
    .replaceAll("|", "\\|")
    .replace(/[\r\n]+/g, " ");
const number = (value) =>
  typeof value === "number" ? value.toFixed(4) : "N/A";

const QUALITY_HEADER =
  "| Arm | File Hit@1 | File Hit@5 | File Hit@10 | File MRR@10 | nDCG@10 | Output KiB (mean) | Latency P50 ms |";
const QUALITY_SEPARATOR = "| --- | --- | --- | --- | --- | --- | --- | --- |";
const qualityRow = (label, file, official, measurements) =>
  `| ${cell(label)} | ${number(file?.hit_at_1)} | ${number(file?.hit_at_5)} | ${number(file?.hit_at_10)} | ${number(file?.mrr_at_10)} | ${number(official?.repository_macro.ndcg_at_10)} | ${number(measurements?.output_bytes_mean == null ? null : measurements.output_bytes_mean / 1024)} | ${number(measurements?.latency_ms_p50)} |`;

export function markdownSembleReport(report) {
  const comparison = report.cross_tool_comparison;
  const variants = comparison
    ? comparison.zg_previews
      ? [
          ["zg MCP short", comparison.zg_previews.short, "zg"],
          ["zg MCP full", comparison.zg_previews.full, "zg"],
          ["Semble MCP full chunk", comparison, "semble"],
        ]
      : [
          ["zg MCP", comparison, "zg"],
          ["Semble MCP full chunk", comparison, "semble"],
        ]
    : null;
  const lines = [
    "# Semble Retrieval-only — SWE-QA original queries",
    "",
    `Scope: **${cell(report.scope)}**. Calls: **${report.observed_calls}**. Integrity: **${report.integrity_passed ? "PASS" : "FAIL"}**. Quality: **${report.quality_score_valid ? "VALID" : "INVALID — aggregate withheld"}**.`,
    "",
    ...(comparison ? ["Cross-tool quality comparison:", ""] : []),
    QUALITY_HEADER,
    QUALITY_SEPARATOR,
    ...(variants
      ? variants.map(([label, entry, engine]) =>
          qualityRow(
            label,
            entry.file_retrieval[engine],
            entry.semble_official[engine],
            entry.measurements[engine],
          ),
        )
      : [
          qualityRow(
            "Semble MCP full chunk",
            report.modes.hybrid.file_retrieval,
            report.modes.hybrid.semble_official,
            report.modes.hybrid.measurements,
          ),
        ]),
    "",
    "Averaging: quality uses repetition 5. File Hit/MRR average all original questions equally; nDCG@10 first averages questions within each repository, then averages repositories equally. Misses and product errors contribute zero. Invalid experiments have no aggregate. Five repeats do not add independent questions.",
    "",
    "Measurements: output is mean UTF-8 public MCP text size on successful fifth calls (1 KiB = 1024 bytes); latency is P50 across all successful search calls. Errors are excluded from measurement samples; timing includes engine-specific loading and is not a controlled speed comparison.",
    ...(variants
      ? variants.map(
          ([label, entry, engine]) =>
            `${label}: output samples=${entry.measurements[engine].output_sample_count}; latency samples=${entry.measurements[engine].latency_sample_count}.`,
        )
      : [
          `Semble: output samples=${report.modes.hybrid.measurements?.output_sample_count ?? 0}; latency samples=${report.modes.hybrid.measurements?.latency_sample_count ?? 0}.`,
        ]),
    "",
    "Both metrics use the frozen accepted-file targets and Semble path matching, preserving native result ranks without deduplication. Repeated chunks consume ranks; only the first match for each target contributes. File localization does not establish sufficient answer evidence. The labels are the SWE-QA projection, not Semble’s original benchmark annotations.",
    "",
    `Semble ${cell(report.tool?.version)}, commit \`${cell(report.tool?.source_commit)}\`. Native stdio MCP search; content=code; top_k=10; max_snippet_lines=null. Model: ${SEMBLE_PROTOCOL.model}. No query rewrite or subquery.`,
    "",
    "<details>",
    "<summary>Per-question evidence and run integrity</summary>",
    "",
    "| Task | Status | File first rank | File RR@10 | nDCG@10 | Target ranks | Public response |",
    "| --- | --- | --- | --- | --- | --- | --- |",
    ...report.tasks.map(
      (row) =>
        `| ${cell(row.task_id)} | ${cell(row.execution_status)} | ${cell(row.file_retrieval?.first_hit_rank)} | ${number(row.file_retrieval?.rr_at_10)} | ${number(row.semble_official?.ndcg_at_10)} | ${row.semble_official?.target_ranks.map((rank) => rank ?? "not found").join(", ") ?? "N/A"} | [response](${row.raw_path}) |`,
    ),
    "",
    "Source, model and index inventories must match before/after; public snippets are source-audited. Each question’s fifth MCP response is checked against the official SDK. Repository evidence, query/repository/language nDCG means, repeated outputs and call timing observations are retained in report.json and scores.jsonl.",
    "",
    ...report.repositories.map(
      (run) =>
        `- ${cell(run.repository)}: preparation=${cell(run.preparation_status)}; post-run integrity=${cell(run.post_run_integrity)}; fifth-call SDK parity=${cell(run.sdk_parity_verified)}.`,
    ),
    "",
    ...(comparison
      ? [
          "Cross-tool deltas and per-question evidence are in report.json. Endpoints, representations, filtering, model runtime and environment differ; the comparison does not isolate a causal component or compute cross-environment speed ratios.",
          "",
          ...comparison.warnings.map((warning) => `- ${warning}`),
          "",
        ]
      : []),
    "</details>",
  ];
  if (report.comparison_error)
    lines.push(
      "",
      `Cross-tool comparison withheld: ${cell(report.comparison_error)}`,
    );
  if (report.product_error_calls)
    lines.push(
      "",
      `Product errors: **${report.product_error_calls}**. Quality zeros remain in the denominator; operational integrity fails.`,
    );
  if (report.integrity_errors.length)
    lines.push(
      "",
      "<details>",
      "<summary>Invalid experiment</summary>",
      "",
      ...report.integrity_errors.map((error) => `- ${cell(error)}`),
      "",
      "</details>",
    );
  return lines.join("\n") + "\n";
}
