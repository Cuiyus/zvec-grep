import copy
import unittest

from robust_statistics_v1 import SweQaError, aggregate_cases, profile_statistics
from test_robust_statistics_pr261 import measured_profile


def one_task(task_id="q002"):
    return {"task_id": task_id, "profiles": {
        name: {"trials": [{"trial_index": 1, "judge": {"total": 9},
            "quality_pass": name == "zvec-grep", "metrics": {
                "input_tokens": 100 if name == "baseline" else 60,
                "output_tokens": 10, "tool_calls": 2, "agent_wall_seconds": 5,
                "outer_slot_seconds": 7 if name == "baseline" else 9,
                "cost_usd": None}}]} for name in ("baseline", "zvec-grep")}}


class NativeStatisticsTests(unittest.TestCase):
    def test_small_phase_retains_every_observation_and_explains_guards(self):
        cases = [one_task(), one_task("q003")]
        before = copy.deepcopy(cases)
        report = aggregate_cases(cases)
        self.assertEqual(report["task_filter"]["status"], "unavailable_insufficient_tasks")
        self.assertEqual(report["task_filter"]["excluded_task_ids"], [])
        self.assertEqual(report["raw"], report["score_trimmed"])
        self.assertEqual(report["raw"], report["task_screened"])
        self.assertFalse(report["planned_trial_count_complete"])
        self.assertFalse(report["frozen_quality_and_execution_decisions_modified"])
        self.assertEqual(cases, before)
        for task in report["task_statistics"]:
            for profile in task["profiles"].values():
                self.assertEqual(profile["filter"]["status"], "unavailable_insufficient_trials")

    def test_quality_failure_is_not_an_exclusion_rule(self):
        report = aggregate_cases([one_task()])
        self.assertEqual(report["task_filter"]["retained_task_ids"], ["q002"])
        self.assertEqual(report["raw"]["profiles"]["baseline"]["judge"], 9)

    def test_native_extra_metrics_use_same_retained_score_tuples(self):
        profile = measured_profile()
        for trial in profile["trials"]:
            trial["metrics"]["outer_slot_seconds"] = trial["trial_index"] * 100
        result = profile_statistics(profile)
        self.assertEqual(result["filter"]["retained_trial_indexes"], list(range(2, 10)))
        self.assertEqual(result["filtered"]["outer_slot_seconds"], 550)

    def test_paired_task_removal_and_equal_weight_means(self):
        cases = [{"task_id": f"task:{i}", "profiles": {
            "baseline": measured_profile(spread),
            "zvec-grep": measured_profile(0, center=75)}}
            for i, spread in enumerate((1, 2, 3, 4, 5, 30), 1)]
        before = copy.deepcopy(cases)
        report = aggregate_cases(cases)
        self.assertEqual(report["task_filter"]["excluded_task_ids"], ["task:6"])
        for counts in report["task_filter"]["profiles"].values():
            self.assertEqual(counts, {"original_count": 60, "after_trim_count": 48, "final_count": 40})
        self.assertEqual(report["raw"]["task_count"], 6)
        self.assertEqual(report["score_trimmed"]["task_count"], 6)
        self.assertEqual(report["task_screened"]["task_count"], 5)
        self.assertEqual(report["task_screened"]["profiles"]["baseline"]["judge"], 50)
        self.assertEqual(report["task_screened"]["profiles"]["zvec-grep"]["judge"], 75)
        self.assertTrue(report["planned_trial_count_complete"])
        self.assertEqual(cases, before)

    def test_mean_effect_alone_never_removes_task(self):
        cases = [{"task_id": str(i), "profiles": {
            "baseline": measured_profile(0, center=90),
            "zvec-grep": measured_profile(0, center=10 if i == 6 else 90)}} for i in range(7)]
        report = aggregate_cases(cases)
        self.assertEqual(report["task_filter"]["excluded_task_ids"], [])
        self.assertAlmostEqual(report["task_screened"]["comparison"]["judge_delta"], -80 / 7)

    def test_unknown_cost_and_distinct_time_scopes_remain_visible(self):
        report = aggregate_cases([one_task()])["task_screened"]
        self.assertIsNone(report["profiles"]["baseline"]["cost_usd"])
        self.assertIsNone(report["comparison"]["cost_usd_reduction_pct"])
        self.assertEqual(report["comparison"]["input_tokens_reduction_pct"], 40)
        self.assertEqual(report["comparison"]["agent_wall_seconds_reduction_pct"], 0)
        self.assertLess(report["comparison"]["outer_slot_seconds_reduction_pct"], 0)

    def test_missing_bad_and_unmatched_raw_evidence_is_rejected(self):
        for mutation in ("missing", "nan", "bool", "duplicate", "unpaired"):
            with self.subTest(mutation=mutation):
                case = one_task()
                trial = case["profiles"]["baseline"]["trials"][0]
                if mutation == "missing":
                    del trial["metrics"]["input_tokens"]
                elif mutation == "nan":
                    trial["judge"]["total"] = float("nan")
                elif mutation == "bool":
                    trial["metrics"]["tool_calls"] = True
                elif mutation == "duplicate":
                    case["profiles"]["baseline"]["trials"].append(copy.deepcopy(trial))
                else:
                    trial["trial_index"] = 2
                with self.assertRaises(SweQaError):
                    aggregate_cases([case])


if __name__ == "__main__":
    unittest.main()
