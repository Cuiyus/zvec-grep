import assert from "node:assert/strict";
import { targetRank } from "./ndcg.mjs";

export const FILE_RETRIEVAL_CONTRACT = "sweqa-file-hit-rr-v1";

/** Standard first-relevant-result metrics on the frozen accepted-file targets. */
export function scoreFileRetrieval(items, targets) {
  assert.ok(
    Array.isArray(targets) && targets.length > 0,
    "file retrieval requires nonempty targets",
  );
  const paths = new Set();
  for (const target of targets) {
    assert.equal(typeof target?.path, "string", "file target requires a path");
    assert.ok(target.path.length > 0, "file target path must not be empty");
    assert.ok(
      target.start_line == null && target.end_line == null,
      "file retrieval requires file-only targets",
    );
    const path = target.path.replaceAll("\\", "/");
    assert.ok(!paths.has(path), "file retrieval requires unique file targets");
    paths.add(path);
  }
  // targetRank validates native consecutive ranks. Repeated file chunks consume
  // ranks, exactly as in nDCG; no deduplication or compaction.
  const targetRanks = targets.map((target) => targetRank(items, target));
  const ranks = targetRanks.filter((rank) => rank !== null && rank <= 10);
  const rank = ranks.length ? Math.min(...ranks) : null;
  return {
    first_hit_rank: rank ?? "not_in_top10",
    hit_at_1: Number(rank !== null && rank <= 1),
    hit_at_5: Number(rank !== null && rank <= 5),
    hit_at_10: Number(rank !== null),
    rr_at_10: rank === null ? 0 : 1 / rank,
    target_ranks: targetRanks,
    n_relevant: targets.length,
  };
}

/** Recompute only from public items and frozen targets, never cached scores. */
export function fileRetrievalForRow(row) {
  if (
    row.status === "harness_invalid" ||
    row.execution_status === "harness_invalid" ||
    row.status?.startsWith("gold_") ||
    (row.gold_status != null && row.gold_status !== "reviewed")
  )
    return null;
  assert.ok(
    ["success", "product_error"].includes(row.execution_status),
    "file retrieval requires a valid execution status",
  );
  assert.ok(
    Array.isArray(row.items),
    "file retrieval requires parsed public items",
  );
  if (row.execution_status === "product_error")
    assert.equal(
      row.items.length,
      0,
      "product errors cannot provide file retrieval credit",
    );
  return scoreFileRetrieval(row.items, row.ndcg?.targets);
}

/** Each original question has equal weight; repeats and repositories add no votes. */
export function summarizeFileRetrieval(rows) {
  // Stable task order avoids one-ULP differences between independently
  // assembled shard reports and the same rows in frozen-suite order.
  const scored = [...rows]
    .sort((left, right) => {
      const a = String(left.task_id ?? ""),
        b = String(right.task_id ?? "");
      return a < b ? -1 : a > b ? 1 : 0;
    })
    .map(fileRetrievalForRow)
    .filter((score) => score !== null);
  const mean = (values) =>
    values.length
      ? values.reduce((sum, value) => sum + value, 0) / values.length
      : null;
  return {
    planned_tasks: rows.length,
    scored_tasks: scored.length,
    ...Object.fromEntries(
      [1, 5, 10].flatMap((cutoff) => {
        const key = `hit_at_${cutoff}`;
        return [
          [`${key}_count`, scored.reduce((sum, score) => sum + score[key], 0)],
          [key, mean(scored.map((score) => score[key]))],
        ];
      }),
    ),
    mrr_at_10: mean(scored.map((score) => score.rr_at_10)),
  };
}
