"""Cohort count, no-retry boundaries and one-case statistics; no providers."""
import copy
import json
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import q001_repeat_v1 as driver
import q001_repeat_analysis_v1 as statistics
from test_repeat_analysis_v1 import cases


class Q001RepeatTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.repo = self.root / "repo"
        self.args = SimpleNamespace(plan=self.root / "plan.json", repetition=1,
            output=self.root / "run", command="run")
        self.plan = json.loads((driver.HERE / "q001-repeat-plan-v1.json").read_text())
        self.reference = json.loads((driver.REPO / driver.INNER_PLAN).read_text())
        for relative in driver.CONTROLS:
            target = self.repo / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(driver.REPO / relative, target)
        self.pin()

    def pin(self):
        self.plan["frozen_harness_sha256"] = {p: driver.inner.live.pilot.digest(self.repo / p)
                                                for p in driver.CONTROLS}
        self.args.plan.write_text(json.dumps(self.plan))

    def validate(self):
        with patch.object(driver.inner, "validate_plan", return_value=(self.reference, {}, {})):
            return driver.validate_plan(self.args, self.repo)

    def test_exactly_ten_pairs_and_balanced_orders(self):
        rows = driver.schedule()
        self.assertEqual(len(rows), 10)
        self.assertEqual({row["task_id"] for row in rows}, {"q001"})
        self.assertEqual({row["trial_index"] for row in rows}, set(range(1, 11)))
        self.assertEqual(sum(row["profiles"][0] == "baseline" for row in rows), 5)
        self.assertEqual(sum(len(row["profiles"]) for row in rows), 20)
        self.validate()

    def test_scope_count_parallelism_and_runtime_overrides_fail(self):
        original = copy.deepcopy(self.plan)
        for update in ({"task_ids": ["q001", "q002"]}, {"scheduled_solver_trials": 60},
                       {"max_parallel_pairs": 3}, {"max_retries": 1}, {"n_attempts_per_pair": True},
                       {"provider": {}}, {"frozen": False}):
            self.plan = copy.deepcopy(original)
            self.plan.update(update)
            self.pin()
            with self.subTest(update=update), self.assertRaises(ValueError):
                self.validate()

    def test_bad_repeat_and_non_q001_rejected(self):
        for index in (0, 11, True):
            self.args.repetition = index
            with self.assertRaises(ValueError):
                self.validate()
        self.args.repetition, self.args.task_id = 1, "q002"
        with self.assertRaisesRegex(ValueError, "q001_only"):
            self.validate()

    def test_original_runtime_and_frozen_files_are_required(self):
        self.validate()
        (self.repo / driver.INNER_PLAN).write_text("{}")
        with self.assertRaisesRegex(ValueError, "inner_runtime_reference_changed"):
            self.validate()

    def test_disabled_authorization_fails(self):
        path = self.repo / driver.DECISION
        decision = json.loads(path.read_text())
        decision["paid_execution_authorized"] = False
        path.write_text(json.dumps(decision))
        self.pin()
        with self.assertRaisesRegex(ValueError, "execution_not_authorized"):
            self.validate()

    def test_context_is_exclusive_and_dry_run_cannot_be_collected(self):
        self.args.command = "dry-run"
        with patch.object(driver.inner, "execute", return_value=0) as execute:
            self.assertEqual(driver.execute(self.args, self.plan, self.reference, {}, {}, self.repo), 0)
            selected = execute.call_args.args[0]
            self.assertEqual(selected.task_id, "q001")
            self.assertEqual(selected.plan, self.repo / driver.INNER_PLAN)
            with self.assertRaises(FileExistsError):
                driver.execute(self.args, self.plan, self.reference, {}, {}, self.repo)
        self.args.command = "collect"
        with self.assertRaisesRegex(ValueError, "cohort_context"):
            driver.execute(self.args, self.plan, self.reference, {}, {}, self.repo)

    def test_raw10_trim8_and_single_task_mad_guard(self):
        source = cases()[:1]
        report = statistics.analyze(source, resamples=20)
        self.assertTrue(report["stable_reported_input_with_quality_preserved_on_q001"])
        self.assertEqual(report["statistics"]["task_filter"]["status"], "unavailable_insufficient_tasks")
        for count in report["statistics"]["task_filter"]["profiles"].values():
            self.assertEqual(count, {"original_count": 10, "after_trim_count": 8, "final_count": 8})
        for view in ("raw", "score_trimmed"):
            interval = report["repeated_estimates"]["q001"][view]["intervals"]["input_reduction_pct"]
            self.assertAlmostEqual(interval["lower"], 50)
            self.assertAlmostEqual(interval["upper"], 50)

    def test_incomplete_or_invalid_data_cannot_be_rehabilitated(self):
        source = cases()[:1]
        source[0]["profiles"]["baseline"]["trials"][0]["execution_and_scope_valid"] = False
        report = statistics.analyze(source, resamples=10)
        self.assertFalse(report["stable_reported_input_with_quality_preserved_on_q001"])
        source[0]["profiles"]["baseline"]["trials"].pop()
        with self.assertRaises(statistics.implementation.SweQaError):
            statistics.analyze(source, resamples=10)
        with self.assertRaises(statistics.implementation.SweQaError):
            statistics.analyze(cases(), resamples=10)


if __name__ == "__main__":
    unittest.main()
