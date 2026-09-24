"""Render and write SWE-QA reports without calling a model."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from zg_bench.core.errors import SweQaError
from zg_bench.metrics.usage import (
    LEGACY_USAGE_SCOPE,
    SESSION_USAGE_SCOPE,
)


def _fmt_number(value: int | float, *, decimals: int = 2) -> str:
    return f"{float(value):,.{decimals}f}"


def _fmt_delta(value: float | int | None, *, suffix: str = "") -> str:
    if value is None:
        return "N/A"
    numeric = float(value)
    if round(numeric, 2) == 0:
        numeric = 0.0
    return f"{numeric:+.2f}{suffix}"


def metric_cell(
    baseline: int | float | None,
    zvec: int | float | None,
    reduction: float | None,
    *,
    decimals: int = 2,
) -> str:
    if baseline is None or zvec is None:
        return "N/A"
    left = _fmt_number(baseline, decimals=decimals)
    right = _fmt_number(zvec, decimals=decimals)
    # The stored comparison remains a reduction: positive means zvec-grep used
    # less. In the table, present every third value as zvec-grep minus baseline,
    # so efficiency wins are negative while Judge improvements stay positive.
    change = None if reduction is None else -reduction
    return f"{left} / {right} / {_fmt_delta(change, suffix='%')}"


def render_report(report: dict[str, Any]) -> str:
    usage_scope = report.get("usage_scope", LEGACY_USAGE_SCOPE)
    aggregate = report["aggregate"]
    filtering = aggregate.get("filter", {})
    included = set(
        filtering.get(
            "included_task_ids", [case["task_id"] for case in report["cases"]]
        )
    )

    def table_row(
        label: str,
        baseline: dict[str, Any],
        zvec: dict[str, Any],
        judge_b: float | None,
        judge_z: float | None,
        comparison: dict[str, Any],
    ) -> str:
        judge_cell = (
            "N/A"
            if judge_b is None or judge_z is None
            else f"{judge_b:.2f} / {judge_z:.2f} / {_fmt_delta(comparison['judge_delta'])}"
        )
        cells = [label, judge_cell]
        for metric, change in (
            ("input_tokens", "input_token_reduction_pct"),
            ("tool_calls", "toolcall_reduction_pct"),
            ("agent_wall_seconds", "time_reduction_pct"),
        ):
            cells.append(
                metric_cell(baseline[metric], zvec[metric], comparison[change])
            )
        return "| " + " | ".join(cells) + " |"

    baseline = aggregate["profiles"]["baseline"]
    zvec = aggregate["profiles"]["zvec-grep"]
    lines = [
        "# SWE-QA-Bench CI report",
        "",
    ]
    missing_tasks = report.get("gate", {}).get("missing_tasks", [])
    if missing_tasks:
        lines.extend(
            (
                f"**Incomplete run:** aggregated {len(report['cases'])} completed task(s); "
                f"{len(missing_tasks)} expected task(s) did not produce reports: "
                + ", ".join(f"`{task}`" for task in missing_tasks)
                + ". Missing tasks are not assigned zero values and are excluded from every Aggregate metric.",
                "",
            )
        )
    lines.extend(
        (
            "All cells use `baseline / zvec-grep / change`. Resource savings are negative; Judge gains are positive.",
            "",
            "| Case | Judge self-judge | input_token | toolcall | time (s) |",
            "|---|---:|---:|---:|---:|",
            table_row(
                "**Aggregate**",
                baseline,
                zvec,
                baseline["judge"],
                zvec["judge"],
                aggregate["comparison"],
            ),
        )
    )
    for case in report["cases"]:
        if case["task_id"] not in included:
            continue
        baseline = case["profiles"]["baseline"]
        zvec = case["profiles"]["zvec-grep"]
        lines.append(
            table_row(
                str(case["task_id"]),
                baseline["metrics"],
                zvec["metrics"],
                baseline["judge"]["total"],
                zvec["judge"]["total"],
                case["comparison"],
            )
        )
    lines.append("")
    if filtering:
        lines.extend(
            (
                f"Aggregate includes **{filtering['included_count']}/{filtering['total_count']} tasks**. "
                "A task is excluded from every Aggregate metric and the table above if either its input-token change "
                "`(zvec-grep mean - baseline mean) / baseline mean` is strictly outside **[-100%, +100%]**, "
                "or its Judge difference `zvec-grep mean - baseline mean` is strictly outside **[-10, +10] score points**. "
                "Exactly +/-10 Judge points and +/-100% input-token changes are retained. "
                "Both filters use each profile's trial mean and are applied independently to each workflow run; "
                "tasks matching both are excluded only once. "
                "All tasks are still executed, judged, and retained in the JSON evidence.",
                "",
            )
        )
        excluded = filtering.get("excluded_tasks", [])
        judge_excluded = [
            row
            for row in excluded
            if "judge_delta_outside_range" in row.get("reasons", [])
        ]
        if judge_excluded:
            lines.extend(
                (
                    "### Tasks excluded for Judge differences",
                    "",
                    "These are differences between the two profiles' mean scores, in points, not percentages. "
                    "Positive values favor zvec-grep; negative values favor baseline. "
                    "Every task below is excluded from all Aggregate metrics, including resource totals.",
                    "",
                    "| Task | Baseline Judge | zvec-grep Judge | Judge change (points) | Exclusion reason |",
                    "|---|---:|---:|---:|---|",
                )
            )
            for row in judge_excluded:
                reason = (
                    "Judge gain exceeds +10 points"
                    if row["judge_delta"] > 0
                    else "Judge decline exceeds -10 points"
                )
                if "undefined_baseline" in row["reasons"]:
                    reason += "; input baseline is zero while zvec-grep is positive"
                elif "input_token_change_outside_range" in row["reasons"]:
                    reason += f"; input change {_fmt_delta(row['change_pct'], suffix='%')} is outside [-100%, +100%]"
                lines.append(
                    f"| {row['task_id']} | {row['baseline_judge']:.2f} | {row['zvec_grep_judge']:.2f} "
                    f"| {_fmt_delta(row['judge_delta'])} | {reason} |"
                )
            lines.append("")
        elif any(
            rule.get("metric") == "judge" for rule in filtering.get("criteria", [])
        ):
            lines.extend(
                (
                    "No tasks were excluded for Judge differences outside [-10, +10] points.",
                    "",
                )
            )
        input_excluded = [
            row
            for row in excluded
            if set(row.get("reasons", [row.get("reason")]))
            & {"undefined_baseline", "input_token_change_outside_range"}
        ]
        if input_excluded:
            descriptions = []
            for row in input_excluded:
                change = row["change_pct"]
                descriptions.append(
                    f"`{row['task_id']}` (input "
                    + (
                        "N/A: baseline is zero, zvec-grep is positive"
                        if change is None
                        else _fmt_delta(change, suffix="%")
                    )
                    + ")"
                )
            lines.extend(
                (
                    "Tasks excluded for input-token changes: "
                    + "; ".join(descriptions)
                    + ".",
                    "",
                )
            )
        if not included:
            lines.extend(
                (
                    "No tasks remain after filtering; Aggregate is N/A. This does not invalidate completed trials.",
                    "",
                )
            )

    completion_statement = (
        "The completion gate failed because one or more expected tasks did not produce a valid judged pair. "
        "The displayed Aggregate covers completed tasks only."
        if missing_tasks
        else "The hard gate requires every expected pair and every judge call to succeed, including excluded tasks."
    )
    lines.extend(
        (
            "Each task's baseline and zvec-grep values are arithmetic means across its trials. "
            "Aggregate resource values are sums of the included task means; Judge values are equal-weight means across included tasks. "
            "Every Aggregate change is calculated directly from the displayed Aggregate values, not an average of task percentages. "
            "A zero Aggregate baseline denominator produces N/A; zero-baseline tasks in other metrics still contribute to the sums. "
            "For the input filter, two zero means are retained, while a zero baseline with positive zvec-grep is excluded.",
            "",
            "Nonnegative input tokens cannot decrease by more than 100%; this threshold therefore removes high-overhead tasks. "
            "Filtered statistics are a sensitivity analysis and do not imply the excluded results are invalid.",
            "",
        )
    )
    samples = aggregate.get("comparison_samples")
    if isinstance(samples, dict):
        task_count = len(included)
        lines.extend(
            (
                "Aggregate comparison sample counts: "
                f"Judge n={samples.get('judge_delta', 0)}/{task_count}, "
                f"input_token n={samples.get('input_token_reduction_pct', 0)}/{task_count}, "
                f"toolcall n={samples.get('toolcall_reduction_pct', 0)}/{task_count}, "
                f"time n={samples.get('time_reduction_pct', 0)}/{task_count}.",
                "",
            )
        )
    judge_metadata = report["judge"]
    generation_description = (
        "Judge generation: "
        f"`temperature={judge_metadata['temperature']}`, "
        f"`seed={judge_metadata.get('seed', 'unrecorded')}`, "
        f"`enable_thinking={str(judge_metadata['enable_thinking']).lower()}`, "
        f"`reasoning_effort={judge_metadata['reasoning_effort']}`, "
        f"`max_tokens={judge_metadata['max_tokens']}`, "
        f"`response_format={(judge_metadata['response_format'] or {}).get('type', 'unset')}`."
        if "enable_thinking" in judge_metadata
        else "Legacy report: judge thinking, reasoning effort, and output limit were not recorded."
    )
    lines.extend(
        (
            f"Judge: **{judge_metadata['label']}** ({judge_metadata['model']} self-judge).",
            "",
            generation_description,
            "",
        )
    )
    if (
        judge_metadata["model"] == "qwen3.8-max"
        and judge_metadata.get("enable_thinking") is True
    ):
        lines.extend(
            (
                "Qwen thinking-mode parameter interpretation: the metadata above records requested values. "
                "The [provider documentation](https://help.aliyun.com/zh/model-studio/qwen-api-via-openai-chat-completions) "
                "specifies that temperatures below 0.6 are raised to 0.6 and `reasoning_effort=high` maps to `xhigh`; "
                "`max_tokens` limits the final answer and excludes reasoning tokens. "
                "These are documented provider behaviors, not effective values observed in the response.",
                "",
            )
        )
    lines.extend(
        (
            "This run is **report-only**. Numeric scores and the Aggregate filter are not code-review or merge gates. "
            + completion_statement,
            "",
            f"Usage scope: **`{usage_scope}`**. "
            + (
                "Tokens and tool calls include the root session and every recursively linked subagent session. "
                "Input includes uncached, cache-read, and cache-write tokens; output includes text and reasoning tokens. "
                "Per-session detail remains in JSON evidence. "
                "Agent wall time already includes awaited subagents and excludes usage-export overhead; child durations are not added. "
                "The judge evaluates only the root final answer. Background title/summary calls not persisted in the session database are outside this scope, so this is not a complete provider bill."
                if usage_scope == SESSION_USAGE_SCOPE
                else "Legacy evidence covers the root trajectory only; subagent usage is unknown and may be omitted. "
                "Do not interpret these resource comparisons as complete agent usage or combine them with session-tree reports."
            ),
            "",
        )
    )
    return "\n".join(lines)


def write_report(report: dict[str, Any], output_dir: Path) -> None:
    markdown = render_report(report)
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        (output_dir / "report.md").write_text(markdown, encoding="utf-8")
    except OSError as error:
        raise SweQaError(f"could not write SWE-QA report: {error}") from error

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY", "").strip()
    if summary_path:
        try:
            with Path(summary_path).open("a", encoding="utf-8") as summary:
                summary.write(markdown)
                if not markdown.endswith("\n"):
                    summary.write("\n")
        except OSError as error:
            raise SweQaError(
                f"could not append GitHub step summary: {error}"
            ) from error
