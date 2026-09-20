"""Shared SWE-QA evidence fixtures; this module defines no discovered tests."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

from zg_bench.metrics.summary import aggregate_cases
from zg_bench.metrics.usage import SESSION_USAGE_METRICS, SESSION_USAGE_SCOPE
from zg_bench.swe_qa import SELF_JUDGE_LABEL

SWE_QA_BENCH_DIR = Path(__file__).resolve().parents[1]


SELECTION_PATH = SWE_QA_BENCH_DIR / "zg_bench" / "swe_qa" / "data" / "selection.json"


REFERENCES_PATH = SWE_QA_BENCH_DIR / "zg_bench" / "swe_qa" / "data" / "references.json"


DATASET_PATH = SWE_QA_BENCH_DIR / "datasets"


EXPECTED_TASK_IDS = (
    "reflex:6",
    "sqlfluff:2",
    "conan:1",
    "pylint:10",
    "pylint:9",
    "sympy:38",
    "conan:39",
    "xarray:46",
    "astropy:38",
    "matplotlib:37",
    "streamlink:14",
    "conan:19",
    "django:21",
    "pylint:14",
    "requests:16",
    "django:32",
    "xarray:32",
    "streamlink:43",
    "sympy:26",
    "conan:27",
)


JUDGE_GENERATION_METADATA = {
    "enable_thinking": True,
    "reasoning_effort": "high",
    "max_tokens": 32000,
    "response_format": None,
}


_summary_environment = patch.dict("os.environ", {"GITHUB_STEP_SUMMARY": ""})


def setUpModule() -> None:
    # Direct report-library calls must not append fixture tables to the real
    # Validate job summary. Tests that exercise summary output set a temp path.
    _summary_environment.start()


def tearDownModule() -> None:
    _summary_environment.stop()


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _judged_task_report(task_id: str, index: int = 0) -> dict[str, Any]:
    scale = index + 1
    baseline_score = 10 + index % 5
    zvec_score = 12 + index % 5
    category = ("what", "where", "how", "why")[min(index // 5, 3)]
    baseline_total = baseline_score * 5
    zvec_total = zvec_score * 5

    score_keys = (
        "correctness",
        "completeness",
        "relevance",
        "clarity",
        "coherence",
    )

    def profile_result(
        *, profile: str, score: int, metrics: dict[str, int | float]
    ) -> dict[str, Any]:
        scores = {key: score for key in score_keys}
        trials = [
            {
                "trial_index": trial_index,
                "trial_name": (f"{task_id.replace(':', '-')}-{profile}-{trial_index}"),
                "judge": {
                    "label": SELF_JUDGE_LABEL,
                    "model": "glm-5.2",
                    "scores": scores,
                    "total": score * 5,
                    "latency_seconds": 1.0,
                    "usage": {
                        "input_tokens": 50 * scale,
                        "output_tokens": 5 * scale,
                        "cost_usd": 0.01 * scale,
                    },
                },
                "metrics": dict(metrics),
            }
            for trial_index in range(1, 4)
        ]
        return {
            "trial_count": 3,
            "judge": {
                "label": SELF_JUDGE_LABEL,
                "model": "glm-5.2",
                "scores": {key: float(value) for key, value in scores.items()},
                "total": float(score * 5),
                "latency_seconds": 1.0,
                "usage": {
                    "calls": 3,
                    "input_tokens": 150 * scale,
                    "output_tokens": 15 * scale,
                    "cost_usd": 0.03 * scale,
                },
            },
            "metrics": {key: float(value) for key, value in metrics.items()},
            "trials": trials,
        }

    baseline_metrics = {
        "input_tokens": 100 * scale,
        "output_tokens": 20 * scale,
        "tool_calls": 10 * scale,
        "agent_wall_seconds": 20.0 * scale,
        "cost_usd": 0.2 * scale,
    }
    zvec_metrics = {
        "input_tokens": 50 * scale,
        "output_tokens": 15 * scale,
        "tool_calls": 4 * scale,
        "agent_wall_seconds": 10.0 * scale,
        "cost_usd": 0.1 * scale,
    }
    trial_comparison = {
        "judge_delta": zvec_total - baseline_total,
        "input_token_reduction_pct": 50.0,
        "toolcall_reduction_pct": 60.0,
        "time_reduction_pct": 50.0,
        "cost_reduction_pct": 50.0,
    }
    case = {
        "task_id": task_id,
        "role": "smoke" if index == 0 else "category",
        "category": category,
        "trial_count": 3,
        "profiles": {
            "baseline": profile_result(
                profile="baseline",
                score=baseline_score,
                metrics=baseline_metrics,
            ),
            "zvec-grep": profile_result(
                profile="zvec-grep", score=zvec_score, metrics=zvec_metrics
            ),
        },
        "comparison": {
            **trial_comparison,
            "trials": [
                {"trial_index": trial_index, **trial_comparison}
                for trial_index in range(1, 4)
            ],
        },
    }
    return {
        "schema_version": 2,
        "benchmark": "peng-weihan/SWE-QA-Bench",
        "judge": {
            "label": SELF_JUDGE_LABEL,
            "model": "glm-5.2",
            "self_judge": True,
            "temperature": 0,
            "rubric": [
                "correctness",
                "completeness",
                "relevance",
                "clarity",
                "coherence",
            ],
            "usage": {
                "calls": 6,
                "input_tokens": 300 * scale,
                "output_tokens": 30 * scale,
                "cost_usd": 0.06 * scale,
            },
        },
        "gate": {
            "kind": "completion-only",
            "report_only": True,
            "numeric_thresholds": False,
            "expected_tasks": [task_id],
            "valid_pairs": 1,
            "successful_judgements": 6,
            "passed": True,
        },
        "cases": [case],
        "aggregate": aggregate_cases([case]),
    }


def _set_report_trial_metrics(
    report: dict[str, Any],
    *,
    baseline: list[tuple[int, int, float, float | None]],
    zvec: list[tuple[int, int, float, float | None]],
) -> None:
    case = report["cases"][0]
    for profile_name, rows in (("baseline", baseline), ("zvec-grep", zvec)):
        profile = case["profiles"][profile_name]
        for trial, (input_tokens, tool_calls, wall_seconds, cost_usd) in zip(
            profile["trials"], rows, strict=True
        ):
            trial["metrics"].update(
                {
                    "input_tokens": input_tokens,
                    "tool_calls": tool_calls,
                    "agent_wall_seconds": wall_seconds,
                    "cost_usd": cost_usd,
                }
            )
        profile["metrics"].update(
            {
                "input_tokens": sum(row[0] for row in rows) / len(rows),
                "tool_calls": sum(row[1] for row in rows) / len(rows),
                "agent_wall_seconds": sum(row[2] for row in rows) / len(rows),
                "cost_usd": (
                    None
                    if any(row[3] is None for row in rows)
                    else sum(float(row[3]) for row in rows if row[3] is not None)
                    / len(rows)
                ),
            }
        )

    baseline_summary = case["profiles"]["baseline"]
    zvec_summary = case["profiles"]["zvec-grep"]

    def reduction(key: str) -> float | None:
        baseline_value = baseline_summary["metrics"][key]
        zvec_value = zvec_summary["metrics"][key]
        if baseline_value is None or zvec_value is None or baseline_value == 0:
            return None
        return (baseline_value - zvec_value) / baseline_value * 100

    # Keep the serialized task summary aligned with its displayed profile means.
    # Individual tests may overwrite it to simulate a stale source artifact.
    case["comparison"] = {
        "judge_delta": (
            zvec_summary["judge"]["total"] - baseline_summary["judge"]["total"]
        ),
        "input_token_reduction_pct": reduction("input_tokens"),
        "toolcall_reduction_pct": reduction("tool_calls"),
        "time_reduction_pct": reduction("agent_wall_seconds"),
        "cost_reduction_pct": reduction("cost_usd"),
    }


def _set_report_trial_judgements(
    report: dict[str, Any], *, baseline: list[int], zvec: list[int]
) -> None:
    """Set complete, consistent Judge evidence with an arbitrary trial count."""
    assert len(baseline) == len(zvec)
    case = report["cases"][0]
    count = len(baseline)
    case["trial_count"] = count
    for profile_name, totals in (("baseline", baseline), ("zvec-grep", zvec)):
        profile = case["profiles"][profile_name]
        source_trials = profile["trials"]
        trials = []
        for index, total in enumerate(totals, start=1):
            trial = copy.deepcopy(source_trials[(index - 1) % len(source_trials)])
            trial["trial_index"] = index
            trial["trial_name"] = f"{case['task_id']}-{profile_name}-{index}"
            quotient, remainder = divmod(total, 5)
            trial["judge"]["scores"] = {
                key: quotient + (position < remainder)
                for position, key in enumerate(trial["judge"]["scores"])
            }
            trial["judge"]["total"] = total
            trials.append(trial)
        profile["trials"] = trials
        profile["trial_count"] = count
        profile["judge"]["total"] = sum(totals) / count
        profile["judge"]["scores"] = {
            key: sum(trial["judge"]["scores"][key] for trial in trials) / count
            for key in profile["judge"]["scores"]
        }
        profile["judge"]["usage"] = {
            "calls": count,
            **{
                key: sum(trial["judge"]["usage"][key] for trial in trials)
                for key in ("input_tokens", "output_tokens", "cost_usd")
            },
        }
    report["gate"]["successful_judgements"] = count * 2
    report["judge"]["usage"] = {
        key: sum(
            profile["judge"]["usage"][key] for profile in case["profiles"].values()
        )
        for key in ("calls", "input_tokens", "output_tokens", "cost_usd")
    }
    comparison = case["comparison"]
    comparison["judge_delta"] = sum(zvec) / count - sum(baseline) / count
    comparison["trials"] = [
        {
            "trial_index": index,
            **{key: value for key, value in comparison.items() if key != "trials"},
            "judge_delta": zvec_score - baseline_score,
        }
        for index, (baseline_score, zvec_score) in enumerate(
            zip(baseline, zvec, strict=True), start=1
        )
    ]
    report["aggregate"] = aggregate_cases([case])


def _write_harbor_job(
    root: Path,
    *,
    profile: str,
    trials: list[dict[str, Any]],
) -> None:
    job_dir = root / f"fixture-reflex-6-{profile}"
    _write_json(
        job_dir / "result.json",
        {
            "finished_at": "2026-08-11T10:01:00+00:00",
            "n_total_trials": len(trials),
            "stats": {
                "n_completed_trials": len(trials),
                "n_errored_trials": 0,
            },
        },
    )
    for trial_position, trial in enumerate(trials, start=1):
        trial_dir = job_dir / str(trial["trial_name"])
        wall_seconds = int(trial["agent_wall_seconds"])
        started_second = trial_position
        finished_second = started_second + wall_seconds
        _write_json(
            trial_dir / "result.json",
            {
                "task_name": "reflex-6",
                "task_id": {"path": "/dataset/reflex-6"},
                "trial_name": trial_dir.name,
                "finished_at": f"2026-08-11T10:00:{wall_seconds:02d}+00:00",
                "exception_info": None,
                "agent_info": {
                    "name": "opencode",
                    "model_info": {"name": "custom-openai/glm-5.2"},
                },
                "agent_result": {
                    "n_input_tokens": trial["input_tokens"],
                    "n_output_tokens": trial["output_tokens"],
                    "cost_usd": trial["cost_usd"],
                },
                "verifier_result": {"rewards": {"reward": 1}},
                "agent_execution": {
                    "started_at": (f"2026-08-11T10:00:{started_second:02d}+00:00"),
                    "finished_at": (f"2026-08-11T10:00:{finished_second:02d}+00:00"),
                },
            },
        )
        calls = [
            {
                "tool_call_id": f"call-{index}",
                "function_name": "bash",
                "arguments": {"command": "true"},
            }
            for index in range(trial["tool_calls"])
        ]
        _write_json(
            trial_dir / "agent" / "trajectory.json",
            {
                "schema_version": "ATIF-v1.7",
                "agent": {
                    "name": "opencode",
                    "version": "1.18.4",
                    "model_name": "custom-openai/glm-5.2",
                },
                "steps": [
                    {"step_id": 1, "source": "user", "message": "question"},
                    {
                        "step_id": 2,
                        "source": "agent",
                        "message": trial["answer"],
                        "tool_calls": calls,
                    },
                ],
            },
        )


def _harbor_trial(
    trial_name: str,
    *,
    answer: str,
    input_tokens: int,
    output_tokens: int,
    tool_calls: int,
    agent_wall_seconds: int,
    cost_usd: float | None,
) -> dict[str, Any]:
    return {
        "trial_name": trial_name,
        "answer": answer,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "tool_calls": tool_calls,
        "agent_wall_seconds": agent_wall_seconds,
        "cost_usd": cost_usd,
    }


def _session_usage_fixture() -> dict[str, Any]:
    def metric(
        uncached: int,
        cache_read: int,
        cache_write: int,
        text: int,
        reasoning: int,
        tools: int,
        calls: int,
        cost: float,
    ) -> dict[str, int | float]:
        return {
            "input_tokens": uncached + cache_read + cache_write,
            "output_tokens": text + reasoning,
            "uncached_input_tokens": uncached,
            "cache_read_tokens": cache_read,
            "cache_write_tokens": cache_write,
            "text_output_tokens": text,
            "reasoning_tokens": reasoning,
            "tool_calls": tools,
            "llm_calls": calls,
            "cost_usd": cost,
        }

    root = metric(75, 20, 5, 16, 4, 1, 2, 0.1)
    child = metric(100, 100, 0, 25, 15, 3, 2, 0.2)
    grandchild = metric(90, 200, 10, 40, 20, 4, 3, 0.3)
    descendants = {key: child[key] + grandchild[key] for key in SESSION_USAGE_METRICS}
    total = {key: root[key] + descendants[key] for key in SESSION_USAGE_METRICS}
    return {
        "schema_version": 1,
        "scope": SESSION_USAGE_SCOPE,
        "complete": True,
        "errors": [],
        "root_session_id": "session-root",
        "root": root,
        "descendants": descendants,
        "total": total,
        "collection_wall_seconds": 2.0,
        "sessions": [
            {"session_id": "session-root", "parent_session_id": None, **root},
            {
                "session_id": "session-child",
                "parent_session_id": "session-root",
                **child,
            },
            {
                "session_id": "session-grandchild",
                "parent_session_id": "session-child",
                **grandchild,
            },
        ],
    }
