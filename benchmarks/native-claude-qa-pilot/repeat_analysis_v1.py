"""Fixed current-task repetition analysis; no provider calls or execution.

Bootstrap samples whole baseline/zg repeat pairs within each fixed task. Both
profiles use the same draws, then PR #261 trims their score tails separately.
Original trial identities remain the deterministic tie breakers, including for
repeated bootstrap draws. Intervals describe this fixed task sample only.
"""
from __future__ import annotations

from collections import Counter
import math
import random
from typing import Any

from robust_statistics_v1 import (
    PROFILE_NAMES, SweQaError, aggregate_cases, mean_or_none, profile_statistics,
    residual_dispersion,
)

BOOTSTRAP_SEED = 20261020
BOOTSTRAP_RESAMPLES = 10000
EXPECTED_TASK_IDS = ("q001", "q002", "q003")
VIEWS = ("raw", "score_trimmed")
MEASURES = ("judge_delta", "quality_pass_rate_delta", "input_reduction_pct",
            "agent_time_reduction_pct", "outer_slot_reduction_pct")


def _retained_trials(trials, statistics):
    # A multiset preserves repeated bootstrap observations; a set would silently
    # retain the wrong count when the same original trial is drawn more than once.
    counts = Counter(statistics["filter"]["retained_trial_indexes"])
    retained = []
    for trial in trials:
        index = trial["trial_index"]
        if counts[index]:
            retained.append(trial)
            counts[index] -= 1
    if any(counts.values()) or len(retained) != statistics["filter"]["retained_count"]:
        raise SweQaError("retained tuple accounting disagrees")
    return retained


def _task_estimates(profiles):
    reduced = {name: profile_statistics(profiles[name]) for name in PROFILE_NAMES}
    estimates = {}
    for view in VIEWS:
        rows = {}
        for name in PROFILE_NAMES:
            trials = profiles[name]["trials"]
            selected = trials if view == "raw" else _retained_trials(trials, reduced[name])
            row = dict(reduced[name]["raw" if view == "raw" else "filtered"])
            row["quality_pass_rate"] = sum(trial["quality_pass"] for trial in selected) / len(selected)
            rows[name] = row
        estimates[view] = rows
    return estimates


def _comparison(profiles):
    baseline, zvec = (profiles[name] for name in PROFILE_NAMES)
    result = {key + "_delta": zvec[key] - baseline[key]
              for key in ("judge", "quality_pass_rate")}
    for output, source in (("input_reduction_pct", "input_tokens"),
                           ("agent_time_reduction_pct", "agent_wall_seconds"),
                           ("outer_slot_reduction_pct", "outer_slot_seconds")):
        left, right = baseline.get(source), zvec.get(source)
        result[output] = None if left is None or right is None or left == 0 else (left - right) / left * 100
    return result


def _macro(task_profiles):
    result = {}
    for name in PROFILE_NAMES:
        keys = set().union(*(profiles[name] for profiles in task_profiles))
        result[name] = {key: mean_or_none([profiles[name].get(key) for profiles in task_profiles]) for key in keys}
    return result


def _quantile(values, probability):
    ordered = sorted(values)
    position = probability * (len(ordered) - 1)
    lo, hi = math.floor(position), math.ceil(position)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (position - lo)


def _interval(values, expected):
    valid = [value for value in values if value is not None and math.isfinite(value)]
    if len(valid) != expected:
        return {"status": "unavailable_incomplete_resamples", "valid_resamples": len(valid),
                "expected_resamples": expected, "lower": None, "upper": None}
    return {"status": "available", "valid_resamples": expected, "expected_resamples": expected,
            "lower": _quantile(valid, 0.025), "upper": _quantile(valid, 0.975)}


def analyze(cases, *, resamples=BOOTSTRAP_RESAMPLES, seed=BOOTSTRAP_SEED):
    if type(resamples) is not int or resamples < 1:
        raise SweQaError("positive bootstrap resample count required")
    if type(seed) is not int:
        raise SweQaError("integer bootstrap seed required")
    base = aggregate_cases(cases)
    if sorted(case["task_id"] for case in cases) != sorted(EXPECTED_TASK_IDS):
        raise SweQaError("this study requires exactly the three predeclared existing tasks")
    if not base["planned_trial_count_complete"]:
        raise SweQaError("full fixed ten-repeat evidence required; retain incomplete evidence separately")
    for case in cases:
        for name in PROFILE_NAMES:
            trials = case["profiles"][name]["trials"]
            if {trial["trial_index"] for trial in trials} != set(range(1, 11)):
                raise SweQaError("study requires every predeclared repeat index 1 through 10")
            if any(type(trial.get("quality_pass")) is not bool for trial in trials):
                raise SweQaError("every trial requires its original complete quality decision")
    if base["task_filter"]["excluded_task_ids"] or base["task_filter"]["status"] != "unavailable_insufficient_tasks":
        raise SweQaError("three-task study cannot enable task-level screening")

    ordered = sorted(cases, key=lambda case: case["task_id"])
    points = {case["task_id"]: _task_estimates(case["profiles"]) for case in ordered}
    points["all_tasks"] = {view: _macro([points[case["task_id"]][view] for case in ordered]) for view in VIEWS}
    distributions = {task: {view: {key: [] for key in MEASURES} for view in VIEWS} for task in points}
    rng = random.Random(seed)
    for _ in range(resamples):
        draw = {}
        for case in ordered:
            indexed = {name: {trial["trial_index"]: trial for trial in case["profiles"][name]["trials"]}
                       for name in PROFILE_NAMES}
            indexes = [rng.randrange(1, 11) for _ in range(10)]
            profiles = {name: {"trials": [indexed[name][index] for index in indexes]} for name in PROFILE_NAMES}
            draw[case["task_id"]] = _task_estimates(profiles)
        draw["all_tasks"] = {view: _macro([draw[case["task_id"]][view] for case in ordered]) for view in VIEWS}
        for task in draw:
            for view in VIEWS:
                comparison = _comparison(draw[task][view])
                for key in MEASURES:
                    distributions[task][view][key].append(comparison[key])

    results = {}
    for task in points:
        result = {view: {"profiles": points[task][view], "comparison": _comparison(points[task][view]),
                         "intervals": {key: _interval(distributions[task][view][key], resamples) for key in MEASURES}}
                  for view in VIEWS}
        result["interval_width_change_pct"] = {}
        for key in MEASURES:
            raw, trimmed = (result[view]["intervals"][key] for view in VIEWS)
            width = raw["upper"] - raw["lower"] if raw["status"] == "available" else None
            other = trimmed["upper"] - trimmed["lower"] if trimmed["status"] == "available" else None
            result["interval_width_change_pct"][key] = None if width in (None, 0) or other is None else (other - width) / width * 100
        result["statistical_criterion_met_only"] = all(
            result[view]["intervals"][key]["status"] == "available"
            and (result[view]["intervals"][key]["lower"] > 0 if key == "input_reduction_pct"
                 else result[view]["intervals"][key]["lower"] >= 0)
            for view in VIEWS for key in ("input_reduction_pct", "judge_delta", "quality_pass_rate_delta"))
        results[task] = result
    validity_by_task = {case["task_id"]: all(
        trial.get("execution_and_scope_valid") is True and trial.get("reported_metric_eligible") is True
        for profile in case["profiles"].values() for trial in profile["trials"])
        for case in cases}
    validity = all(validity_by_task.values())
    validity_by_task["all_tasks"] = validity
    for task, result in results.items():
        result["all_execution_scope_and_reported_metrics_valid"] = validity_by_task[task]
        result["stable_reported_input_with_quality_preserved"] = (
            validity_by_task[task] and result["statistical_criterion_met_only"])
    return {"schema_version": 1, "statistics": base, "repeated_estimates": results,
            "raw_dispersion": {case["task_id"]: {name: residual_dispersion(case["profiles"][name]["trials"])
                                                for name in PROFILE_NAMES} for case in ordered},
            "bootstrap": {"seed": seed, "resamples": resamples, "confidence_level": 0.95,
                          "unit": "whole baseline/zg repetition pair, sampled within each fixed task",
                          "task_weights": "equal; task identities are not resampled",
                          "trim": "recompute original PR score-tail selection after each paired draw",
                          "ties": "original trial_index, then bootstrap occurrence position",
                          "percentile_method": "linear_n_minus_1"},
            "all_execution_scope_and_reported_metrics_valid": validity,
            "stable_reported_input_with_quality_preserved_on_all_existing_tasks": validity and all(
                result["stable_reported_input_with_quality_preserved"] for result in results.values()),
            "scope": "Observed repeatability on these three fixed tasks and this pinned stack; no population or provider-billing guarantee.",
            "filtering_reduced_variability": "Inspect raw versus trimmed dispersion and interval widths; trimming is not assumed to reduce every metric's variability."}


def main():
    import argparse
    import hashlib
    import json
    from pathlib import Path

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    data = args.input.read_bytes()
    payload = json.loads(data)
    for key in ("measurement_scope", "time_scope"):
        if not isinstance(payload.get(key), str) or not payload[key].strip():
            raise SweQaError(f"explicit {key} required")
    report = {"input_sha256": hashlib.sha256(data).hexdigest(),
              "measurement_scope": payload["measurement_scope"], "time_scope": payload["time_scope"],
              **analyze(payload["cases"])}
    with args.output.open("x") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


if __name__ == "__main__":
    main()
