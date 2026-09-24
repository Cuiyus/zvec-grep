"""Offline report identity, filtering and completion contracts."""

from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from swe_qa_fixtures import (
    EXPECTED_TASK_IDS,
    JUDGE_GENERATION_METADATA,
    _judged_task_report,
    _set_report_trial_judgements,
    _set_report_trial_metrics,
    _write_json,
)
from swe_qa_fixtures import (
    setUpModule as setUpModule,
)
from swe_qa_fixtures import (
    tearDownModule as tearDownModule,
)
from zg_bench.reports.aggregate import aggregate_reports
from zg_bench.settings import OPENCODE_QWEN_TEMPERATURE
from zg_bench.swe_qa import SELF_JUDGE_LABEL, SweQaError
from zg_bench.swe_qa.cli import main as swe_qa_main


class AggregateReportTests(unittest.TestCase):
    def test_offline_reports_do_not_import_model_or_agent_runtimes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            report_path = root / "source" / "report.json"
            _write_json(report_path, _judged_task_report("reflex:6"))
            script = textwrap.dedent(
                """
                import importlib.abc
                import json
                import sys
                from pathlib import Path

                class BlockModelRuntimes(importlib.abc.MetaPathFinder):
                    def find_spec(self, fullname, path=None, target=None):
                        for prefix in ("harbor", "litellm", "zg_bench.engines"):
                            if fullname == prefix or fullname.startswith(prefix + "."):
                                raise ImportError(f"offline report imported {fullname}")
                        return None

                sys.meta_path.insert(0, BlockModelRuntimes())
                from zg_bench.reports.aggregate import aggregate_reports
                from zg_bench.reports.render import render_report
                from zg_bench.reports.validation import validate_task_report

                source, output = map(Path, sys.argv[1:])
                report = json.loads(source.read_text())
                validate_task_report(report, source)
                assert "reflex:6" in render_report(report)
                result = aggregate_reports(
                    reports_root=source.parent,
                    output_dir=output,
                    expected=["reflex:6"],
                )
                assert result["gate"]["passed"]
                assert result["gate"]["valid_pairs"] == 1
                assert (output / "report.json").is_file()
                assert "reflex:6" in (output / "report.md").read_text()
                """
            )
            result = subprocess.run(
                [sys.executable, "-c", script, str(report_path), str(root / "output")],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_judge_filter_uses_trial_means_and_excludes_union_once(self) -> None:
        # Decimal means can subtract to just beyond 10 due to float rounding.
        # Both boundary directions remain included, while real 10.2-point
        # differences are excluded. A single divergent trial is insufficient.
        rows = [
            ("reflex:6", [54, 54, 54, 55, 55], [64, 64, 64, 65, 65], 100, 50),
            ("sqlfluff:2", [64, 64, 64, 65, 65], [54, 54, 54, 55, 55], 100, 50),
            ("conan:1", [60] * 5, [70, 70, 70, 70, 71], 100, 50),
            ("pylint:10", [70, 70, 70, 70, 71], [60] * 5, 100, 50),
            ("pylint:9", [60] * 5, [90, 55, 55, 55, 55], 100, 50),
            ("sympy:38", [60] * 5, [70, 70, 70, 70, 71], 100, 300),
            ("conan:39", [60] * 5, [60] * 5, 100, 300),
            ("xarray:46", [70, 70, 70, 70, 71], [60] * 5, 0, 1),
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for index, (
                task_id,
                baseline,
                zvec,
                input_baseline,
                input_zvec,
            ) in enumerate(rows):
                source = _judged_task_report(task_id, index)
                _set_report_trial_judgements(source, baseline=baseline, zvec=zvec)
                _set_report_trial_metrics(
                    source,
                    baseline=[(input_baseline, 10, 10.0, 1.0)] * 5,
                    zvec=[(input_zvec, 4, 5.0, 0.5)] * 5,
                )
                _write_json(root / "reports" / str(index) / "report.json", source)
            report = aggregate_reports(
                reports_root=root / "reports",
                output_dir=root / "combined",
                expected=[row[0] for row in rows],
            )
            aggregate = report["aggregate"]
            filtering = aggregate["filter"]
            self.assertEqual(filtering["total_count"], 8)
            self.assertEqual(filtering["included_count"], 3)
            self.assertEqual(filtering["excluded_count"], 5)
            self.assertEqual(
                filtering["included_task_ids"], ["reflex:6", "sqlfluff:2", "pylint:9"]
            )
            excluded = {row["task_id"]: row for row in filtering["excluded_tasks"]}
            self.assertEqual(
                set(excluded),
                {"conan:1", "pylint:10", "sympy:38", "conan:39", "xarray:46"},
            )
            for task_id in ("conan:1", "pylint:10"):
                self.assertEqual(
                    excluded[task_id]["reasons"], ["judge_delta_outside_range"]
                )
            self.assertAlmostEqual(excluded["conan:1"]["judge_delta"], 10.2)
            self.assertAlmostEqual(excluded["pylint:10"]["judge_delta"], -10.2)
            self.assertEqual(
                excluded["sympy:38"]["reasons"],
                [
                    "input_token_change_outside_range",
                    "judge_delta_outside_range",
                ],
            )
            self.assertEqual(
                excluded["conan:39"]["reasons"], ["input_token_change_outside_range"]
            )
            self.assertEqual(
                excluded["xarray:46"]["reasons"],
                [
                    "undefined_baseline",
                    "judge_delta_outside_range",
                ],
            )
            self.assertAlmostEqual(
                aggregate["profiles"]["baseline"]["judge"], (54.4 + 64.4 + 60) / 3
            )
            self.assertAlmostEqual(
                aggregate["profiles"]["zvec-grep"]["judge"], (64.4 + 54.4 + 62) / 3
            )
            self.assertEqual(aggregate["profiles"]["baseline"]["input_tokens"], 300)
            self.assertEqual(aggregate["profiles"]["zvec-grep"]["input_tokens"], 150)
            self.assertEqual(aggregate["profiles"]["baseline"]["tool_calls"], 30)
            self.assertEqual(aggregate["profiles"]["zvec-grep"]["tool_calls"], 12)
            self.assertEqual(
                aggregate["profiles"]["zvec-grep"]["agent_wall_seconds"], 15
            )
            self.assertEqual(aggregate["profiles"]["zvec-grep"]["cost_usd"], 1.5)
            self.assertEqual(report["gate"]["valid_pairs"], 8)
            self.assertEqual(report["gate"]["successful_judgements"], 80)
            self.assertEqual(report["judge"]["usage"]["calls"], 80)
            self.assertEqual(len(report["cases"]), 8)
            markdown = (root / "combined" / "report.md").read_text()
            main_table, excluded_section = markdown.split(
                "### Tasks excluded for Judge differences", 1
            )
            self.assertIn("| **Aggregate** |", main_table)
            self.assertLess(
                main_table.index("| **Aggregate** |"), main_table.index("| reflex:6 |")
            )
            for task_id in excluded:
                self.assertNotIn(f"| {task_id} |", main_table)
            self.assertIn(
                "| Task | Baseline Judge | zvec-grep Judge | Judge change (points) | Exclusion reason |",
                excluded_section,
            )
            self.assertIn("| conan:1 | 60.00 | 70.20 | +10.20 |", excluded_section)
            self.assertIn("| pylint:10 | 70.20 | 60.00 | -10.20 |", excluded_section)
            self.assertIn("| sympy:38 | 60.00 | 70.20 | +10.20 |", excluded_section)
            self.assertIn("| xarray:46 | 70.20 | 60.00 | -10.20 |", excluded_section)
            self.assertNotIn("| conan:39 |", excluded_section)

    def test_input_filter_uses_task_means_and_keeps_boundary_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            # +100% and -100% are retained. A zero/zero input pair is also
            # retained, while positive input against a zero baseline is not.
            rows = [
                ("reflex:6", [100, 100, 100], [200, 200, 200]),
                ("sqlfluff:2", [100, 100, 100], [201, 201, 201]),
                ("conan:1", [100, 100, 100], [0, 0, 0]),
                ("pylint:10", [0, 0, 0], [0, 0, 0]),
                ("pylint:9", [0, 0, 0], [1, 1, 1]),
                # One trial rises 400%, but the task mean rises only 66.67%.
                ("sympy:38", [100, 100, 100], [500, 0, 0]),
            ]
            for index, (task_id, baseline, zvec) in enumerate(rows):
                source = _judged_task_report(task_id, index)
                _set_report_trial_metrics(
                    source,
                    baseline=[(value, 10, 10.0, 1.0) for value in baseline],
                    zvec=[(value, 1000, 1000.0, 100.0) for value in zvec],
                )
                _write_json(root / "reports" / str(index) / "report.json", source)

            report = aggregate_reports(
                reports_root=root / "reports",
                output_dir=root / "combined",
                expected=[row[0] for row in rows],
            )
            aggregate = report["aggregate"]
            filtering = aggregate["filter"]
            self.assertEqual(
                filtering["criteria"],
                [
                    {
                        "metric": "input_tokens",
                        "comparison": "change_pct",
                        "min": -100.0,
                        "max": 100.0,
                    },
                    {
                        "metric": "judge",
                        "comparison": "delta",
                        "min": -10.0,
                        "max": 10.0,
                        "unit": "points",
                    },
                ],
            )
            self.assertEqual(filtering["total_count"], 6)
            self.assertEqual(filtering["included_count"], 4)
            self.assertEqual(filtering["excluded_count"], 2)
            included = ["reflex:6", "conan:1", "pylint:10", "sympy:38"]
            self.assertEqual(filtering["included_task_ids"], included)
            self.assertEqual(
                filtering["excluded_tasks"],
                [
                    {
                        "task_id": "sqlfluff:2",
                        "baseline_input_tokens": 100.0,
                        "zvec_grep_input_tokens": 201.0,
                        "change_pct": 101.0,
                        "baseline_judge": 55.0,
                        "zvec_grep_judge": 65.0,
                        "judge_delta": 10.0,
                        "reasons": ["input_token_change_outside_range"],
                    },
                    {
                        "task_id": "pylint:9",
                        "baseline_input_tokens": 0.0,
                        "zvec_grep_input_tokens": 1.0,
                        "change_pct": None,
                        "baseline_judge": 70.0,
                        "zvec_grep_judge": 80.0,
                        "judge_delta": 10.0,
                        "reasons": ["undefined_baseline"],
                    },
                ],
            )
            baseline = aggregate["profiles"]["baseline"]
            zvec = aggregate["profiles"]["zvec-grep"]
            self.assertEqual(baseline["input_tokens"], 300)
            self.assertAlmostEqual(zvec["input_tokens"], 200 + 500 / 3)
            self.assertEqual(baseline["judge"], (50 + 60 + 65 + 50) / 4)
            self.assertEqual(zvec["judge"], (60 + 70 + 75 + 60) / 4)
            self.assertEqual(baseline["tool_calls"], 40)
            self.assertEqual(zvec["tool_calls"], 4000)
            self.assertEqual(zvec["agent_wall_seconds"], 4000)
            self.assertEqual(zvec["cost_usd"], 400)
            self.assertAlmostEqual(
                aggregate["comparison"]["input_token_reduction_pct"], -200 / 9
            )
            self.assertEqual(report["gate"]["valid_pairs"], 6)
            self.assertEqual(report["gate"]["successful_judgements"], 36)
            self.assertEqual(report["judge"]["usage"]["calls"], 36)
            self.assertEqual(
                [case["task_id"] for case in report["cases"]], [row[0] for row in rows]
            )
            markdown = (root / "combined" / "report.md").read_text()
            for task_id in included:
                self.assertIn(f"| {task_id} |", markdown)
                self.assertLess(
                    markdown.index("| **Aggregate** |"),
                    markdown.index(f"| {task_id} |"),
                )
            for task_id in ("sqlfluff:2", "pylint:9"):
                self.assertNotIn(f"| {task_id} |", markdown)
                self.assertIn(task_id, markdown)

    def test_aggregate_accepts_qwen_and_rejects_mixed_or_inconsistent_identities(
        self,
    ) -> None:
        def qwen_report(task_id: str, index: int) -> dict[str, Any]:
            report = _judged_task_report(task_id, index)
            identities = [report["judge"]]
            for profile in report["cases"][0]["profiles"].values():
                identities.append(profile["judge"])
                identities.extend(trial["judge"] for trial in profile["trials"])
            for identity in identities:
                identity.update(model="qwen3.8-max", label="qwen3.8-max-self-judge-v1")
            report["judge"]["temperature"] = OPENCODE_QWEN_TEMPERATURE
            return report

        for mismatch in (
            None,
            "mixed_models",
            "label",
            "profile",
            "trial",
            "unknown",
            "temperature",
        ):
            with (
                self.subTest(mismatch=mismatch),
                tempfile.TemporaryDirectory() as temp_dir,
            ):
                root = Path(temp_dir)
                first = qwen_report("reflex:6", 0)
                second = qwen_report("sqlfluff:2", 1)
                if mismatch == "mixed_models":
                    second = _judged_task_report("sqlfluff:2", 1)
                elif mismatch == "label":
                    second["judge"]["label"] = SELF_JUDGE_LABEL
                elif mismatch in ("profile", "trial"):
                    profile = second["cases"][0]["profiles"]["baseline"]
                    identity = (
                        profile["judge"]
                        if mismatch == "profile"
                        else profile["trials"][0]["judge"]
                    )
                    identity.update(model="glm-5.2", label=SELF_JUDGE_LABEL)
                elif mismatch == "unknown":
                    second["judge"].update(
                        model="unknown", label="unknown-self-judge-v1"
                    )
                elif mismatch == "temperature":
                    second["judge"]["temperature"] = OPENCODE_QWEN_TEMPERATURE + 0.5
                _write_json(root / "reports" / "first" / "report.json", first)
                _write_json(root / "reports" / "second" / "report.json", second)
                if mismatch is None:
                    report = aggregate_reports(
                        reports_root=root / "reports", output_dir=root / "combined"
                    )
                    self.assertEqual(report["judge"]["model"], "qwen3.8-max")
                    self.assertEqual(report["judge"]["usage"]["calls"], 12)
                else:
                    with self.assertRaisesRegex(SweQaError, "incompatible judge"):
                        aggregate_reports(
                            reports_root=root / "reports", output_dir=root / "combined"
                        )

    def test_aggregate_preserves_generation_metadata_and_rejects_mixing(self) -> None:
        variants = [
            {},
            {"enable_thinking": False},
            {"reasoning_effort": "medium"},
            {"reasoning_effort": None},
            {"max_tokens": 16000},
            {"response_format": {"type": "json_object"}},
            None,
        ]
        for overrides in variants:
            for legacy_first in (False, True):
                with (
                    self.subTest(overrides=overrides, legacy_first=legacy_first),
                    tempfile.TemporaryDirectory() as temp_dir,
                ):
                    root = Path(temp_dir)
                    first = _judged_task_report("reflex:6")
                    second = _judged_task_report("sqlfluff:2", 1)
                    first["judge"].update(copy.deepcopy(JUDGE_GENERATION_METADATA))
                    if overrides is not None:
                        second["judge"].update(copy.deepcopy(JUDGE_GENERATION_METADATA))
                        second["judge"].update(overrides)
                    if legacy_first:
                        first, second = second, first
                    _write_json(root / "reports" / "first" / "report.json", first)
                    _write_json(root / "reports" / "second" / "report.json", second)
                    if overrides == {}:
                        report = aggregate_reports(
                            reports_root=root / "reports",
                            output_dir=root / "combined",
                        )
                        persisted = json.loads(
                            (root / "combined" / "report.json").read_text()
                        )
                        for key, value in JUDGE_GENERATION_METADATA.items():
                            self.assertEqual(report["judge"][key], value)
                            self.assertEqual(persisted["judge"][key], value)
                    else:
                        with self.assertRaisesRegex(
                            SweQaError, "incompatible judge metadata"
                        ):
                            aggregate_reports(
                                reports_root=root / "reports",
                                output_dir=root / "combined",
                            )

    def test_aggregate_preserves_explicit_thinking_false_and_unknown_legacy(
        self,
    ) -> None:
        for metadata in (
            {},
            {
                **JUDGE_GENERATION_METADATA,
                "enable_thinking": False,
                "reasoning_effort": None,
            },
            {
                **JUDGE_GENERATION_METADATA,
                "enable_thinking": False,
                "reasoning_effort": None,
                "response_format": {"type": "json_object"},
            },
        ):
            with (
                self.subTest(metadata=metadata),
                tempfile.TemporaryDirectory() as temp_dir,
            ):
                root = Path(temp_dir)
                for index, task_id in enumerate(("reflex:6", "sqlfluff:2")):
                    report = _judged_task_report(task_id, index)
                    report["judge"].update(copy.deepcopy(metadata))
                    _write_json(root / "reports" / str(index) / "report.json", report)
                combined = aggregate_reports(
                    reports_root=root / "reports", output_dir=root / "combined"
                )
                for key in JUDGE_GENERATION_METADATA:
                    if metadata:
                        self.assertEqual(combined["judge"][key], metadata[key])
                    else:
                        self.assertNotIn(key, combined["judge"])

    def test_aggregate_rejects_partial_or_invalid_generation_metadata(self) -> None:
        invalid: list[dict[str, Any]] = []
        for key, value in JUDGE_GENERATION_METADATA.items():
            invalid.append({key: value})
            invalid.append(
                {
                    name: item
                    for name, item in JUDGE_GENERATION_METADATA.items()
                    if name != key
                }
            )
        for key, values in (
            ("enable_thinking", (None, 0, 1, "true")),
            ("reasoning_effort", ("", True, 4)),
            ("max_tokens", (None, True, 0, -1, 32000.0, "32000")),
            (
                "response_format",
                (
                    "json_object",
                    {},
                    {"type": "text"},
                    {"type": "json_object", "other": True},
                ),
            ),
        ):
            invalid.extend(
                {**JUDGE_GENERATION_METADATA, key: value} for value in values
            )
        for metadata in invalid:
            with (
                self.subTest(metadata=metadata),
                tempfile.TemporaryDirectory() as temp_dir,
            ):
                root = Path(temp_dir)
                report = _judged_task_report("reflex:6")
                report["judge"].update(copy.deepcopy(metadata))
                _write_json(root / "reports" / "report.json", report)
                with self.assertRaises(SweQaError):
                    aggregate_reports(
                        reports_root=root / "reports", output_dir=root / "combined"
                    )

    def test_aggregate_preserves_seed_and_rejects_mixed_sampling(self) -> None:
        for second_seed in (42, 7, None):
            with (
                self.subTest(second_seed=second_seed),
                tempfile.TemporaryDirectory() as temp_dir,
            ):
                root = Path(temp_dir)
                first = _judged_task_report("reflex:6")
                first["judge"]["seed"] = 42
                second = _judged_task_report("sqlfluff:2", 1)
                if second_seed is not None:
                    second["judge"]["seed"] = second_seed
                _write_json(root / "reports" / "first" / "report.json", first)
                _write_json(root / "reports" / "second" / "report.json", second)
                if second_seed == 42:
                    report = aggregate_reports(
                        reports_root=root / "reports", output_dir=root / "combined"
                    )
                    self.assertEqual(report["judge"]["seed"], 42)
                else:
                    with self.assertRaisesRegex(
                        SweQaError, "incompatible judge metadata"
                    ):
                        aggregate_reports(
                            reports_root=root / "reports",
                            output_dir=root / "combined",
                        )

    def test_aggregate_rejects_invalid_seed_metadata(self) -> None:
        for seed in (True, 42.5, "42", None):
            with self.subTest(seed=seed), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                report = _judged_task_report("reflex:6")
                report["judge"]["seed"] = seed
                _write_json(root / "reports" / "report.json", report)
                with self.assertRaisesRegex(SweQaError, "invalid judge seed"):
                    aggregate_reports(
                        reports_root=root / "reports", output_dir=root / "combined"
                    )

    def test_cli_aggregates_single_report_without_glm_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            reports_root = root / "reports"
            output_dir = reports_root / "combined"
            _write_json(
                reports_root / "reflex-6" / "report.json",
                _judged_task_report("reflex:6"),
            )

            with (
                patch.dict("os.environ", {}, clear=True),
                patch("builtins.print") as print_mock,
            ):
                exit_code = swe_qa_main(
                    [
                        "aggregate",
                        "--reports-root",
                        str(reports_root),
                        "--output-dir",
                        str(output_dir),
                        "--expected",
                        "reflex:6",
                    ]
                )

            self.assertEqual(exit_code, 0)
            self.assertEqual(print_mock.call_count, 1)
            report = json.loads((output_dir / "report.json").read_text())
            self.assertEqual(
                [case["task_id"] for case in report["cases"]], ["reflex:6"]
            )
            self.assertEqual(report["gate"]["expected_tasks"], ["reflex:6"])
            self.assertEqual(report["gate"]["successful_judgements"], 6)
            self.assertEqual(report["judge"]["usage"]["calls"], 6)
            self.assertIn("| reflex:6 |", (output_dir / "report.md").read_text())

            # A retry may scan a root that already contains its own prior output.
            # The aggregate output is excluded instead of becoming a source report.
            with patch.dict("os.environ", {}, clear=True):
                retried = aggregate_reports(
                    reports_root=reports_root,
                    output_dir=output_dir,
                )
            self.assertEqual(len(retried["cases"]), 1)

    def test_offline_aggregate_compares_sums_of_task_means(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            reports_root = root / "reports"
            first = _judged_task_report("reflex:6")
            _set_report_trial_metrics(
                first,
                baseline=[
                    (100, 10, 10.0, 1.0),
                    (900, 90, 90.0, 9.0),
                    (100, 10, 20.0, 2.0),
                ],
                zvec=[
                    (10, 1, 1.0, 0.1),
                    (900, 90, 90.0, 9.0),
                    (50, 5, 10.0, 1.0),
                ],
            )
            first["cases"][0]["comparison"]["input_token_reduction_pct"] = 140 / 3
            # Pair by trial_index, not incidental array order in the artifact.
            first["cases"][0]["profiles"]["zvec-grep"]["trials"].reverse()

            second = _judged_task_report("sqlfluff:2", 1)
            _set_report_trial_metrics(
                second,
                baseline=[
                    (1000, 100, 100.0, 10.0),
                    (1000, 100, 100.0, 10.0),
                    (1000, 100, 100.0, 10.0),
                ],
                zvec=[
                    (500, 50, 50.0, 5.0),
                    (500, 50, 50.0, 5.0),
                    (500, 50, 50.0, 5.0),
                ],
            )
            _write_json(reports_root / "first" / "report.json", first)
            _write_json(reports_root / "second" / "report.json", second)

            report = aggregate_reports(
                reports_root=reports_root,
                output_dir=root / "combined",
                expected=["reflex:6", "sqlfluff:2"],
            )

            first_comparison = report["cases"][0]["comparison"]
            self.assertAlmostEqual(
                first_comparison["input_token_reduction_pct"],
                (1100 - 960) / 1100 * 100,
            )
            self.assertEqual(
                [row["trial_index"] for row in first_comparison["trials"]],
                [1, 2, 3],
            )
            self.assertEqual(
                [
                    row["input_token_reduction_pct"]
                    for row in first_comparison["trials"]
                ],
                [90.0, 0.0, 50.0],
            )

            aggregate = report["aggregate"]
            self.assertAlmostEqual(
                aggregate["comparison"]["input_token_reduction_pct"],
                40.0,
            )
            self.assertEqual(
                aggregate["comparison_samples"]["input_token_reduction_pct"],
                2,
            )
            self.assertAlmostEqual(
                aggregate["profiles"]["baseline"]["input_tokens"],
                1100 / 3 + 1000,
            )
            self.assertEqual(aggregate["profiles"]["zvec-grep"]["input_tokens"], 820.0)
            grand_totals_ratio = (
                (
                    aggregate["profiles"]["baseline"]["input_tokens"]
                    - aggregate["profiles"]["zvec-grep"]["input_tokens"]
                )
                / aggregate["profiles"]["baseline"]["input_tokens"]
                * 100
            )
            self.assertAlmostEqual(grand_totals_ratio, 40.0)
            self.assertAlmostEqual(
                aggregate["comparison"]["input_token_reduction_pct"],
                grand_totals_ratio,
            )
            markdown = (root / "combined" / "report.md").read_text()
            self.assertIn("1,366.67 / 820.00 / -40.00%", markdown)

    def test_zero_baseline_metric_still_contributes_to_aggregate_totals(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = _judged_task_report("reflex:6")
            _set_report_trial_metrics(
                source,
                baseline=[
                    (100, 0, 10.0, 0.0),
                    (100, 0, 10.0, 1.0),
                    (100, 0, 10.0, None),
                ],
                zvec=[
                    (50, 1, 5.0, 0.1),
                    (50, 1, 5.0, 0.5),
                    (50, 1, 5.0, None),
                ],
            )
            _write_json(root / "reports" / "source" / "report.json", source)
            _write_json(
                root / "reports" / "valid" / "report.json",
                _judged_task_report("sqlfluff:2", 1),
            )

            report = aggregate_reports(
                reports_root=root / "reports",
                output_dir=root / "combined",
                expected=["reflex:6", "sqlfluff:2"],
            )

            comparison = report["cases"][0]["comparison"]
            self.assertIsNone(comparison["trials"][0]["toolcall_reduction_pct"])
            self.assertIsNone(comparison["toolcall_reduction_pct"])
            self.assertIsNone(comparison["cost_reduction_pct"])
            self.assertAlmostEqual(
                report["aggregate"]["comparison"]["toolcall_reduction_pct"],
                55.0,
            )
            self.assertIsNone(report["aggregate"]["comparison"]["cost_reduction_pct"])
            self.assertEqual(
                report["aggregate"]["profiles"]["baseline"]["tool_calls"],
                20.0,
            )
            self.assertEqual(
                report["aggregate"]["comparison_samples"]["toolcall_reduction_pct"],
                2,
            )
            self.assertEqual(
                report["aggregate"]["comparison_samples"]["cost_reduction_pct"],
                0,
            )
            markdown = (root / "combined" / "report.md").read_text()
            self.assertIn("0.00 / 1.00 / N/A", markdown)
            self.assertIn("20.00 / 9.00 / -55.00%", markdown)
            self.assertIn("toolcall n=2/2", markdown)

    def test_filters_summary_without_relaxing_twenty_task_completion_gate(self) -> None:
        tasks = list(EXPECTED_TASK_IDS)
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            reports_root = root / "reports"
            for index, task_id in enumerate(tasks):
                source = _judged_task_report(task_id, index)
                profile = source["cases"][0]["profiles"]
                baseline_score = int(profile["baseline"]["judge"]["total"])
                zvec_score = int(profile["zvec-grep"]["judge"]["total"])
                _set_report_trial_judgements(
                    source,
                    baseline=[baseline_score] * 5,
                    zvec=[baseline_score + 15 if task_id == "pylint:10" else zvec_score]
                    * 5,
                )
                if task_id == "requests:16":
                    _set_report_trial_metrics(
                        source,
                        baseline=[(1500, 150, 300.0, 3.0)] * 5,
                        zvec=[(6000, 60, 150.0, 1.5)] * 5,
                    )
                _write_json(
                    reports_root / f"artifact-{index}" / "report.json",
                    source,
                )

            with patch.dict("os.environ", {}, clear=True):
                report = aggregate_reports(
                    reports_root=reports_root,
                    output_dir=root / "combined",
                    expected=tasks,
                )

            self.assertEqual([case["task_id"] for case in report["cases"]], tasks)
            self.assertEqual(report["gate"]["expected_tasks"], tasks)
            self.assertEqual(report["gate"]["valid_pairs"], 20)
            self.assertEqual(report["gate"]["successful_judgements"], 200)
            self.assertEqual(report["judge"]["usage"]["calls"], 200)
            self.assertEqual(
                report["judge"]["usage"]["input_tokens"],
                sum(500 * (index + 1) for index in range(20)),
            )
            self.assertEqual(
                report["aggregate"]["profiles"]["baseline"]["input_tokens"],
                19100,
            )
            self.assertEqual(
                report["aggregate"]["comparison"]["input_token_reduction_pct"],
                50.0,
            )
            markdown = (root / "combined" / "report.md").read_text()
            main_table, excluded_section = markdown.split(
                "### Tasks excluded for Judge differences", 1
            )
            self.assertIn("/ -50.00%", markdown)
            for task_id in tasks:
                if task_id in ("requests:16", "pylint:10"):
                    self.assertNotIn(f"| {task_id} |", main_table)
                    self.assertIn(task_id, markdown)
                else:
                    self.assertIn(f"| {task_id} |", main_table)
            self.assertIn("| pylint:10 | 65.00 | 80.00 | +15.00 |", excluded_section)
            self.assertIn("| **Aggregate** |", markdown)
            self.assertLess(
                markdown.index("| **Aggregate** |"), markdown.index("| reflex:6 |")
            )
            self.assertEqual(report["aggregate"]["filter"]["total_count"], 20)
            self.assertEqual(report["aggregate"]["filter"]["included_count"], 18)
            self.assertEqual(report["aggregate"]["filter"]["excluded_count"], 2)

    def test_aggregate_rejects_missing_expected_task(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            reports_root = root / "reports"
            _write_json(
                reports_root / "reflex-6" / "report.json",
                _judged_task_report("reflex:6"),
            )

            with self.assertRaisesRegex(SweQaError, "aggregate report task mismatch"):
                aggregate_reports(
                    reports_root=reports_root,
                    output_dir=root / "combined",
                    expected=["reflex:6", "sqlfluff:2"],
                )

    def test_partial_aggregate_excludes_missing_tasks_from_every_metric(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            reports_root = root / "reports"
            _write_json(
                reports_root / "reflex-6" / "report.json",
                _judged_task_report("reflex:6"),
            )

            report = aggregate_reports(
                reports_root=reports_root,
                output_dir=root / "combined",
                expected=["reflex:6", "sqlfluff:2"],
                allow_missing=True,
            )

            self.assertFalse(report["gate"]["passed"])
            self.assertEqual(
                report["gate"]["expected_tasks"], ["reflex:6", "sqlfluff:2"]
            )
            self.assertEqual(report["gate"]["completed_tasks"], ["reflex:6"])
            self.assertEqual(report["gate"]["missing_tasks"], ["sqlfluff:2"])
            self.assertEqual(report["gate"]["valid_pairs"], 1)
            self.assertEqual(report["aggregate"]["filter"]["total_count"], 1)
            self.assertEqual(
                [case["task_id"] for case in report["cases"]], ["reflex:6"]
            )
            markdown = (root / "combined" / "report.md").read_text()
            self.assertIn("**Incomplete run:** aggregated 1 completed task(s)", markdown)
            self.assertIn("`sqlfluff:2`", markdown)
            self.assertIn("Missing tasks are not assigned zero values", markdown)
            self.assertNotIn("| sqlfluff:2 |", markdown)

    def test_aggregate_rejects_zero_reports(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            reports_root = root / "reports"
            reports_root.mkdir()

            with self.assertRaisesRegex(
                SweQaError, "no per-task report.json files found"
            ):
                aggregate_reports(
                    reports_root=reports_root,
                    output_dir=root / "combined",
                )

    def test_aggregate_rejects_duplicate_task_reports(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = _judged_task_report("reflex:6")
            _write_json(root / "reports" / "one" / "report.json", source)
            _write_json(root / "reports" / "two" / "report.json", source)

            with self.assertRaisesRegex(
                SweQaError, "duplicate task report for reflex:6"
            ):
                aggregate_reports(
                    reports_root=root / "reports",
                    output_dir=root / "combined",
                )


if __name__ == "__main__":
    unittest.main()
