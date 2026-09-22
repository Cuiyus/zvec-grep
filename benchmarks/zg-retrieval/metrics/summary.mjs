import assert from "node:assert/strict";
const average = (values) =>
  values.length
    ? values.reduce((sum, value) => sum + value, 0) / values.length
    : null;

export function summarizeNdcg(rows) {
  // Canonical accumulation order keeps exact cached aggregates independent of
  // filesystem shard order and caller task order without changing the metric.
  rows = rows
    .filter(
      (row) =>
        row.ndcg != null &&
        (row.gold_status == null || row.gold_status === "reviewed"),
    )
    .sort((left, right) => {
      const a = String(left.task_id ?? ""),
        b = String(right.task_id ?? "");
      return a < b ? -1 : a > b ? 1 : 0;
    });
  const metrics = ["ndcg_at_10"];
  const mean = (values) =>
    Object.fromEntries(
      metrics.map((key) => [key, average(values.map((value) => value[key]))]),
    );
  const byRepository = Object.fromEntries(
    [...new Set(rows.map((row) => row.repository))].sort().map((repository) => {
      const selected = rows.filter((row) => row.repository === repository);
      const languages = [...new Set(selected.map((row) => row.language))];
      assert.equal(
        languages.length,
        1,
        "one repository must have one benchmark language",
      );
      return [
        repository,
        {
          language: languages[0],
          query_count: selected.length,
          ...mean(selected.map((row) => row.ndcg)),
        },
      ];
    }),
  );
  const byLanguage = Object.fromEntries(
    [...new Set(rows.map((row) => row.language))].sort().map((language) => {
      const selected = Object.values(byRepository).filter(
        (repo) => repo.language === language,
      );
      return [
        language,
        { repository_count: selected.length, ...mean(selected) },
      ];
    }),
  );
  return {
    dataset: "SWE-QA accepted-file projection",
    metric:
      "First-target-rank binary nDCG; all projected targets; native ranks without result deduplication",
    quality_repetition: 5,
    query_count: rows.length,
    repository_count: Object.keys(byRepository).length,
    language_count: Object.keys(byLanguage).length,
    query_mean: mean(rows.map((row) => row.ndcg)),
    repository_macro: mean(Object.values(byRepository)),
    language_macro: mean(Object.values(byLanguage)),
    by_repository: byRepository,
    by_language: byLanguage,
  };
}
