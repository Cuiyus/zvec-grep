import copy
import unittest

from repeat_analysis_v1 import SweQaError, _task_estimates, analyze


def cases():
    result = []
    for task in ("q001", "q002", "q003"):
        profiles = {}
        for name in ("baseline", "zvec-grep"):
            trials = []
            for index in range(1, 11):
                trials.append({"trial_index": index, "judge": {"total": 7 + index % 3},
                    "quality_pass": 7 + index % 3 >= 8, "execution_and_scope_valid": True,
                    "reported_metric_eligible": True, "metrics": {
                        "input_tokens": (100 + index * index) * (1 if name == "baseline" else 0.5),
                        "output_tokens": 10 + index, "tool_calls": index,
                        "agent_wall_seconds": 5 + index,
                        "outer_slot_seconds": 10 + index if name == "baseline" else 15 + index,
                        "cost_usd": None}})
            profiles[name] = {"trials": trials}
        result.append({"task_id": task, "profiles": profiles})
    return result


class RepeatAnalysisTests(unittest.TestCase):
    def test_joint_resampling_preserves_per_pair_quality_and_token_ratio(self):
        source = cases()
        before = copy.deepcopy(source)
        report = analyze(source, resamples=40)
        for task in report["repeated_estimates"].values():
            for view in ("raw", "score_trimmed"):
                intervals = task[view]["intervals"]
                self.assertEqual(intervals["judge_delta"]["lower"], 0)
                self.assertEqual(intervals["judge_delta"]["upper"], 0)
                self.assertEqual(intervals["quality_pass_rate_delta"]["lower"], 0)
                self.assertAlmostEqual(intervals["input_reduction_pct"]["lower"], 50)
                self.assertAlmostEqual(intervals["input_reduction_pct"]["upper"], 50)
        self.assertTrue(report["stable_reported_input_with_quality_preserved_on_all_existing_tasks"])
        self.assertEqual(source, before)

    def test_bootstrap_multiplicity_keeps_exactly_eight_tuples(self):
        source = cases()[0]["profiles"]
        for profile in source.values():
            for trial in profile["trials"]:
                trial["judge"]["total"] = 9
            indexed = {row["trial_index"]: row for row in profile["trials"]}
            profile["trials"] = [indexed[i] for i in (1, 1, 1, 1, 2, 3, 4, 5, 9, 10)]
        estimates = _task_estimates(source)
        expected = sum(100 + i * i for i in (1, 1, 1, 2, 3, 4, 5, 9)) / 8
        self.assertAlmostEqual(estimates["score_trimmed"]["baseline"]["input_tokens"], expected)

    def test_invalid_execution_blocks_task_and_global_stability(self):
        source = cases()
        source[1]["profiles"]["baseline"]["trials"][4]["execution_and_scope_valid"] = False
        report = analyze(source, resamples=10)
        local = report["repeated_estimates"]["q002"]
        self.assertTrue(local["statistical_criterion_met_only"])
        self.assertFalse(local["stable_reported_input_with_quality_preserved"])
        self.assertTrue(report["repeated_estimates"]["q001"]["stable_reported_input_with_quality_preserved"])
        self.assertFalse(report["repeated_estimates"]["all_tasks"]["stable_reported_input_with_quality_preserved"])
        self.assertFalse(report["stable_reported_input_with_quality_preserved_on_all_existing_tasks"])

    def test_bad_task_cannot_be_averaged_away(self):
        source = cases()
        for case, score_pair in zip(source, ((3, 10), (9, 8), (9, 10))):
            for name, score in zip(("baseline", "zvec-grep"), score_pair):
                for trial in case["profiles"][name]["trials"]:
                    trial["judge"]["total"] = score
                    trial["quality_pass"] = score >= 8 and not (case["task_id"] == "q002" and name == "zvec-grep")
        report = analyze(source, resamples=10)
        self.assertGreater(report["repeated_estimates"]["all_tasks"]["raw"]["comparison"]["judge_delta"], 0)
        self.assertFalse(report["repeated_estimates"]["q002"]["stable_reported_input_with_quality_preserved"])
        self.assertFalse(report["stable_reported_input_with_quality_preserved_on_all_existing_tasks"])
        self.assertEqual(report["statistics"]["task_filter"]["excluded_task_ids"], [])

    def test_missing_repeats_and_missing_quality_are_not_filled(self):
        for mode in ("repeat", "quality"):
            source = cases()
            for profile in source[0]["profiles"].values():
                if mode == "repeat":
                    profile["trials"].pop()
                else:
                    del profile["trials"][0]["quality_pass"]
            with self.assertRaises(SweQaError):
                analyze(source, resamples=10)

    def test_optional_unknown_time_and_cost_remain_unknown(self):
        source = cases()
        for case in source:
            for profile in case["profiles"].values():
                for trial in profile["trials"]:
                    trial["metrics"]["outer_slot_seconds"] = None
        report = analyze(source, resamples=10)
        for view in ("raw", "score_trimmed"):
            row = report["repeated_estimates"]["all_tasks"][view]
            self.assertIsNone(row["profiles"]["baseline"]["cost_usd"])
            self.assertEqual(row["intervals"]["outer_slot_reduction_pct"]["status"], "unavailable_incomplete_resamples")

    def test_seed_replays_and_three_tasks_keep_mad_disabled(self):
        left = analyze(cases(), resamples=12, seed=1)
        right = analyze(cases(), resamples=12, seed=1)
        self.assertEqual(left, right)
        self.assertEqual(left["statistics"]["task_filter"]["status"], "unavailable_insufficient_tasks")
        self.assertEqual(left["statistics"]["task_filter"]["retained_task_count"], 3)
        for count in left["statistics"]["task_filter"]["profiles"].values():
            self.assertEqual(count, {"original_count": 30, "after_trim_count": 24, "final_count": 24})


if __name__ == "__main__":
    unittest.main()
