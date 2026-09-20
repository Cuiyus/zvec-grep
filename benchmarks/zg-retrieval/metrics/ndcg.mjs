import assert from "node:assert/strict";

// Adapted from the reference implementation, Copyright (c) 2026 Thomas van Dongen, MIT licensed.
// Source attribution, license and byte-identical oracle are in
// test/fixtures/ndcg-reference/.

export const NDCG_SOURCE = Object.freeze({
  commit: "0051e000fcaac69a9c5d081ebbc8d4cb8508160b",
  files: ["benchmarks/data.py", "benchmarks/metrics.py"],
});

/** Exact port of upstream data.path_matches; no case-folding or directory stripping. */
export function pathMatches(filePath, targetPath) {
  const file = filePath.replaceAll("\\", "/");
  const target = targetPath.replaceAll("\\", "/");
  return (
    file === target ||
    file.endsWith(`/${target}`) ||
    target.endsWith(`/${file}`)
  );
}

const hasSpan = (target) =>
  target.start_line != null && target.end_line != null;

/** Upstream requires any inclusive overlap when BOTH bounds are supplied. */
export function targetMatchesLocation(filePath, startLine, endLine, target) {
  if (!pathMatches(filePath, target.path)) return false;
  if (!hasSpan(target)) return true;
  return !(endLine < target.start_line || startLine > target.end_line);
}

function validateItems(items) {
  assert.ok(Array.isArray(items), "nDCG requires native ranked items");
  for (const [index, item] of items.entries()) {
    assert.equal(
      item.rank,
      index + 1,
      "nDCG requires consecutive native ranks without compaction",
    );
    assert.equal(typeof item.path, "string", "result path must be a string");
    if (item.range?.kind === "text") {
      assert.ok(
        Number.isSafeInteger(item.range.start_line) &&
          Number.isSafeInteger(item.range.end_line) &&
          item.range.start_line >= 1 &&
          item.range.end_line >= item.range.start_line,
        "text result must carry a valid native source range",
      );
    }
  }
}

function validateTarget(target) {
  assert.equal(typeof target?.path, "string", "target path must be a string");
  for (const field of ["start_line", "end_line"])
    if (target[field] != null)
      assert.ok(
        Number.isSafeInteger(target[field]),
        `${field} must be an integer`,
      );
}

function firstTargetRank(items, target) {
  for (const item of items) {
    // Public non-text results can satisfy a file-only target. They cannot
    // establish overlap with an explicitly annotated source line interval.
    if (hasSpan(target) && item.range?.kind !== "text") continue;
    if (
      targetMatchesLocation(
        item.path,
        item.range?.start_line,
        item.range?.end_line,
        target,
      )
    )
      return item.rank;
  }
  return null;
}

/** Upstream target_rank takes the FIRST matching result, not every same-file chunk. */
export function targetRank(items, target) {
  validateItems(items);
  validateTarget(target);
  return firstTargetRank(items, target);
}

/** Exact binary-rank port of upstream metrics.ndcg_at_k, without group reassignment. */
export function ndcgAtK(relevantRanks, nRelevant, k) {
  if (nRelevant === 0) return 0;
  const relevances = Array(Math.max(0, k)).fill(0);
  for (const rank of relevantRanks)
    if (rank >= 1 && rank <= k) relevances[rank - 1] = 1;
  let ideal = 0;
  for (let i = 0; i < Math.min(k, nRelevant); i++)
    ideal += 1 / Math.log2(i + 2);
  const dcg = relevances.reduce(
    (sum, relevance, index) => sum + relevance / Math.log2(index + 2),
    0,
  );
  return ideal > 0 ? dcg / ideal : 0;
}

/**
 * Score the caller's combined primary + secondary targets with upstream rules.
 * Preserve every supplied target, including duplicates, in the IDCG denominator.
 * Input item order/ranks are native: repeated files are never compacted away.
 * Source text, outline, snippets and retrieval scores are deliberately not read.
 */
export function scoreNdcg(items, targets) {
  validateItems(items);
  assert.ok(Array.isArray(targets), "nDCG requires a target array");
  targets.forEach(validateTarget);
  const targetRanks = targets.map((target) => firstTargetRank(items, target));
  const relevantRanks = targetRanks.filter((rank) => rank !== null);
  return {
    ndcg_at_10: ndcgAtK(relevantRanks, targets.length, 10),
    target_ranks: targetRanks,
    n_relevant: targets.length,
    relevant_ranks: relevantRanks,
  };
}

/**
 * SWE-QA-specific file-only label projection from reviewed accepted paths.
 * One target per exact accepted path, in first occurrence order. Bridge labels
 * do not participate. Source anchors are not converted into optional spans:
 * an anchor identifies visible evidence, not the whole relevant source region.
 */
export function projectFileTargets(gold) {
  assert.ok(Array.isArray(gold?.targets), "Gold targets must be an array");
  const paths = new Set();
  for (const target of gold.targets) {
    if (target.role !== "accepted") continue;
    assert.equal(
      typeof target.path,
      "string",
      "accepted path must be a string",
    );
    paths.add(target.path);
  }
  return [...paths].map((path) => ({ path }));
}
