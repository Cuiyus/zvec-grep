import { objectHash } from "../core/lib.mjs";

const display = (value) =>
  typeof value === "number" ? value.toFixed(4) : "N/A";

/** Compare only public retrieval identities; preview-dependent text is deliberately excluded. */
export function summarizePreviewPairs(
  observations,
  {
    primaryPreview = "short",
    comparisonPreview = "full",
    qualityRepetition = 5,
  } = {},
) {
  const identity = (row) =>
    objectHash(
      row.items.map((item) => ({
        rank: item.rank,
        path: item.path,
        range: item.range,
        matched_range: item.matched_range ?? null,
        matched_by: item.matched_by ?? null,
      })),
    );
  const keyed = new Map();
  for (const row of observations) {
    if (![primaryPreview, comparisonPreview].includes(row.preview)) continue;
    const key = `${row.task_id}/${row.mode}/${row.repetition}`;
    if (!keyed.has(key)) keyed.set(key, {});
    keyed.get(key)[row.preview] = row;
  }
  const pairs = [...keyed.values()].map((pair) => {
    const primary = pair[primaryPreview],
      comparison = pair[comparisonPreview];
    const row = primary ?? comparison;
    const valid = [primary, comparison].every(
      (entry) =>
        entry?.execution_status === "success" &&
        entry.status !== "harness_invalid" &&
        entry.semble_official != null &&
        Array.isArray(entry.items),
    );
    return {
      task_id: row.task_id,
      mode: row.mode,
      repetition: row.repetition,
      quality_observation: row.repetition === qualityRepetition,
      status: valid ? "compared" : "unavailable",
      ranking_equal: valid ? identity(primary) === identity(comparison) : null,
      official_ndcg_equal: valid
        ? primary.semble_official.ndcg_at_10 ===
          comparison.semble_official.ndcg_at_10
        : null,
    };
  });
  const counts = (rows) => ({
    observed_pairs: rows.length,
    compared_pairs: rows.filter((row) => row.status === "compared").length,
    same_ranking_pairs: rows.filter((row) => row.ranking_equal === true).length,
    different_ranking_pairs: rows.filter((row) => row.ranking_equal === false)
      .length,
    same_official_ndcg_pairs: rows.filter(
      (row) => row.official_ndcg_equal === true,
    ).length,
    different_official_ndcg_pairs: rows.filter(
      (row) => row.official_ndcg_equal === false,
    ).length,
  });
  return {
    primary_preview: primaryPreview,
    comparison_preview: comparisonPreview,
    identity_scope:
      "ordered rank/path/range/matched range/matched-by; excludes preview-dependent source locations, text and outline; hidden entity IDs unavailable",
    interpretation:
      "report-only presentation control; a ranking difference is an uncontrolled retrieval difference, not proof that preview changed retrieval; artifact validity is audited separately",
    ...counts(pairs),
    quality: counts(pairs.filter((row) => row.quality_observation)),
    pairs,
  };
}

export function markdownReport(report) {
  const previewModes = Object.entries(
    report.previews ?? { [report.preview ?? "short"]: { modes: report.modes } },
  ).flatMap(([preview, entry]) =>
    Object.entries(entry.modes).map(([mode, value]) => [
      `${mode} / ${preview}`,
      value,
    ]),
  );
  const queryCount =
    report.expected_task_ids?.length ??
    new Set(report.tasks.map((row) => row.task_id)).size;
  const repoCount = new Set(report.repositories.map((repo) => repo.repository))
    .size;
  const lines = [
    "# zg Retrieval-only",
    "",
    `Status: **${report.integrity_passed ? "PASS" : "FAIL"}**. Dataset: **${queryCount} original questions / ${repoCount} repositories**. Quality: **fifth call per question and preview**. Five repetitions are stability observations, not additional questions.`,
    "",
    "| Mode / preview | File Hit@1 | File Hit@5 | File Hit@10 | File MRR@10 | nDCG@10 | Output mean (KiB) | Latency P50 (ms) |",
    "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ...previewModes.map(([label, entry]) => {
      const file = entry.file_retrieval,
        official = entry.semble_official?.repository_macro;
      return file && official
        ? `| ${label} | ${file.hit_at_1_count}/${file.scored_tasks} | ${file.hit_at_5_count}/${file.scored_tasks} | ${file.hit_at_10_count}/${file.scored_tasks} | ${display(file.mrr_at_10)} | ${display(official.ndcg_at_10)} | ${display(entry.measurements?.output_bytes_mean == null ? null : entry.measurements.output_bytes_mean / 1024)} | ${display(entry.measurements?.latency_ms_p50)} |`
        : `| ${label} | N/A — invalid experiment | N/A | N/A | N/A | N/A | N/A | N/A |`;
    }),
    "",
    "File Hit@1/5/10 and MRR@10 give every original question equal weight, including misses and product-error zeros. nDCG@10 first averages questions within each repository, then weights repositories equally. All five metrics use the same frozen accepted-file targets and native result ranks; repeated chunks consume ranks without file deduplication. Finding a file does not establish sufficient answer evidence. These are SWE-QA accepted-file projection scores, not scores on Semble's original dataset.",
    "",
    "Short and full use the same frozen index, session, query and Top-10 limit. Full returns all available content of each retrieved unit, not the entire file. Preview text and output length do not affect these five metrics. Quality thresholds are report-only.",
  ];
  if (report.paired_preview_comparison) {
    const pair = report.paired_preview_comparison;
    lines.push(
      "",
      `Paired retrieval identities: **${pair.same_ranking_pairs}/${pair.compared_pairs}** equal; fifth-call pairs: **${pair.quality.same_ranking_pairs}/${pair.quality.compared_pairs}**. Unavailable pairs: **${pair.observed_pairs - pair.compared_pairs}**. The identity check compares ordered rank, path, range, matched range and match type; it excludes displayed text.`,
    );
    if (pair.different_ranking_pairs)
      lines.push(
        "",
        `**${pair.different_ranking_pairs} pairs have different retrieval identities.** Inspect paired_preview_comparison in report.json before attributing differences to presentation.`,
      );
  }
  lines.push(
    "",
    "<details>",
    "<summary>Per-question results and captured responses</summary>",
    "",
    "| Question / mode / preview | File Hit@1 | File Hit@5 | File Hit@10 | File RR@10 | nDCG@10 | Status | Response |",
    "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ...report.tasks.map(
      (row) =>
        `| ${row.task_id} / ${row.mode} / ${row.preview ?? report.preview ?? "short"} | ${row.file_retrieval?.hit_at_1 ?? "N/A"} | ${row.file_retrieval?.hit_at_5 ?? "N/A"} | ${row.file_retrieval?.hit_at_10 ?? "N/A"} | ${display(row.file_retrieval?.rr_at_10)} | ${display(row.semble_official?.ndcg_at_10)} | ${row.status} | [response](${row.raw_path}) |`,
    ),
    "",
    "</details>",
    "",
    ...previewModes.map(
      ([label, entry]) =>
        `${label}: output uses **${entry.measurements?.output_sample_count ?? 0}** successful fifth-call responses; latency P50 uses **${entry.measurements?.latency_sample_count ?? 0}** successful valid calls. Output is public UTF-8 bytes / 1024, not model tokens. Latency includes engine/session load and fixed-order cache effects; no cross-environment speed claim is made.`,
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
