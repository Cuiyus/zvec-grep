"""Large version regressions and improvements must remain in primary results."""
import copy
import unittest

from swe_qa_fixtures import _judged_task_report, _set_report_trial_judgements, _set_report_trial_metrics
from zg_bench.core.errors import SweQaError
from zg_bench.metrics.summary import aggregate_cases
from zg_bench.version_summary import include_all_cases, render_version_report


class FullCaseSummaryTests(unittest.TestCase):
    def make_report(self):
        reports = []
        for task, before, after, tokens in (("reflex:6", 50, 80, 300), ("sqlfluff:2", 80, 40, 100)):
            report = _judged_task_report(task)
            _set_report_trial_judgements(report, baseline=[before] * 5, zvec=[after] * 5)
            _set_report_trial_metrics(report, baseline=[(100, 2, 4.0, None)] * 5,
                                      zvec=[(tokens, 3, 6.0, None)] * 5)
            reports.append(report)
        result = reports[0]
        result["cases"] += reports[1]["cases"]
        result["aggregate"] = aggregate_cases(result["cases"])
        result["gate"].update(expected_tasks=["reflex:6", "sqlfluff:2", "conan:1"], missing_tasks=["conan:1"], passed=False)
        result.update(model="glm-5.2", embedding="local")
        return result

    def test_extreme_outcomes_retained_and_missing_case_not_zeroed(self):
        source = self.make_report()
        original = copy.deepcopy(source)
        result = include_all_cases(source)
        self.assertEqual(source, original)
        self.assertEqual(result["legacy_filtered_aggregate"]["filter"]["included_count"], 0)
        aggregate = result["aggregate"]
        self.assertEqual(aggregate["filter"]["included_count"], 2)
        self.assertEqual(aggregate["filter"]["criteria"], [])
        self.assertEqual(aggregate["profiles"]["baseline"]["judge"], 65)
        self.assertEqual(aggregate["profiles"]["zvec-grep"]["judge"], 60)
        self.assertEqual(aggregate["comparison"]["judge_delta"], -5)
        self.assertEqual(aggregate["comparison"]["input_token_reduction_pct"], -100)
        self.assertEqual(result["gate"]["missing_tasks"], ["conan:1"])
        markdown = render_version_report(result)
        self.assertIn("Completed tasks: 2/3", markdown)
        self.assertIn("| reflex:6 |", markdown)
        self.assertIn("| sqlfluff:2 |", markdown)
        self.assertEqual(include_all_cases(result), result)

    def test_incomplete_trial_group_is_rejected(self):
        report = self.make_report()
        report["cases"][0]["profiles"]["baseline"]["trials"].pop()
        with self.assertRaisesRegex(SweQaError, "five trials"):
            include_all_cases(report)
