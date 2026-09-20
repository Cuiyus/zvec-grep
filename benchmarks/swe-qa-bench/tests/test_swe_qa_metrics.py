"""Profile comparison and displayed aggregate metric contracts."""

from __future__ import annotations

import unittest
from typing import Any

from swe_qa_fixtures import (
    setUpModule as setUpModule,
)
from swe_qa_fixtures import (
    tearDownModule as tearDownModule,
)
from zg_bench.metrics.summary import aggregate_cases
from zg_bench.reports.render import metric_cell


class ReportMetricsTests(unittest.TestCase):
    def test_efficiency_change_displays_savings_as_negative(self) -> None:
        self.assertEqual(
            metric_cell(100, 50, 50),
            "100.00 / 50.00 / -50.00%",
        )
        self.assertEqual(
            metric_cell(100, 125, -25),
            "100.00 / 125.00 / +25.00%",
        )
        self.assertEqual(
            metric_cell(100, 100, 0),
            "100.00 / 100.00 / +0.00%",
        )
        self.assertEqual(metric_cell(0, 1, None), "0.00 / 1.00 / N/A")

    def test_aggregate_compares_displayed_totals_not_mean_percentages(self) -> None:
        def case(
            *,
            baseline: dict[str, int | float],
            zvec: dict[str, int | float],
            judge_baseline: int,
            judge_zvec: int,
            reductions: dict[str, float | None],
        ) -> dict[str, Any]:
            return {
                "task_id": f"task:{judge_baseline}",
                "profiles": {
                    "baseline": {
                        "judge": {"total": judge_baseline},
                        "metrics": baseline,
                    },
                    "zvec-grep": {
                        "judge": {"total": judge_zvec},
                        "metrics": zvec,
                    },
                },
                "comparison": {
                    "judge_delta": judge_zvec - judge_baseline,
                    **reductions,
                },
            }

        cases = [
            case(
                baseline={
                    "input_tokens": 100,
                    "tool_calls": 10,
                    "agent_wall_seconds": 10.0,
                    "cost_usd": 1.0,
                },
                zvec={
                    "input_tokens": 10,
                    "tool_calls": 1,
                    "agent_wall_seconds": 1.0,
                    "cost_usd": 0.1,
                },
                judge_baseline=50,
                judge_zvec=60,
                reductions={
                    "input_token_reduction_pct": 90.0,
                    "toolcall_reduction_pct": 90.0,
                    "time_reduction_pct": 90.0,
                    "cost_reduction_pct": 90.0,
                },
            ),
            case(
                baseline={
                    "input_tokens": 900,
                    "tool_calls": 90,
                    "agent_wall_seconds": 90.0,
                    "cost_usd": 9.0,
                },
                zvec={
                    "input_tokens": 900,
                    "tool_calls": 90,
                    "agent_wall_seconds": 90.0,
                    "cost_usd": 9.0,
                },
                judge_baseline=80,
                judge_zvec=70,
                reductions={
                    "input_token_reduction_pct": 0.0,
                    "toolcall_reduction_pct": 0.0,
                    "time_reduction_pct": 0.0,
                    "cost_reduction_pct": 0.0,
                },
            ),
        ]

        aggregate = aggregate_cases(cases)

        self.assertEqual(aggregate["comparison"]["judge_delta"], 0.0)
        totals_ratio = (1000 - 910) / 1000 * 100
        for key in (
            "input_token_reduction_pct",
            "toolcall_reduction_pct",
            "time_reduction_pct",
            "cost_reduction_pct",
        ):
            self.assertAlmostEqual(aggregate["comparison"][key], totals_ratio)
        self.assertEqual(
            aggregate["comparison_basis"], "ratio_of_aggregate_profile_means"
        )

        cases[1]["profiles"]["baseline"]["metrics"]["cost_usd"] = None
        aggregate = aggregate_cases(cases)
        self.assertIsNone(aggregate["comparison"]["cost_reduction_pct"])
        self.assertIsNone(aggregate["profiles"]["baseline"]["cost_usd"])
        self.assertEqual(aggregate["comparison_samples"]["cost_reduction_pct"], 0)
        cases[0]["profiles"]["baseline"]["metrics"]["cost_usd"] = None
        aggregate = aggregate_cases(cases)
        self.assertIsNone(aggregate["comparison"]["cost_reduction_pct"])
        self.assertEqual(aggregate["comparison_samples"]["cost_reduction_pct"], 0)


if __name__ == "__main__":
    unittest.main()
