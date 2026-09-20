"""Session usage contracts and reductions, independent of agent implementations."""

from __future__ import annotations

from typing import Any, Sequence

from zg_bench.core.errors import SweQaError
from zg_bench.metrics.numbers import (
    mean_or_none,
    require_number,
    same_metric,
    sum_or_none,
)

LEGACY_USAGE_SCOPE = "legacy-root"


SESSION_USAGE_SCOPE = "opencode-session-tree-v1"


SESSION_USAGE_METRICS = (
    "input_tokens",
    "output_tokens",
    "text_output_tokens",
    "reasoning_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "uncached_input_tokens",
    "tool_calls",
    "llm_calls",
    "cost_usd",
)


def usage_scope(value: dict[str, Any]) -> str:
    scope = value.get("usage_scope", LEGACY_USAGE_SCOPE)
    if scope not in (LEGACY_USAGE_SCOPE, SESSION_USAGE_SCOPE):
        raise SweQaError(f"unsupported usage_scope: {scope!r}")
    return scope


def compatible_usage_scope(rows: list[dict[str, Any]]) -> str:
    scopes = {usage_scope(row) for row in rows}
    if len(scopes) != 1:
        raise SweQaError("cannot mix legacy-root and session-tree usage scopes")
    return next(iter(scopes))


def validate_session_usage(
    usage: Any, *, integer: bool = True, require_identity: bool = False
) -> dict[str, Any]:
    """Validate measured session-tree totals, including averaged report rows."""
    if (
        not isinstance(usage, dict)
        or usage.get("scope") != SESSION_USAGE_SCOPE
        or usage.get("complete") is not True
        or usage.get("errors", []) != []
    ):
        raise SweQaError("session usage is missing, incomplete, or has errors")
    if require_identity and (
        usage.get("schema_version") != 1
        or not isinstance(usage.get("root_session_id"), str)
        or not usage["root_session_id"].strip()
        or not isinstance(usage.get("sessions"), list)
        or not usage["sessions"]
    ):
        raise SweQaError("session usage has invalid session identity/evidence")
    for scope in ("root", "descendants", "total"):
        metrics = usage.get(scope)
        if not isinstance(metrics, dict):
            raise SweQaError(f"session usage has no {scope} metrics")
        for key in SESSION_USAGE_METRICS:
            if key not in metrics:
                raise SweQaError(f"session usage {scope} is missing {key}")
            require_number(
                metrics[key],
                label=f"session usage {scope}.{key}",
                integer=integer and key != "cost_usd",
                allow_none=key == "cost_usd",
            )
        if not same_metric(
            metrics["input_tokens"],
            sum(
                metrics[key]
                for key in (
                    "uncached_input_tokens",
                    "cache_read_tokens",
                    "cache_write_tokens",
                )
            ),
        ):
            raise SweQaError(f"session usage {scope} input token components disagree")
        if not same_metric(
            metrics["output_tokens"],
            metrics["text_output_tokens"] + metrics["reasoning_tokens"],
        ):
            raise SweQaError(f"session usage {scope} output token components disagree")
    for key in SESSION_USAGE_METRICS:
        parts = [usage[scope][key] for scope in ("root", "descendants")]
        expected = None if any(value is None for value in parts) else sum(parts)
        if not same_metric(usage["total"][key], expected):
            raise SweQaError(f"session usage total.{key} disagrees with session split")
    return usage


def validate_usage_metrics(metrics: dict[str, Any], *, integer: bool) -> str:
    scope = usage_scope(metrics)
    if scope == SESSION_USAGE_SCOPE:
        usage = validate_session_usage(metrics.get("session_usage"), integer=integer)
        for key in SESSION_USAGE_METRICS:
            require_number(
                metrics.get(key),
                label=f"reported {key}",
                integer=integer and key != "cost_usd",
                allow_none=key == "cost_usd",
            )
            if key not in metrics or not same_metric(metrics[key], usage["total"][key]):
                raise SweQaError(f"reported {key} disagrees with session usage total")
    elif metrics.get("session_usage") is not None:
        raise SweQaError("legacy-root metrics cannot contain session-tree usage")
    return scope


def summarize_usage(rows: Sequence[dict[str, Any]], *, average: bool) -> dict[str, Any]:
    scope = compatible_usage_scope(list(rows))
    result: dict[str, Any] = {"usage_scope": scope}
    if scope == LEGACY_USAGE_SCOPE:
        return result
    combine = mean_or_none if average else sum_or_none
    split = {
        part: {
            key: combine([row["session_usage"][part][key] for row in rows])
            for key in SESSION_USAGE_METRICS
        }
        for part in ("root", "descendants", "total")
    }
    result.update(split["total"])
    result["session_usage"] = {
        "scope": scope,
        "complete": True,
        **split,
    }
    return result
