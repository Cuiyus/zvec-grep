"""Offline, full-denominator summaries for the frozen Node/Rust experiment."""

from __future__ import annotations

from typing import Any

from zg_bench.core.errors import SweQaError
from zg_bench.core.protocol import PROFILE_NAMES
from zg_bench.metrics.summary import aggregate_cases
from zg_bench.reports.render import metric_cell


def include_all_cases(report: dict[str, Any]) -> dict[str, Any]:
    """Retain legacy sensitivity results, but never select by observed outcomes."""
    cases = report["cases"]
    if len({case["task_id"] for case in cases}) != len(cases):
        raise SweQaError("duplicate tasks in version report")
    for case in cases:
        for profile in PROFILE_NAMES:
            if len(case["profiles"][profile]["trials"]) != 5:
                raise SweQaError("version summary requires five trials per profile")
    return {
        **report,
        "legacy_filtered_aggregate": report.get("legacy_filtered_aggregate", report["aggregate"]),
        "aggregate": aggregate_cases(cases, filter_outcomes=False),
        "aggregation_policy": "all_completed_tasks_without_outcome_filtering",
    }


def render_version_report(report: dict[str, Any]) -> str:
    gate = report["gate"]
    lines = [
        "# Node 0.2.2 versus Rust main E2e",
        "",
        f"Model: {report['model']}; embedding: {report['embedding']}; five trials per version and task.",
        "",
        f"Completed tasks: {len(report['cases'])}/{len(gate['expected_tasks'])}. "
        f"Missing tasks: {', '.join(gate['missing_tasks']) or 'none'}.",
        "",
        "Every cell is **Node / Rust / Rust − Node**. Quality changes are score points; resource changes are percentages. "
        "All completed tasks contribute, including large improvements and regressions. "
        "The legacy outcome-filtered aggregate is retained separately in JSON as a sensitivity analysis.",
        "",
        "| Task | Judge (0–100) | Input tokens | Tool calls | Agent time (s) |",
        "|---|---:|---:|---:|---:|",
    ]

    def row(label: str, node: dict, rust: dict, comparison: dict) -> str:
        cells = [label, f"{node['judge']:.2f} / {rust['judge']:.2f} / {comparison['judge_delta']:+.2f}"]
        for metric, change in (("input_tokens", "input_token_reduction_pct"),
                               ("tool_calls", "toolcall_reduction_pct"),
                               ("agent_wall_seconds", "time_reduction_pct")):
            cells.append(metric_cell(node[metric], rust[metric], comparison[change]))
        return "| " + " | ".join(cells) + " |"

    aggregate = report["aggregate"]
    lines.append(row("**Aggregate**", aggregate["profiles"]["baseline"],
                     aggregate["profiles"]["zvec-grep"], aggregate["comparison"]))
    for case in report["cases"]:
        profiles = {name: {**p["metrics"], "judge": p["judge"]["total"]}
                    for name, p in case["profiles"].items()}
        lines.append(row(case["task_id"], profiles["baseline"], profiles["zvec-grep"], case["comparison"]))
    lines.extend(["", "Task values are means of five trials. Aggregate quality gives each task equal weight; "
                  "aggregate resources sum task means. Input tokens include cached input across the complete session tree. "
                  "Setup/index time and judge usage are reported separately. Missing tasks receive no invented zero values.", ""])
    return "\n".join(lines)
