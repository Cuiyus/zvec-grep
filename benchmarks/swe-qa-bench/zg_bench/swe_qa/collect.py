"""Collect baseline/zvec-grep trials from completed Harbor 0.18 jobs."""

from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any

from zg_bench.core.errors import SweQaError
from zg_bench.core.io import load_object
from zg_bench.core.protocol import PROFILE_NAMES as PROFILES
from zg_bench.metrics.numbers import require_number, same_metric
from zg_bench.metrics.usage import (
    LEGACY_USAGE_SCOPE,
    SESSION_USAGE_SCOPE,
    compatible_usage_scope,
    validate_session_usage,
)


def _task_slug(task: str) -> str:
    return task.replace(":", "-")


def _task_names(result: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    for key in ("task_name", "task"):
        value = result.get(key)
        if isinstance(value, str) and value.strip():
            names.add(value.strip())
    task_id = result.get("task_id")
    if isinstance(task_id, dict):
        for key in ("name", "path"):
            value = task_id.get(key)
            if isinstance(value, str) and value.strip():
                names.add(value.strip())
                names.add(Path(value).name)
    return names


def _matches_task(result: dict[str, Any], task: str) -> bool:
    expected = {task, _task_slug(task)}
    return bool(_task_names(result) & expected)


def _job_dirs(runs_dir: Path, profile: str) -> list[Path]:
    suffix = f"-{profile}"
    candidates = [runs_dir] if runs_dir.name.endswith(suffix) else []
    candidates.extend(
        path
        for path in runs_dir.rglob("*")
        if path.is_dir() and path.name.endswith(suffix)
    )
    return sorted(
        {
            path.resolve()
            for path in candidates
            if (path / "result.json").is_file()
            and any(path.glob("*/agent/trajectory.json"))
        }
    )


def _completed_job(job_dir: Path, *, expected_trials: int) -> dict[str, Any]:
    result = load_object(job_dir / "result.json", label="Harbor job result")
    if not result.get("finished_at"):
        raise SweQaError(f"Harbor job did not finish: {job_dir.name}")
    total = result.get("n_total_trials")
    stats = result.get("stats")
    if not isinstance(total, int) or total < 1 or not isinstance(stats, dict):
        raise SweQaError(f"Harbor job result is incomplete: {job_dir.name}")
    if total != expected_trials:
        raise SweQaError(
            f"expected exactly {expected_trials} Harbor trial(s) in "
            f"{job_dir.name}, found {total}"
        )
    completed = stats.get("n_completed_trials")
    errors = stats.get("n_errored_trials")
    if completed != total or errors not in (0, None):
        raise SweQaError(f"Harbor job has incomplete or errored trials: {job_dir.name}")
    return result


def _select_trials(
    job_dir: Path, task: str, *, expected_trials: int
) -> list[tuple[Path, dict[str, Any]]]:
    matches: list[tuple[Path, dict[str, Any]]] = []
    for trajectory_path in sorted(job_dir.glob("*/agent/trajectory.json")):
        trial_dir = trajectory_path.parent.parent
        result_path = trial_dir / "result.json"
        if not result_path.is_file():
            continue
        result = load_object(result_path, label="Harbor trial result")
        if _matches_task(result, task):
            matches.append((trial_dir, result))

    def execution_order(
        item: tuple[Path, dict[str, Any]],
    ) -> tuple[datetime, str]:
        trial_dir, result = item
        timing = result.get("agent_execution")
        if not isinstance(timing, dict):
            raise SweQaError(
                f"trial is missing agent execution timing: {trial_dir.name}"
            )
        started = _parse_time(
            timing.get("started_at"),
            label=f"trial agent start time for {trial_dir.name}",
        )
        trial_name = str(result.get("trial_name") or trial_dir.name)
        return started, trial_name

    matches.sort(key=execution_order)
    if len(matches) != expected_trials:
        raise SweQaError(
            f"expected exactly {expected_trials} completed trial(s) for {task!r} in "
            f"{job_dir.name}, found {len(matches)}"
        )
    return matches


def _parse_time(value: Any, *, label: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise SweQaError(f"{label} is missing")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise SweQaError(f"{label} is not a valid ISO-8601 timestamp") from error


def _message_text(message: Any) -> str:
    if isinstance(message, str):
        return message.strip()
    if isinstance(message, list):
        parts = [
            part.get("text", "")
            for part in message
            if isinstance(part, dict) and part.get("type") == "text"
        ]
        return "\n".join(str(part) for part in parts if str(part).strip()).strip()
    return ""


def _trajectory_metrics(trajectory: dict[str, Any]) -> dict[str, Any]:
    steps = trajectory.get("steps")
    if not isinstance(steps, list) or not steps:
        raise SweQaError("agent trajectory has no steps")
    agent_steps = [
        step
        for step in steps
        if isinstance(step, dict) and step.get("source") == "agent"
    ]
    if not agent_steps:
        raise SweQaError("agent trajectory has no agent steps")
    answer = _message_text(agent_steps[-1].get("message"))
    if not answer:
        raise SweQaError("agent trajectory has an empty final answer")

    tool_calls = 0
    for index, step in enumerate(agent_steps):
        calls = step.get("tool_calls")
        if calls is None:
            continue
        if not isinstance(calls, list) or any(
            not isinstance(call, dict) for call in calls
        ):
            raise SweQaError(f"agent trajectory step {index + 1} has invalid tools")
        tool_calls += len(calls)
    return {"answer": answer, "tool_calls": tool_calls}


def _session_usage_for_trial(
    trial_dir: Path, result: dict[str, Any], context: dict[str, Any]
) -> dict[str, Any] | None:
    configs = [result.get("config", {})]
    config_path = trial_dir / "config.json"
    if config_path.is_file():
        configs.append(load_object(config_path, label="Harbor trial config"))
    required = False
    for config in configs:
        if not isinstance(config, dict):
            continue
        agent = config.get("agent", {})
        kwargs = agent.get("kwargs", {}) if isinstance(agent, dict) else {}
        flag = kwargs.get("collect_session_usage") if isinstance(kwargs, dict) else None
        if flag is not None and not isinstance(flag, bool):
            raise SweQaError("collect_session_usage must be a boolean")
        required = required or flag is True
    metadata = context.get("metadata")
    if isinstance(metadata, dict) and metadata.get("session_usage") is not None:
        required = True
    path = trial_dir / "agent" / "session-usage.json"
    if not required and not path.exists():
        return None
    usage = validate_session_usage(
        load_object(path, label="required session usage"), require_identity=True
    )
    for context_key, usage_key in (
        ("n_input_tokens", "input_tokens"),
        ("n_output_tokens", "output_tokens"),
        ("cost_usd", "cost_usd"),
    ):
        if not same_metric(context.get(context_key), usage["total"][usage_key]):
            raise SweQaError(f"AgentContext {context_key} disagrees with session usage")
    cache_tokens = (
        usage["total"]["cache_read_tokens"] + usage["total"]["cache_write_tokens"]
    )
    if not same_metric(context.get("n_cache_tokens"), cache_tokens):
        raise SweQaError("AgentContext n_cache_tokens disagrees with session usage")
    require_number(
        usage.get("collection_wall_seconds"), label="session usage collection time"
    )
    return usage


def _profile_result(
    *,
    profile: str,
    job_dir: Path,
    trial_dir: Path,
    result: dict[str, Any],
    trial_index: int,
) -> dict[str, Any]:
    if result.get("exception_info") is not None:
        raise SweQaError(f"{profile} trial ended with an exception: {trial_dir.name}")
    if not result.get("finished_at"):
        raise SweQaError(f"{profile} trial did not finish: {trial_dir.name}")
    verifier = result.get("verifier_result")
    if not isinstance(verifier, dict):
        raise SweQaError(f"{profile} trial is missing verifier result")
    rewards = verifier.get("rewards")
    if not isinstance(rewards, dict) or not any(
        isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0
        for value in rewards.values()
    ):
        raise SweQaError(f"{profile} trial has no positive verifier reward")

    context = result.get("agent_result")
    if not isinstance(context, dict):
        raise SweQaError(f"{profile} trial is missing agent result")
    input_tokens = require_number(
        context.get("n_input_tokens"),
        label=f"{profile} input_tokens",
        integer=True,
        positive=True,
    )
    output_tokens = require_number(
        context.get("n_output_tokens"),
        label=f"{profile} output_tokens",
        integer=True,
        positive=True,
    )
    cost_usd = require_number(
        context.get("cost_usd"),
        label=f"{profile} cost_usd",
        allow_none=True,
    )

    timing = result.get("agent_execution")
    if not isinstance(timing, dict):
        raise SweQaError(f"{profile} trial is missing agent execution timing")
    started = _parse_time(timing.get("started_at"), label=f"{profile} agent start time")
    finished = _parse_time(
        timing.get("finished_at"), label=f"{profile} agent finish time"
    )
    try:
        wall_seconds = (finished - started).total_seconds()
    except TypeError as error:
        raise SweQaError(f"{profile} agent timestamps use mixed timezones") from error
    if not math.isfinite(wall_seconds) or wall_seconds <= 0:
        raise SweQaError(f"{profile} agent execution time must be positive")

    trajectory = load_object(
        trial_dir / "agent" / "trajectory.json", label="agent trajectory"
    )
    trajectory_values = _trajectory_metrics(trajectory)
    session_usage = _session_usage_for_trial(trial_dir, result, context)
    usage_metrics: dict[str, Any] = {"usage_scope": LEGACY_USAGE_SCOPE}
    if session_usage is not None:
        collection_seconds = session_usage["collection_wall_seconds"]
        wall_seconds -= collection_seconds
        if wall_seconds <= 0:
            raise SweQaError(
                "session usage collection time exceeds agent execution time"
            )
        usage_metrics = {
            **session_usage["total"],
            "usage_scope": SESSION_USAGE_SCOPE,
            "usage_collection_wall_seconds": collection_seconds,
            "session_usage": {
                key: session_usage[key]
                for key in (
                    "schema_version",
                    "scope",
                    "complete",
                    "root_session_id",
                    "root",
                    "descendants",
                    "total",
                    "collection_wall_seconds",
                )
            },
        }
    model_info = result.get("agent_info")
    model_name: str | None = None
    if isinstance(model_info, dict):
        raw_model = model_info.get("model_info")
        if isinstance(raw_model, dict) and isinstance(raw_model.get("name"), str):
            model_name = raw_model["name"]

    return {
        "trial_index": trial_index,
        "profile": profile,
        "job_name": job_dir.name,
        "trial_name": str(result.get("trial_name") or trial_dir.name),
        "model": model_name,
        "answer": trajectory_values["answer"],
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "tool_calls": trajectory_values["tool_calls"],
        "agent_wall_seconds": wall_seconds,
        "cost_usd": cost_usd,
        **usage_metrics,
    }


def collect_pair(
    *,
    runs_dir: Path,
    task: str,
    output: Path,
    expected_trials: int = 1,
) -> dict[str, Any]:
    """Collect and validate the expected trials for both task profiles."""
    task = task.strip()
    if not task:
        raise SweQaError("task must not be empty")
    if not runs_dir.is_dir():
        raise SweQaError(f"runs directory does not exist: {runs_dir}")
    if isinstance(expected_trials, bool) or not isinstance(expected_trials, int):
        raise SweQaError("expected_trials must be an integer")
    if expected_trials < 1:
        raise SweQaError("expected_trials must be positive")

    profiles: dict[str, Any] = {}
    for profile in PROFILES:
        job_dirs = _job_dirs(runs_dir, profile)
        matching_jobs: list[tuple[Path, list[tuple[Path, dict[str, Any]]]]] = []
        errors: list[SweQaError] = []
        for job_dir in job_dirs:
            try:
                _completed_job(job_dir, expected_trials=expected_trials)
                trials = _select_trials(job_dir, task, expected_trials=expected_trials)
            except SweQaError as error:
                errors.append(error)
                continue
            matching_jobs.append((job_dir, trials))
        if len(matching_jobs) != 1:
            if not matching_jobs and len(job_dirs) == 1 and errors:
                raise errors[0]
            raise SweQaError(
                f"expected exactly one *-{profile} Harbor job for {task!r}, "
                f"found {len(matching_jobs)}"
            )
        job_dir, trials = matching_jobs[0]
        trial_results = [
            _profile_result(
                profile=profile,
                job_dir=job_dir,
                trial_dir=trial_dir,
                result=result,
                trial_index=index,
            )
            for index, (trial_dir, result) in enumerate(trials, start=1)
        ]
        profiles[profile] = {
            "profile": profile,
            "job_name": job_dir.name,
            "trial_count": len(trial_results),
            "trials": trial_results,
        }

    usage_scope = compatible_usage_scope(
        [trial for profile in profiles.values() for trial in profile["trials"]]
    )
    pair = {
        "schema_version": 2,
        "task_id": task,
        "task_slug": _task_slug(task),
        "valid": True,
        "expected_trials": expected_trials,
        "actual_trials": expected_trials,
        "profiles": profiles,
        "usage_scope": usage_scope,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(pair, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return pair
