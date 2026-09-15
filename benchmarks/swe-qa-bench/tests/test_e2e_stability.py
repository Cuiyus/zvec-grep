from __future__ import annotations

import copy
import json
import math
import tempfile
import unittest
from pathlib import Path

from zg_bench.swe_qa.e2e_stability import make_plan, summarize, render_markdown, render_ci_conclusion, main


def observations(plan):
    results, quality = [], []
    for trial in plan["trials"]:
        arm, block = trial["arm"], trial["block"]
        results.append({"trial_id": trial["trial_id"], "status": "completed",
                        "usage_complete": True, "tools_complete": True,
                        "metrics": {"input_tokens": 1000 + block * 10 - {"B": 0, "C": 100, "N": 200}[arm],
                                    "tool_calls_attempted": {"B": 12, "C": 10, "N": 8}[arm],
                                    "cache_read_tokens": 300},
                        "behavior": {"zg_adopted": arm != "B", "first_zg_request":
                                     {"arguments": {"query": "dependency tracking", "limit": 10}, "name": "zg_search"} if arm != "B" else None}})
        quality.append({"trial_id": trial["trial_id"], "consensus_status": "pass", "quality": "pass"})
    return results, quality


class E2EStabilityTests(unittest.TestCase):
    def test_plan_is_frozen_reproducible_and_balanced_across_seeds(self):
        for candidate, arms in (("P10", {"B", "C", "N"}), (None, {"B", "C"})):
            for seed in range(40):
                plan = make_plan("reflex-6", seed=seed, candidate=candidate)
                self.assertEqual(plan, make_plan("reflex-6", seed=seed, candidate=candidate))
                self.assertEqual(len(plan["trials"]), 10 * len(arms))
                self.assertEqual(plan["model_seed"], 20260915)
                self.assertEqual({t["model_seed"] for t in plan["trials"]}, {20260915})
                for block in range(1, 11):
                    trials = [t for t in plan["trials"] if t["block"] == block]
                    self.assertEqual({t["arm"] for t in trials}, arms)
                    self.assertEqual({t["repetition"] for t in trials}, {block})
                for arm in arms:
                    counts = [sum(t["arm"] == arm and t["position"] == p for t in plan["trials"])
                              for p in range(1, len(arms) + 1)]
                    self.assertLessEqual(max(counts) - min(counts), 1)
                summarize(plan, [])  # Generated plans also satisfy report validation.
        self.assertNotEqual(make_plan("case", seed=1), make_plan("case", seed=2))

    def test_no_promotion_is_explicit_and_cannot_duplicate_current_as_candidate(self):
        plan = make_plan("case", candidate=None)
        self.assertEqual(plan["candidate_status"], "no_promotion")
        self.assertEqual(set(summarize(plan, [])["comparisons"]), {"C-B"})
        self.assertEqual(make_plan("case")["candidate_status"], "placeholder")
        for candidate in ("P00", "", 1):
            with self.assertRaises(ValueError):
                make_plan("case", candidate=candidate)
        for repetitions in (0, 5, 9, 11, True):
            with self.assertRaises(ValueError):
                make_plan("case", repetitions=repetitions)

    def test_complete_native_costs_form_three_distinct_ten_pair_comparisons(self):
        plan = make_plan("case", candidate="P10")
        results, quality = observations(plan)
        report = summarize(plan, {"trials": results}, {"trials": quality},
                           source_reference={"commit": "abc", "qa_gold_sha256": "def"},
                           controls={"temperature": 0, "model_seed": 20260915})
        self.assertEqual(report["execution_counts"], {"completed": 30})
        self.assertEqual(report["source_reference"]["commit"], "abc")
        self.assertEqual(report["unknown_controls"], [])
        for name, expected in (("C-B", -100), ("N-B", -200), ("N-C", -100)):
            metric = report["comparisons"][name]["metrics"]["input_tokens"]
            self.assertEqual(metric["qualified_difference_summary"]["values"], [expected] * 10)
            self.assertEqual(metric["qualified_difference_summary"]["mean"], expected)
            self.assertTrue(metric["complete_planned_pair_estimate"])
            self.assertEqual(metric["auxiliary_t95"]["lower"], expected)
            self.assertEqual(metric["auxiliary_t95"]["n"], 10)
        self.assertEqual(report["arms"]["N"]["cost"]["input_tokens"]["observed_all_statuses"]["mean"], 855)

    def test_failed_cheap_run_stays_visible_but_cannot_be_a_benefit(self):
        plan = make_plan("case", candidate="P10")
        results, quality = observations(plan)
        failed = next(r for r in results if r["trial_id"] == "case-b01-N")
        failed.update(status="timeout", error="time budget exhausted", retries=2)
        failed["metrics"]["input_tokens"] = 1
        report = summarize(plan, results, quality)
        metric = report["comparisons"]["N-B"]["metrics"]["input_tokens"]
        pair = metric["pairs"][0]
        self.assertEqual(pair["observed_delta"], -1009)
        self.assertFalse(pair["benefit_eligible"])
        self.assertIn("N:execution_timeout", pair["exclusions"])
        self.assertIsNone(metric["auxiliary_t95"])
        self.assertEqual(metric["eligible_pairs"], 9)
        self.assertEqual(report["arms"]["N"]["planned"], 10)
        self.assertEqual(report["arms"]["N"]["completed_quality_passed"], 9)
        preserved = next(r for r in report["trials"] if r["trial_id"] == failed["trial_id"])
        self.assertEqual(preserved["observation"]["error"], "time budget exhausted")
        self.assertEqual(preserved["observation"]["retries"], 2)

    def test_missing_usage_is_not_zero_and_token_and_tool_completeness_are_separate(self):
        plan = make_plan("case", candidate="P01")
        results, quality = observations(plan)
        item = next(r for r in results if r["trial_id"] == "case-b02-N")
        item["usage_complete"] = False
        item["metrics"].pop("input_tokens")
        report = summarize(plan, results, quality)
        tokens = report["comparisons"]["N-C"]["metrics"]["input_tokens"]
        tools = report["comparisons"]["N-C"]["metrics"]["tool_calls_attempted"]
        self.assertIsNone(tokens["pairs"][1]["observed_delta"])
        self.assertEqual(tokens["eligible_pairs"], 9)
        self.assertEqual(tools["eligible_pairs"], 10)
        self.assertEqual(report["arms"]["N"]["cost"]["input_tokens"]["observed_all_statuses"]["n"], 9)

    def test_unknown_completeness_and_missing_quality_never_become_passed(self):
        plan = make_plan("case", candidate="P10")
        results, quality = observations(plan)
        results[0].pop("usage_complete")
        results[0]["tools_complete"] = "true"
        report = summarize(plan, results, [])
        self.assertEqual(report["quality_counts"], {"unscored": 30})
        self.assertIsNone(report["trials"][0]["usage_completeness"])
        self.assertIsNone(report["trials"][0]["tools_completeness"])
        self.assertEqual(report["comparisons"]["N-B"]["metrics"]["input_tokens"]["eligible_pairs"], 0)
        self.assertEqual(report["controls_record_status"], "unknown")

    def test_quality_regression_blocks_candidate_cost_but_not_current_comparison(self):
        plan = make_plan("case", candidate="P10")
        results, quality = observations(plan)
        next(q for q in quality if q["trial_id"] == "case-b03-N")["consensus_status"] = "fail"
        report = summarize(plan, results, quality)
        self.assertEqual(report["comparisons"]["N-B"]["metrics"]["input_tokens"]["eligible_pairs"], 9)
        self.assertEqual(report["comparisons"]["C-B"]["metrics"]["input_tokens"]["eligible_pairs"], 10)
        self.assertEqual(report["arms"]["N"]["quality_counts"], {"pass": 9, "fail": 1})

    def test_missing_planned_trials_and_unplanned_observations_are_preserved(self):
        plan = make_plan("case", candidate="P10")
        results, quality = observations(plan)
        removed = results.pop()
        plan["trials"][-1]["status"] = "completed"
        results.append({"trial_id": "unplanned", "status": "completed", "metrics": {"input_tokens": 1}})
        report = summarize(plan, results, quality)
        self.assertEqual(report["observed_trials"], 29)
        self.assertEqual(report["execution_counts"]["missing"], 1)
        self.assertEqual(report["trials"][-1]["trial_id"], removed["trial_id"])
        self.assertEqual(report["trials"][-1]["execution_status"], "missing")
        self.assertEqual(report["unplanned_results"][0]["trial_id"], "unplanned")

    def test_request_grouping_ignores_key_order_but_preserves_complete_arguments(self):
        plan = make_plan("case", candidate="P10")
        results, quality = observations(plan)
        rows = [r for r in results if r["trial_id"].endswith("-N")]
        rows[1]["behavior"]["first_zg_request"] = {"name": "zg_search", "arguments": {"limit": 10, "query": "dependency tracking"}}
        rows[2]["behavior"]["first_zg_request"]["arguments"].pop("limit")
        rows[3]["behavior"].update(first_zg_request=None, zg_adopted=False)
        rows[4]["behavior"] = {}
        report = summarize(plan, results, quality)
        summary = report["arms"]["N"]
        self.assertEqual(summary["zg_adoption"], {"yes": 8, "no": 1, "unknown": 1, "planned": 10})
        self.assertEqual(summary["first_complete_request_repetition"]["unique"], 2)
        self.assertEqual(summary["first_complete_request_repetition"]["modal_count"], 7)
        self.assertEqual(summary["first_complete_request_repetition"]["missing"], 2)
        self.assertEqual(summary["first_query_repetition"]["unique"], 1)

    def test_report_rejects_duplicate_ids_and_malformed_schedule(self):
        plan = make_plan("case", candidate="P10")
        results, _ = observations(plan)
        with self.assertRaisesRegex(ValueError, "unique"):
            summarize(plan, results + [results[0]])
        invalid = copy.deepcopy(plan)
        invalid["trials"][0]["arm"] = invalid["trials"][1]["arm"]
        with self.assertRaisesRegex(ValueError, "exactly one"):
            summarize(invalid, [])
        invalid = copy.deepcopy(plan)
        invalid["trials"][0]["order"], invalid["trials"][1]["order"] = invalid["trials"][1]["order"], invalid["trials"][0]["order"]
        with self.assertRaisesRegex(ValueError, "agree"):
            summarize(invalid, [])

    def test_nonfinite_negative_and_boolean_metrics_are_unknown(self):
        for bad in (math.nan, math.inf, -1, True, "100"):
            plan = make_plan("case", candidate="P10")
            results, quality = observations(plan)
            results[0]["metrics"]["input_tokens"] = bad
            report = summarize(plan, results, quality)
            self.assertIsNone(report["trials"][0]["metrics"]["input_tokens"])
            json.dumps(report, allow_nan=False)

    def test_render_and_offline_cli_preserve_existing_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertEqual(main(["plan", "--case-id", "case", "--candidate", "P10", "--output", str(root / "plan.json")]), 0)
            plan = json.loads((root / "plan.json").read_text())
            results, quality = observations(plan)
            (root / "results.json").write_text(json.dumps(results))
            (root / "quality.json").write_text(json.dumps(quality))
            args = ["report", "--plan", str(root / "plan.json"), "--results", str(root / "results.json"),
                    "--quality", str(root / "quality.json"), "--output", str(root / "report.json")]
            self.assertEqual(main(args), 0)
            report = json.loads((root / "report.json").read_text())
            markdown = render_markdown(report)
            self.assertEqual(markdown, (root / "report.md").read_text())
            self.assertIn("N-C", markdown)
            self.assertIn("independent approximately normal", markdown)
            with self.assertRaises(SystemExit):
                main(args)

    def test_ci_conclusion_separates_applied_controls_from_behavior_stability(self):
        plan = make_plan("case", candidate=None)
        results, quality = observations(plan)
        for row in results:
            row["wire_contract"] = {"valid": True, "temperature_zero_verified": True, "seed_verified": True}
        report = summarize(plan, results, quality)
        retrieval = {"case_id": "case", "replays": [{"stability": {"identical_all_five": True},
            "context_assessments": [{"assessment": {"status": "scored", "target": {
                "hit_at_1": False, "hit_at_5": True, "hit_at_10": True, "rr_at_10": 0.2}}}]}],
            "actual_vs_replay": [{"original_vs_replay_text_identical": True}]}
        markdown = render_ci_conclusion({"opencode-test": report}, retrieval)
        self.assertIn("temp=0 verified", markdown)
        self.assertIn("20/20", markdown)
        self.assertIn("behavior stability is reported separately", markdown)
        self.assertIn("Hit@10", markdown)
        self.assertIn("lower cost observed", markdown)


if __name__ == "__main__":
    unittest.main()
