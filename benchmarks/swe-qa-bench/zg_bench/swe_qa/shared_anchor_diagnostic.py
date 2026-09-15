"""Supplementary comparisons using anchors already accepted for every rewrite.

Partial positive label sets can differ even when requests express the same goal.
Their first-hit ranks must not be compared as if their denominators were equal.
This diagnostic never edits labels, chooses an anchor from observed ranks, or
replaces the primary per-query scores.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any


def _target(scores: dict | None) -> dict:
    return ((scores or {}).get("native", {}).get("query_relevance") or {}).get("target") or {}


def _rank(target: dict, common: set[str]) -> dict:
    known = bool(common) and target.get("status") == "scored" and isinstance(target.get("matches"), list)
    matches = [m for m in target.get("matches", []) if m.get("target_id") in common] if known else []
    if any(type(m.get("rank")) is not int or m["rank"] < 1 for m in matches):
        known = False
    first = min((m["rank"] for m in matches), default=None) if known else None
    return {"status": "scored" if known else "unknown", "first_hit_rank": first,
        **{f"hit_at_{k}": bool(first is not None and first <= k) if known else None for k in (1, 5, 10)},
        "rr_at_10": (1 / first if first is not None and first <= 10 else 0) if known else None,
        "matches": matches if known else []}


def build_diagnostic(labels: dict[str, Any], groups: list[dict], replay: dict) -> dict:
    """Intersect frozen accepted sets within the same observed original context."""
    queries = {q["query_id"]: q for q in labels.get("queries", [])}
    contexts: dict[str, list[str]] = defaultdict(list)
    excluded = []
    for binding in labels.get("request_bindings", []):
        ident = binding["query_id"]
        query = queries.get(ident, {})
        if query.get("classification") in {"original", "equivalent_rewrite"}:
            contexts[binding["context_id"]].append(ident)
        else:
            excluded.append(ident)
    targets = {t["target_id"]: t for t in labels.get("targets", [])}
    result = []
    for context_id, ids in sorted(contexts.items()):
        ids = sorted(set(ids))
        unknown = [i for i in ids if queries[i].get("annotation_status") != "reviewed"
                   or not queries[i].get("accepted_target_ids")]
        sets = [set(queries[i].get("accepted_target_ids", [])) for i in ids]
        has_original = any(queries[i].get("classification") == "original" for i in ids)
        eligible = len(ids) >= 2 and has_original and not unknown
        common = set.intersection(*sets) if eligible else set()
        records = []
        for unit in replay.get("units", []):
            observation = unit.get("quality_observation") or {}
            for row in observation.get("context_scores", []):
                if row.get("annotation_id") in ids and row.get("context_id") == context_id:
                    target = _target(row.get("request_scores"))
                    records.append({"kind": "replay", "unit_kind": unit.get("kind"), "unit_id": unit.get("unit_id"),
                        "annotation_id": row["annotation_id"], "output_sha256": observation.get("output_sha256"),
                        "quality_repetition": observation.get("repetition"),
                        "primary_first_hit_rank": target.get("first_hit_rank"),
                        "common_anchor_score": _rank(target if observation.get("repetition") == 1 else {}, common)})
        for group in groups:
            for trial in group.get("trials", []):
                for chain in trial.get("query_chains", []):
                    if chain.get("annotation_id") not in ids or chain.get("context_id") != context_id:
                        continue
                    observation = chain["actual_observation"]
                    target = _target(observation.get("request_scores"))
                    records.append({"kind": "actual_e2e", "group": group["group"], "trial_id": trial["trial_id"],
                        "call_id": chain["call_id"], "annotation_id": chain["annotation_id"],
                        "output_sha256": observation.get("output_sha256"),
                        "primary_first_hit_rank": target.get("first_hit_rank"), "common_anchor_score": _rank(target, common)})
        result.append({"context_id": context_id, "annotation_ids": ids, "has_original_reference": has_original,
            "unknown_annotation_ids": unknown, "status": "scored" if common else "unknown",
            "accepted_target_set_variants": len({tuple(sorted(s)) for s in sets}),
            "per_query_accepted_target_ids": {i: sorted(s) for i, s in zip(ids, sets)},
            "common_accepted_target_ids": sorted(common),
            "common_targets": [targets[i] for i in sorted(common)], "observations": records})
    return {"profile": "shared-reviewed-anchor-diagnostic-v1", "primary_scores_modified": False,
        "post_hoc": True, "cross_round_comparison_supported": False,
        "selection_policy": "Intersect every frozen accepted-target set for the original and equivalent rewrites in the same context; no rank/output-based selection. Subgoals stay separate.",
        "unknown_policy": "Missing original, unknown labels, empty intersection or unavailable scores remain unknown, not zero.",
        "excluded_subgoal_or_other_annotation_ids": sorted(set(excluded)), "contexts": result,
        "limitation": "Post-hoc within-cohort entry comparison only; the intersection is not a fixed cross-round benchmark target set. A common anchor does not exhaust relevance or prove that a query caused lower E2E cost. Different partial target sets confound direct comparison of primary ranks."}
