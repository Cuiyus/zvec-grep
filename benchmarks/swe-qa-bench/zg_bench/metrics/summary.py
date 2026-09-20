"""Trial means, aggregate filters, and equal-weight task summaries."""

from __future__ import annotations

import math
from typing import Any, Sequence

from zg_bench.core.errors import SweQaError
from zg_bench.core.protocol import (
    COMPARISON_KEYS,
    PROFILE_NAMES,
    SCORE_KEYS,
)
from zg_bench.metrics.comparison import (
    compare_profiles,
)
from zg_bench.metrics.numbers import (
    mean_or_none,
    sum_or_none,
)
from zg_bench.metrics.usage import (
    SESSION_USAGE_METRICS,
    SESSION_USAGE_SCOPE,
    compatible_usage_scope,
    summarize_usage,
)


def summarize_profile(trials: Sequence[dict[str, Any]]) -> dict[str, Any]:
    count = len(trials)
    identity = {key: trials[0]["judge"][key] for key in ("label", "model")}
    if any(
        trial["judge"].get(key) != value
        for trial in trials
        for key, value in identity.items()
    ):
        raise SweQaError("profile trials use incompatible judge identities")
    scores = {
        key: sum(trial["judge"]["scores"][key] for trial in trials) / count
        for key in SCORE_KEYS
    }
    usages = [trial["judge"]["usage"] for trial in trials]
    judge = {
        **identity,
        "scores": scores,
        "total": sum(trial["judge"]["total"] for trial in trials) / count,
        "latency_seconds": sum(trial["judge"]["latency_seconds"] for trial in trials)
        / count,
        "usage": {
            "calls": count,
            "input_tokens": sum_or_none([usage["input_tokens"] for usage in usages]),
            "output_tokens": sum_or_none([usage["output_tokens"] for usage in usages]),
            "cost_usd": sum_or_none([usage["cost_usd"] for usage in usages]),
        },
    }
    metric_rows = [trial["metrics"] for trial in trials]
    metrics = {
        "input_tokens": sum(row["input_tokens"] for row in metric_rows) / count,
        "output_tokens": sum(row["output_tokens"] for row in metric_rows) / count,
        "tool_calls": sum(row["tool_calls"] for row in metric_rows) / count,
        "agent_wall_seconds": sum(row["agent_wall_seconds"] for row in metric_rows)
        / count,
        "cost_usd": mean_or_none([row["cost_usd"] for row in metric_rows]),
        **summarize_usage(metric_rows, average=True),
    }
    return {
        "trial_count": count,
        "judge": judge,
        "metrics": metrics,
        "trials": list(trials),
    }


def aggregate_cases(cases: Sequence[dict[str, Any]]) -> dict[str, Any]:
    # Validate every case before filtering. An excluded task must not hide a
    # mixture of legacy root-only and complete session-tree accounting.
    usage_scope = compatible_usage_scope(
        [
            case["profiles"][profile]["metrics"]
            for case in cases
            for profile in PROFILE_NAMES
        ]
    )
    included: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for case in cases:
        baseline_input = case["profiles"]["baseline"]["metrics"]["input_tokens"]
        zvec_input = case["profiles"]["zvec-grep"]["metrics"]["input_tokens"]
        if baseline_input == 0:
            change = 0.0 if zvec_input == 0 else None
        else:
            change = (zvec_input - baseline_input) / baseline_input * 100.0
        baseline_judge = case["profiles"]["baseline"]["judge"]["total"]
        zvec_judge = case["profiles"]["zvec-grep"]["judge"]["total"]
        judge_delta = zvec_judge - baseline_judge
        reasons = []
        if change is None:
            reasons.append("undefined_baseline")
        elif change < -100.0 or change > 100.0:
            reasons.append("input_token_change_outside_range")
        # Profile means can introduce floating-point noise at exactly 10 points.
        # Check both criteria so an overlap retains both reasons in the evidence.
        if abs(judge_delta) > 10.0 and not math.isclose(
            abs(judge_delta), 10.0, rel_tol=0.0, abs_tol=1e-9
        ):
            reasons.append("judge_delta_outside_range")
        if reasons:
            excluded.append(
                {
                    "task_id": case["task_id"],
                    "baseline_input_tokens": baseline_input,
                    "zvec_grep_input_tokens": zvec_input,
                    "change_pct": change,
                    "baseline_judge": baseline_judge,
                    "zvec_grep_judge": zvec_judge,
                    "judge_delta": judge_delta,
                    "reasons": reasons,
                }
            )
        else:
            included.append(case)

    count = len(included)
    profiles: dict[str, dict[str, Any]] = {}
    for profile in PROFILE_NAMES:
        profile_rows = [case["profiles"][profile] for case in included]
        if not profile_rows:
            # A single-task report can legitimately have no included tasks.
            # Keep its raw case available for merging, but do not present zero
            # usage or zero quality as a measurement of an empty population.
            profiles[profile] = {
                key: None
                for key in (
                    "judge",
                    "input_tokens",
                    "output_tokens",
                    "tool_calls",
                    "agent_wall_seconds",
                    "cost_usd",
                )
            }
            profiles[profile]["usage_scope"] = usage_scope
            if usage_scope == SESSION_USAGE_SCOPE:
                profiles[profile].update({key: None for key in SESSION_USAGE_METRICS})
                profiles[profile]["session_usage"] = {
                    "scope": usage_scope,
                    "complete": True,
                    **{
                        part: {key: None for key in SESSION_USAGE_METRICS}
                        for part in ("root", "descendants", "total")
                    },
                }
            continue
        profiles[profile] = {
            "judge": sum(row["judge"]["total"] for row in profile_rows) / count,
            "input_tokens": sum(row["metrics"]["input_tokens"] for row in profile_rows),
            "output_tokens": sum_or_none(
                [row["metrics"].get("output_tokens") for row in profile_rows]
            ),
            "tool_calls": sum(row["metrics"]["tool_calls"] for row in profile_rows),
            "agent_wall_seconds": sum(
                row["metrics"]["agent_wall_seconds"] for row in profile_rows
            ),
            "cost_usd": sum_or_none(
                [row["metrics"]["cost_usd"] for row in profile_rows]
            ),
            **summarize_usage([row["metrics"] for row in profile_rows], average=False),
        }
    # Efficiency percentages must describe the displayed aggregate sums, not
    # an average of task percentages that can disagree even in direction.
    if count:
        comparison = compare_profiles(
            profiles["baseline"],
            profiles["zvec-grep"],
            profiles["baseline"]["judge"],
            profiles["zvec-grep"]["judge"],
        )
    else:
        comparison = {key: None for key in COMPARISON_KEYS}
    comparison_samples = {
        key: count if value is not None else 0 for key, value in comparison.items()
    }
    return {
        "profiles": profiles,
        "comparison": comparison,
        "comparison_samples": comparison_samples,
        "comparison_basis": "ratio_of_aggregate_profile_means",
        "filter": {
            "criteria": [
                {
                    "metric": "input_tokens",
                    "comparison": "change_pct",
                    "min": -100.0,
                    "max": 100.0,
                },
                {
                    "metric": "judge",
                    "comparison": "delta",
                    "min": -10.0,
                    "max": 10.0,
                    "unit": "points",
                },
            ],
            "total_count": len(cases),
            "included_count": count,
            "excluded_count": len(excluded),
            "included_task_ids": [case["task_id"] for case in included],
            "excluded_tasks": excluded,
        },
    }
