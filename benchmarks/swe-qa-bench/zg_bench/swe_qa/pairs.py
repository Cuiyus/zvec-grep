"""Load and validate the SWE-QA pairs and reference answers used for judging."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Sequence

from zg_bench.core.errors import (
    SweQaError,
)
from zg_bench.core.io import (
    load_object,
)
from zg_bench.core.protocol import (
    PROFILE_NAMES,
)
from zg_bench.metrics.usage import (
    compatible_usage_scope,
    validate_usage_metrics,
)


def load_references(path: Path) -> dict[str, dict[str, Any]]:
    root = load_object(path, label="references")
    records = root.get("references")
    if not isinstance(records, list):
        raise SweQaError("references.references must be an array")
    result: dict[str, dict[str, Any]] = {}
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            raise SweQaError(f"references[{index}] must be an object")
        task_id = record.get("task_id")
        question = record.get("question")
        answer = record.get("reference_answer")
        if not all(
            isinstance(value, str) and value.strip()
            for value in (task_id, question, answer)
        ):
            raise SweQaError(f"references[{index}] has missing judge input")
        if task_id in result:
            raise SweQaError(f"duplicate reference for {task_id}")
        result[task_id] = record
        slug = task_id.replace(":", "-")
        if slug != task_id:
            if slug in result:
                raise SweQaError(f"reference alias collides for {task_id}")
            result[slug] = record
    return result


def _pair_paths(root: Path) -> list[Path]:
    if root.is_file():
        return [root]
    if not root.is_dir():
        raise SweQaError(f"pairs root does not exist: {root}")
    return sorted(
        path
        for path in root.rglob("*.json")
        if path.is_file()
        and (path.name == "pair.json" or path.name.startswith("pair-"))
    )


def _validate_trial(
    trial: Any, *, task: str, profile: str, default_index: int
) -> dict[str, Any]:
    if not isinstance(trial, dict):
        raise SweQaError(f"{task} {profile} trial {default_index} is invalid")
    answer = trial.get("answer")
    if not isinstance(answer, str) or not answer.strip():
        raise SweQaError(f"{task} {profile} trial {default_index} has an empty answer")
    for key in ("input_tokens", "output_tokens", "tool_calls"):
        value = trial.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise SweQaError(
                f"{task} {profile} trial {default_index} has invalid {key}"
            )
        if key != "tool_calls" and value == 0:
            raise SweQaError(
                f"{task} {profile} trial {default_index} has invalid {key}"
            )
    wall = trial.get("agent_wall_seconds")
    if (
        isinstance(wall, bool)
        or not isinstance(wall, (int, float))
        or not math.isfinite(float(wall))
        or wall <= 0
    ):
        raise SweQaError(
            f"{task} {profile} trial {default_index} has invalid agent_wall_seconds"
        )
    cost = trial.get("cost_usd")
    if cost is not None and (
        isinstance(cost, bool)
        or not isinstance(cost, (int, float))
        or not math.isfinite(float(cost))
        or cost < 0
    ):
        raise SweQaError(f"{task} {profile} trial {default_index} has invalid cost_usd")
    trial_index = trial.get("trial_index", default_index)
    if (
        isinstance(trial_index, bool)
        or not isinstance(trial_index, int)
        or trial_index < 1
    ):
        raise SweQaError(f"{task} {profile} has invalid trial_index")
    normalized = dict(trial)
    normalized["trial_index"] = trial_index
    normalized["usage_scope"] = validate_usage_metrics(normalized, integer=True)
    return normalized


def _validate_profile(profile: Any, *, task: str, name: str) -> list[dict[str, Any]]:
    if not isinstance(profile, dict):
        raise SweQaError(f"{task} has no valid {name} profile")
    raw_trials = profile.get("trials")
    if raw_trials is None:
        raw_trials = [profile]
    if not isinstance(raw_trials, list) or not raw_trials:
        raise SweQaError(f"{task} {name} has no trials")
    trials = [
        _validate_trial(
            trial,
            task=task,
            profile=name,
            default_index=index,
        )
        for index, trial in enumerate(raw_trials, start=1)
    ]
    trials.sort(key=lambda trial: trial["trial_index"])
    indexes = [trial["trial_index"] for trial in trials]
    if indexes != list(range(1, len(trials) + 1)):
        raise SweQaError(
            f"{task} {name} trial_index values must be unique and contiguous"
        )
    declared_count = profile.get("trial_count")
    if declared_count is not None and declared_count != len(trials):
        raise SweQaError(f"{task} {name} trial_count does not match trials")
    return trials


def load_pairs(root: Path, expected: Sequence[str]) -> dict[str, dict[str, Any]]:
    expected_set = set(expected)
    if len(expected_set) != len(expected) or not expected:
        raise SweQaError("expected tasks must be non-empty and unique")
    pairs: dict[str, dict[str, Any]] = {}
    for path in _pair_paths(root):
        pair = load_object(path, label="pair")
        task_id = pair.get("task_id")
        if not isinstance(task_id, str) or not task_id.strip():
            raise SweQaError(f"pair has no task_id: {path}")
        if task_id not in expected_set:
            continue
        if task_id in pairs:
            raise SweQaError(f"multiple pair artifacts found for {task_id}")
        if pair.get("valid") is not True:
            raise SweQaError(f"pair is not marked valid for {task_id}")
        profiles = pair.get("profiles")
        if not isinstance(profiles, dict):
            raise SweQaError(f"pair has no profiles for {task_id}")
        trial_counts: dict[str, int] = {}
        for name in PROFILE_NAMES:
            profile = profiles.get(name)
            if not isinstance(profile, dict):
                raise SweQaError(f"{task_id} has no valid {name} profile")
            trials = _validate_profile(profile, task=task_id, name=name)
            profile["trials"] = trials
            profile["trial_count"] = len(trials)
            trial_counts[name] = len(trials)
        if len(set(trial_counts.values())) != 1:
            raise SweQaError(f"{task_id} profile trial counts do not match")
        actual_trials = next(iter(trial_counts.values()))
        expected_trials = pair.get("expected_trials", actual_trials)
        declared_actual = pair.get("actual_trials", actual_trials)
        for label, value in (
            ("expected_trials", expected_trials),
            ("actual_trials", declared_actual),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise SweQaError(f"{task_id} has invalid {label}")
        if expected_trials != actual_trials or declared_actual != actual_trials:
            raise SweQaError(f"{task_id} pair trial count does not match profiles")
        pair["expected_trials"] = expected_trials
        pair["actual_trials"] = actual_trials
        scope = compatible_usage_scope(
            [trial for profile in profiles.values() for trial in profile["trials"]]
        )
        if "usage_scope" in pair and pair["usage_scope"] != scope:
            raise SweQaError(f"{task_id} pair usage_scope disagrees with trials")
        pairs[task_id] = pair
    missing = [task for task in expected if task not in pairs]
    if missing:
        raise SweQaError(f"hard gate is missing valid pair(s): {', '.join(missing)}")
    compatible_usage_scope(
        [
            trial
            for pair in pairs.values()
            for profile in pair["profiles"].values()
            for trial in profile["trials"]
        ]
    )
    return pairs
