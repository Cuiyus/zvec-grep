"""PR #261 score trimming and task screening for native Claude QA reports.

Selection functions retain the exact upstream source text. The local adapter
uses explicitly named native token/time scopes and never invents provider cost.
Original frozen phase gates remain separate from these descriptive statistics.
"""
from __future__ import annotations

import math
import statistics
from collections.abc import Sequence
from typing import Any

class SweQaError(ValueError):
    """Invalid statistical report input."""



def sum_or_none(values: Sequence[int | float | None]) -> int | float | None:
    if any(value is None for value in values):
        return None
    return sum(value for value in values if value is not None)


def mean_or_none(values: Sequence[int | float | None]) -> float | None:
    if not values or any(value is None for value in values):
        return None
    return sum(float(value) for value in values if value is not None) / len(values)


DISPERSION_METRICS = ("judge", "agent_wall_seconds", "input_tokens")
TASK_SCREEN_POLICY = {
    "method": "task_max_residual_dispersion_mad",
    "score_dispersion": "sample_standard_deviation",
    "resource_dispersion": "sample_standard_deviation_over_arithmetic_mean",
    "ddof": 1,
    "task_statistic": "maximum_over_supplied_profiles",
    "reference": "median_and_unscaled_mad_over_all_task_maxima",
    "coefficient": 0.6745,
    "cutoff": 3.5,
    "direction": "upper_tail_strictly_greater",
    "minimum_tasks": 5,
    "minimum_tasks_reason": "engineering_guard_not_a_statistical_standard",
    "missing_metric": "disable_metric_for_all_tasks",
    "zero_mad": "disable_metric_for_all_tasks",
    "passes": 1,
    "refit_after_exclusion": False,
    "paired_task_exclusion": "all_supplied_profiles",
    "interpretation": "relative_extreme_dispersion_candidates_not_invalid_data",
    "error_control": "no_calibrated_familywise_error_guarantee",
}


def _finite_nonnegative(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value) and value >= 0
    except OverflowError:
        return False


def residual_dispersion(
    retained_trials: Sequence[dict[str, Any]] | None,
) -> dict[str, Any]:
    """Measure the same retained tuples used for every reported trial mean."""
    count = len(retained_trials) if retained_trials is not None else None
    results = {}
    for metric in DISPERSION_METRICS:
        row = {
            "status": "available",
            "value": None,
            "sample_count": count,
            "mean": None,
            "sample_sd": None,
        }
        results[metric] = row
        if retained_trials is None:
            row["status"] = "unavailable_no_trial_evidence"
            continue
        if len(retained_trials) < 2:
            row["status"] = "unavailable_insufficient_samples"
            continue
        values = [
            trial.get("judge", {}).get("total")
            if metric == "judge"
            else trial.get("metrics", {}).get(metric)
            for trial in retained_trials
        ]
        if any(value is None for value in values):
            row["status"] = "unavailable_missing_values"
            continue
        if not all(_finite_nonnegative(value) for value in values):
            row["status"] = "unavailable_invalid_values"
            continue
        try:
            mean = statistics.mean(values)
            sd = statistics.stdev(values)
            if not _finite_nonnegative(mean) or not _finite_nonnegative(sd):
                raise ArithmeticError("non-finite dispersion")
            row.update(mean=mean, sample_sd=sd)
            if metric != "judge" and mean == 0:
                row["status"] = "unavailable_zero_mean"
                continue
            value = sd if metric == "judge" else sd / mean
            if not _finite_nonnegative(value):
                raise ArithmeticError("non-finite dispersion")
            row["value"] = value
        except (ArithmeticError, OverflowError, ValueError):
            row.update(status="unavailable_invalid_values", value=None)
    return results


def _task_maximum(profiles: dict[str, Any], metric: str) -> dict[str, Any]:
    unavailable = []
    values = {}
    for name in sorted(profiles):
        dispersion = profiles[name].get("dispersion", {}).get(metric, {})
        status = dispersion.get("status", "unavailable_missing_dispersion")
        value = dispersion.get("value")
        if status == "available" and not _finite_nonnegative(value):
            status = "unavailable_invalid_values"
        if status != "available":
            unavailable.append({"profile": name, "status": status})
        else:
            values[name] = value
    if unavailable:
        return {
            "status": "unavailable_missing_dispersion",
            "value": None,
            "source_profile": None,
            "modified_z": None,
            "unavailable_profiles": unavailable,
        }
    name = max(values, key=values.__getitem__)
    return {
        "status": "available",
        "value": values[name],
        "source_profile": name,
        "modified_z": None,
        "unavailable_profiles": [],
    }


def _metric_reference(decisions: dict[str, Any], metric: str) -> dict[str, Any]:
    missing = [
        task_id
        for task_id, row in decisions.items()
        if row["metrics"][metric]["status"] != "available"
    ]
    reference = {
        "status": "available",
        "median": None,
        "mad": None,
        "upper_threshold": None,
        "unavailable_task_ids": missing,
    }
    if len(decisions) < TASK_SCREEN_POLICY["minimum_tasks"]:
        reference["status"] = "unavailable_insufficient_tasks"
        return reference
    if missing:
        reference["status"] = "unavailable_missing_dispersion"
        return reference
    values = [row["metrics"][metric]["value"] for row in decisions.values()]
    center = statistics.median(values)
    mad = statistics.median(abs(value - center) for value in values)
    if not _finite_nonnegative(center) or not _finite_nonnegative(mad):
        reference["status"] = "unavailable_invalid_reference"
        return reference
    reference.update(median=center, mad=mad)
    if mad == 0:
        reference["status"] = "unavailable_zero_mad"
        return reference
    coefficient = TASK_SCREEN_POLICY["coefficient"]
    cutoff = TASK_SCREEN_POLICY["cutoff"]
    threshold = center + cutoff * mad / coefficient
    standardized = [coefficient * (value - center) / mad for value in values]
    if not math.isfinite(threshold) or not all(map(math.isfinite, standardized)):
        reference["status"] = "unavailable_invalid_reference"
        return reference
    reference["upper_threshold"] = threshold
    for row, z in zip(decisions.values(), standardized, strict=True):
        row["metrics"][metric]["modified_z"] = z
    return reference


def screen_tasks(tasks: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Screen supplied task statistics once without changing source evidence.

    Every task must provide the same named profiles, so its maximum is taken
    over an equal set of comparison groups. One unavailable group disables that
    metric across the suite; other complete metrics can still be used.
    """
    decisions = {}
    profile_names: set[str] | None = None
    for task in tasks:
        task_id = task.get("task_id")
        if not isinstance(task_id, str) or not task_id or task_id in decisions:
            raise SweQaError("task screen requires non-empty, unique task IDs")
        profiles = task.get("profiles")
        if not isinstance(profiles, dict) or not profiles:
            raise SweQaError("task screen requires non-empty profile groups")
        names = set(profiles)
        if not all(isinstance(name, str) and name for name in names):
            raise SweQaError("task screen requires non-empty profile names")
        if profile_names is not None and names != profile_names:
            raise SweQaError(
                "task screen requires the same profile groups for all tasks"
            )
        profile_names = names
        decisions[task_id] = {
            "status": "unscreened",
            "reasons": [],
            "metrics": {
                metric: _task_maximum(profiles, metric) for metric in DISPERSION_METRICS
            },
        }
    references = {
        metric: _metric_reference(decisions, metric) for metric in DISPERSION_METRICS
    }
    available = [
        metric for metric, row in references.items() if row["status"] == "available"
    ]
    cutoff = TASK_SCREEN_POLICY["cutoff"]
    for row in decisions.values():
        for metric in available:
            measure = row["metrics"][metric]
            if measure["modified_z"] > cutoff:
                row["reasons"].append(
                    {
                        "metric": metric,
                        "source_profile": measure["source_profile"],
                        "dispersion": measure["value"],
                        "modified_z": measure["modified_z"],
                        "upper_threshold": references[metric]["upper_threshold"],
                    }
                )
        row["status"] = (
            "excluded" if row["reasons"] else "retained" if available else "unscreened"
        )
    excluded = [
        task_id for task_id, row in decisions.items() if row["status"] == "excluded"
    ]
    retained = [task_id for task_id in decisions if task_id not in excluded]

    def total(name: str, field: str, selected: Sequence[dict[str, Any]]) -> int | None:
        return sum_or_none(
            [task["profiles"][name].get("filter", {}).get(field) for task in selected]
        )

    retained_tasks = [task for task in tasks if task["task_id"] in retained]
    counts = {
        name: {
            "original_count": total(name, "original_count", tasks),
            "after_trim_count": total(name, "retained_count", tasks),
            "final_count": total(name, "retained_count", retained_tasks),
        }
        for name in sorted(profile_names or ())
    }
    if len(tasks) < TASK_SCREEN_POLICY["minimum_tasks"]:
        status = "unavailable_insufficient_tasks"
    elif not available:
        status = "unavailable_no_metrics"
    elif len(available) == len(DISPERSION_METRICS):
        status = "applied"
    else:
        status = "partially_applied"
    return {
        "status": status,
        "policy": dict(TASK_SCREEN_POLICY),
        "original_task_count": len(tasks),
        "retained_task_count": len(retained),
        "excluded_task_count": len(excluded),
        "original_task_ids": list(decisions),
        "retained_task_ids": retained,
        "excluded_task_ids": excluded,
        "references": references,
        "task_decisions": decisions,
        "profiles": counts,
    }


TRIM_POLICY = {
    "method": "score_trim_min_max_once",
    "group_by": ["model", "task_id", "profile"],
    "trim_count_per_tail": 1,
    "minimum_samples": 3,
    "insufficient_samples": "retain_all",
    "tie_breaker": "trial_index_then_original_position",
    "passes": 1,
    "selection_metric": "judge.total",
    "aggregation": "equal_weight_task_means",
}


def _mean_metrics(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    # Preserve one common set of tuples for every native resource measurement.
    keys = sorted({key for row in rows for key in row})
    return {key: mean_or_none([row.get(key) for row in rows]) for key in keys}


def profile_statistics(profile: dict[str, Any]) -> dict[str, Any]:
    """Select once by score, then use the same retained trials for every metric.

    Keep the original profile (including all judge calls and trial evidence)
    intact. Legacy summary-only evidence cannot be screened retroactively.
    """
    trials = profile.get("trials")
    if not trials:
        raw = {"judge": profile["judge"]["total"], **profile["metrics"]}
        return {
            "raw": raw,
            "filtered": dict(raw),
            "dispersion": residual_dispersion(None),
            "filter": {
                "status": "unavailable_no_trial_evidence",
                "original_count": None,
                "retained_count": None,
                "excluded_count": None,
                "excluded_trials": [],
            },
        }
    ranked = sorted(
        enumerate(trials, start=1),
        key=lambda item: (
            item[1]["judge"]["total"],
            item[1].get("trial_index", item[0]),
            item[0],
        ),
    )
    trimmed_positions = (
        {ranked[0][0]: "low", ranked[-1][0]: "high"} if len(trials) >= 3 else {}
    )
    retained, excluded = [], []
    retained_indexes = []
    for position, trial in enumerate(trials, start=1):
        score = trial["judge"]["total"]
        trial_index = trial.get("trial_index", position)
        if position not in trimmed_positions:
            retained.append(trial)
            retained_indexes.append(trial_index)
        else:
            excluded.append(
                {
                    "trial_index": trial_index,
                    "trial_name": trial.get("trial_name"),
                    "score": score,
                    "direction": trimmed_positions[position],
                }
            )

    def summarize(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
        return {
            "judge": sum(row["judge"]["total"] for row in rows) / len(rows),
            **_mean_metrics([row["metrics"] for row in rows]),
        }

    return {
        "raw": summarize(trials),
        "filtered": summarize(retained),
        "dispersion": residual_dispersion(retained),
        "filter": {
            "status": "applied"
            if trimmed_positions
            else "unavailable_insufficient_trials",
            "original_count": len(trials),
            "retained_count": len(retained),
            "excluded_count": len(excluded),
            "retained_trial_indexes": retained_indexes,
            "excluded_trials": excluded,
        },
    }


PROFILE_NAMES = ("baseline", "zvec-grep")
REQUIRED_METRICS = ("input_tokens", "output_tokens", "tool_calls", "agent_wall_seconds")


def _validate_cases(cases: Sequence[dict[str, Any]]) -> None:
    if not cases:
        raise SweQaError("report requires at least one task")
    task_ids = set()
    for case in cases:
        task_id = case.get("task_id")
        if not isinstance(task_id, str) or not task_id or task_id in task_ids:
            raise SweQaError("report requires unique non-empty task IDs")
        task_ids.add(task_id)
        if set(case.get("profiles", {})) != set(PROFILE_NAMES):
            raise SweQaError("each task requires both comparison profiles")
        paired_indexes = None
        for name in PROFILE_NAMES:
            trials = case["profiles"][name].get("trials")
            if not isinstance(trials, list) or not trials:
                raise SweQaError("each profile requires complete trial evidence")
            indexes = set()
            for trial in trials:
                index = trial.get("trial_index")
                if isinstance(index, bool) or not isinstance(index, int) or index < 1 or index in indexes:
                    raise SweQaError("trial indexes must be unique positive integers")
                indexes.add(index)
                if not _finite_nonnegative(trial.get("judge", {}).get("total")):
                    raise SweQaError("every raw trial requires a finite nonnegative score")
                metrics = trial.get("metrics", {})
                if any(not _finite_nonnegative(metrics.get(key)) for key in REQUIRED_METRICS):
                    raise SweQaError("every raw trial requires complete finite resource metrics")
                if any(value is not None and not _finite_nonnegative(value) for value in metrics.values()):
                    raise SweQaError("optional resource values must be numeric or explicitly unknown")
                if "quality_pass" in trial and not isinstance(trial["quality_pass"], bool):
                    raise SweQaError("quality_pass must retain the original boolean decision")
            if paired_indexes is not None and indexes != paired_indexes:
                raise SweQaError("comparison profiles must have the same planned trial indexes")
            paired_indexes = indexes


def aggregate_cases(cases: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Use unchanged PR selection with native metrics and equal task weights.

    This descriptive view never changes original execution or quality decisions.
    Invalid/missing observations must be reported upstream, never silently dropped.
    """
    _validate_cases(cases)
    tasks = [
        {"task_id": case["task_id"], "profiles": {
            name: profile_statistics(case["profiles"][name]) for name in PROFILE_NAMES
        }} for case in cases
    ]
    task_filter = screen_tasks(tasks)
    retained_ids = set(task_filter["retained_task_ids"])

    def aggregate(view: str, selected: Sequence[dict[str, Any]]) -> dict[str, Any]:
        profiles = {}
        for name in PROFILE_NAMES:
            rows = [task["profiles"][name][view] for task in selected]
            keys = sorted({key for task in tasks for key in task["profiles"][name][view]})
            profiles[name] = {key: mean_or_none([row.get(key) for row in rows]) for key in keys}
        baseline, zvec = (profiles[name] for name in PROFILE_NAMES)
        comparisons = {"judge_delta": None if not selected else zvec["judge"] - baseline["judge"]}
        for key in sorted(set(baseline) - {"judge"}):
            left, right = baseline[key], zvec[key]
            comparisons[key + "_reduction_pct"] = (
                None if left is None or right is None or left == 0 else (left - right) / left * 100
            )
        return {"task_count": len(selected), "profiles": profiles, "comparison": comparisons}

    final = aggregate("filtered", [task for task in tasks if task["task_id"] in retained_ids])
    return {
        "raw": aggregate("raw", tasks),
        "score_trimmed": aggregate("filtered", tasks),
        "task_screened": final,
        "task_filter": task_filter,
        "task_statistics": tasks,
        "trim_policy": dict(TRIM_POLICY),
        "planned_trials_per_task_profile": 10,
        "planned_trial_count_complete": all(
            len(case["profiles"][name]["trials"]) == 10 for case in cases for name in PROFILE_NAMES
        ),
        "comparison_basis": "ratio_of_equal_weight_task_profile_means",
        "frozen_quality_and_execution_decisions_modified": False,
        "interpretation": "Relative variability screening is descriptive and does not prove an execution invalid.",
    }


def main() -> None:
    import argparse
    import json
    from pathlib import Path

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="JSON with explicit measurement_scope, time_scope and cases")
    parser.add_argument("output", type=Path, help="New report path; existing files are never overwritten")
    args = parser.parse_args()
    payload = json.loads(args.input.read_text())
    for key in ("measurement_scope", "time_scope"):
        if not isinstance(payload.get(key), str) or not payload[key].strip():
            raise SweQaError(f"{key} must explicitly identify the measurements")
    report = {"schema_version": 1, "measurement_scope": payload["measurement_scope"],
              "time_scope": payload["time_scope"], **aggregate_cases(payload["cases"])}
    with args.output.open("x") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


if __name__ == "__main__":
    main()

