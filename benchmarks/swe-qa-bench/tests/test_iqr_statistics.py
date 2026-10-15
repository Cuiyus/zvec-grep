"""Score-only IQR screening, linked usage, and equal-weight task aggregation."""

from __future__ import annotations

import copy
import unittest

from swe_qa_fixtures import (
    _judged_task_report,
    _session_usage_fixture,
    _set_report_trial_judgements,
)
from zg_bench.metrics.summary import aggregate_cases, profile_statistics
from zg_bench.metrics.usage import SESSION_USAGE_SCOPE, validate_usage_metrics


def profile(scores):
    report = _judged_task_report("task:1")
    _set_report_trial_judgements(report, baseline=scores, zvec=scores)
    return report["cases"][0]["profiles"]["baseline"]


class IqrStatisticsTests(unittest.TestCase):
    def test_score_examples_and_zero_iqr(self):
        for scores, quartiles, retained, excluded in (
            ([95, 13, 94, 90, 95], (90, 95), [1, 3, 4, 5], [(2, "low")]),
            ([90, 87, 87, 56, 88], (87, 88), [2, 3, 5], [(1, "high"), (4, "low")]),
            ([98, 100, 73, 91, 90], (90, 98), [1, 2, 4, 5], [(3, "low")]),
            ([95, 92, 95, 95, 95], (95, 95), [1, 3, 4, 5], [(2, "low")]),
            ([90] * 10, (90, 90), list(range(1, 11)), []),
        ):
            with self.subTest(scores=scores):
                source = profile(scores)
                before = copy.deepcopy(source)
                result = profile_statistics(source)
                f = result["filter"]
                self.assertEqual((f["q1"], f["q3"]), quartiles)
                self.assertEqual(f["retained_trial_indexes"], retained)
                self.assertEqual(
                    [(r["trial_index"], r["direction"]) for r in f["excluded_trials"]],
                    excluded,
                )
                self.assertEqual(f["retained_count"], len(retained))
                self.assertEqual(result["raw"]["judge"], sum(scores) / len(scores))
                self.assertEqual(
                    result["filtered"]["judge"],
                    sum(scores[i - 1] for i in retained) / len(retained),
                )
                self.assertEqual(source, before)

    def test_ten_trials_use_inclusive_interpolation(self):
        f = profile_statistics(profile(list(range(10, 101, 10))))["filter"]
        self.assertEqual((f["q1"], f["q3"], f["iqr"]), (32.5, 77.5, 45))

    def test_fence_boundaries_are_retained(self):
        f = profile_statistics(profile([60, 75, 80, 85, 100]))["filter"]
        self.assertEqual((f["lower_bound"], f["upper_bound"]), (60, 100))
        self.assertEqual(f["excluded_count"], 0)

    def test_screening_is_one_pass(self):
        f = profile_statistics(profile([13, 80, 90, 91, 92]))["filter"]
        # Removing 13 and recalculating would then exclude 80. Do not iterate.
        self.assertEqual(f["retained_trial_indexes"], [2, 3, 4, 5])

    def test_one_and_two_trial_legacy_samples_are_not_forced_to_ten(self):
        for scores in ([70], [5, 100]):
            with self.subTest(scores=scores):
                f = profile_statistics(profile(scores))["filter"]
                self.assertEqual(f["retained_count"], len(scores))

    def test_all_usage_components_follow_the_same_score_mask(self):
        source = profile([13, 95, 95, 95, 95])
        usage = _session_usage_fixture()
        for index, trial in enumerate(source["trials"]):
            scale = 100 if index == 0 else 1
            split = {
                part: {key: value * scale for key, value in usage[part].items()}
                for part in ("root", "descendants", "total")
            }
            trial["metrics"].update(
                **split["total"],
                usage_scope=SESSION_USAGE_SCOPE,
                session_usage={"scope": SESSION_USAGE_SCOPE, "complete": True, **split},
                agent_wall_seconds=10 * scale,
            )
        original_judge_usage = copy.deepcopy(source["judge"]["usage"])
        result = profile_statistics(source)
        filtered = result["filtered"]
        for key, value in usage["total"].items():
            self.assertAlmostEqual(filtered[key], value)
        self.assertEqual(filtered["agent_wall_seconds"], 10)
        self.assertEqual(result["raw"]["agent_wall_seconds"], 208)
        self.assertEqual(source["judge"]["usage"], original_judge_usage)
        validate_usage_metrics(filtered, integer=False)
        for part in ("root", "descendants", "total"):
            for key, value in usage[part].items():
                self.assertAlmostEqual(filtered["session_usage"][part][key], value)

    def test_resource_extremes_do_not_trigger_independent_filter(self):
        source = profile([90] * 10)
        source["trials"][0]["metrics"].update(
            input_tokens=1000000, agent_wall_seconds=999
        )
        result = profile_statistics(source)
        self.assertEqual(result["filter"]["excluded_count"], 0)
        self.assertEqual(result["raw"], result["filtered"])

    def test_task_and_profile_populations_stay_independent_and_equal_weight(self):
        first = _judged_task_report("task:1")
        _set_report_trial_judgements(first, baseline=[75] * 10, zvec=[90] * 9 + [5])
        second = _judged_task_report("task:2")
        _set_report_trial_judgements(
            second, baseline=[50] * 10, zvec=[40] * 8 + [100] * 2
        )
        a, b = first["cases"][0], second["cases"][0]
        before = copy.deepcopy([a, b])
        result = aggregate_cases([a, b])
        self.assertEqual(result["profiles"]["zvec-grep"]["judge"], 65)
        self.assertEqual(result["profiles"]["baseline"]["judge"], 62.5)
        counts = result["filter"]["profiles"]
        self.assertEqual(counts["baseline"]["retained_count"], 20)
        self.assertEqual(counts["zvec-grep"]["retained_count"], 17)
        self.assertNotEqual(
            result["profiles"]["zvec-grep"]["judge"], (90 * 9 + 40 * 8) / 17
        )
        self.assertEqual(result["raw"]["profiles"]["zvec-grep"]["judge"], 66.75)
        self.assertEqual([a, b], before)

    def test_summary_only_legacy_evidence_is_explicitly_unscreened(self):
        source = profile([70] * 5)
        del source["trials"]
        result = profile_statistics(source)
        self.assertEqual(result["filter"]["status"], "unavailable_no_trial_evidence")
        self.assertIsNone(result["filter"]["original_count"])
        self.assertEqual(result["raw"], result["filtered"])


if __name__ == "__main__":
    unittest.main()
