"""Profile and paired-trial comparisons over normalized measurements."""

from __future__ import annotations

from typing import Any, Sequence

from zg_bench.core.errors import SweQaError


def _reduction(
    baseline: float | int | None, candidate: float | int | None
) -> float | None:
    if baseline is None or candidate is None or baseline == 0:
        return None
    return (float(baseline) - float(candidate)) / float(baseline) * 100.0


def compare_profiles(
    baseline: dict[str, Any],
    zvec: dict[str, Any],
    judge_b: int | float,
    judge_z: int | float,
) -> dict[str, Any]:
    return {
        "judge_delta": judge_z - judge_b,
        "input_token_reduction_pct": _reduction(
            baseline["input_tokens"], zvec["input_tokens"]
        ),
        "toolcall_reduction_pct": _reduction(
            baseline["tool_calls"], zvec["tool_calls"]
        ),
        "time_reduction_pct": _reduction(
            baseline["agent_wall_seconds"], zvec["agent_wall_seconds"]
        ),
        "cost_reduction_pct": _reduction(baseline["cost_usd"], zvec["cost_usd"]),
    }


def _paired_trials(
    baseline_trials: Sequence[dict[str, Any]],
    zvec_trials: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    if len(baseline_trials) != len(zvec_trials):
        raise SweQaError("profile trial counts do not match")
    baseline_by_index = {trial["trial_index"]: trial for trial in baseline_trials}
    zvec_by_index = {trial["trial_index"]: trial for trial in zvec_trials}
    if (
        len(baseline_by_index) != len(baseline_trials)
        or len(zvec_by_index) != len(zvec_trials)
        or set(baseline_by_index) != set(zvec_by_index)
    ):
        raise SweQaError("profile trial_index values do not match")

    trial_rows: list[dict[str, Any]] = []
    for trial_index in sorted(baseline_by_index):
        baseline = baseline_by_index[trial_index]
        zvec = zvec_by_index[trial_index]
        trial_rows.append(
            {
                "trial_index": trial_index,
                **compare_profiles(
                    baseline["metrics"],
                    zvec["metrics"],
                    baseline["judge"]["total"],
                    zvec["judge"]["total"],
                ),
            }
        )
    return trial_rows


def case_comparison(case: dict[str, Any]) -> dict[str, Any]:
    """Compare the baseline/zvec profile means displayed for one task."""
    profiles = case["profiles"]
    baseline = profiles["baseline"]
    zvec = profiles["zvec-grep"]
    comparison = compare_profiles(
        baseline["metrics"],
        zvec["metrics"],
        baseline["judge"]["total"],
        zvec["judge"]["total"],
    )

    baseline_trials = profiles["baseline"].get("trials")
    zvec_trials = profiles["zvec-grep"].get("trials")
    if baseline_trials is None and zvec_trials is None:
        return comparison
    if not isinstance(baseline_trials, list) or not isinstance(zvec_trials, list):
        raise SweQaError("profile trial evidence does not match")
    # Keep paired trial comparisons as diagnostic evidence only. The task-level
    # values above are calculated from the displayed profile means.
    comparison["trials"] = _paired_trials(baseline_trials, zvec_trials)
    return comparison
