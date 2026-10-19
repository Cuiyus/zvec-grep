import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch


def load_module(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


pilot = load_module("pilot_plan", "run.py")
audit = load_module("pilot_order_audit", "audit_artifacts.py")
CONFIRMATION = [{"repeat": 2, "profile": "zg"}, {"repeat": 2, "profile": "baseline"},
                {"repeat": 3, "profile": "baseline"}, {"repeat": 3, "profile": "zg"}]


class TrialPlanTests(unittest.TestCase):
    def test_default_plan_unchanged_and_confirmation_continues_alternation(self):
        args = pilot.parser().parse_args([
            "--source", "/source", "--prompt", "/prompt", "--output", "/output",
            "--index-seed", "/index", "--model", "qwen3.8-max",
        ])
        self.assertEqual(args.start_repeat, 1)
        self.assertEqual(pilot.trial_plan(args.start_repeat, args.repeats),
                         [{"repeat": 1, "profile": "baseline"}, {"repeat": 1, "profile": "zg"}])
        self.assertEqual(pilot.trial_plan(2, 2), CONFIRMATION)

    def test_invalid_repeat_range_rejected(self):
        for start, repeats in ((0, 1), (-1, 2), (1, 0), (True, 2), (1, 1.5)):
            with self.subTest(start=start, repeats=repeats), self.assertRaises(ValueError):
                pilot.trial_plan(start, repeats)

    def run_controller(self, first_failure=None):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, index, output = root / "source", root / "index", root / "output"
            source.mkdir()
            index.mkdir()
            (source / "source.txt").write_text("frozen source\n")
            prompt = root / "prompt.txt"
            prompt.write_text("Frozen question.")
            commands = ["run.py", "--source", str(source), "--index-seed", str(index),
                        "--output", str(output), "--prompt", str(prompt), "--model", "qwen3.8-max",
                        "--start-repeat", "2", "--repeats", "2"]
            observed = []

            def trial(args, profile, repeat, question):
                observed.append({"repeat": repeat, "profile": profile})
                failed = len(observed) == 1 and first_failure is not None
                return {"repeat": repeat, "profile": profile, "valid": not failed,
                        "errors": [first_failure] if failed else [],
                        "infrastructure_errors": [first_failure] if failed and first_failure == "infra" else []}

            environment = {"ANTHROPIC_API_KEY": "dummy-plan-only", "ANTHROPIC_BASE_URL": "http://fixture.invalid"}
            with patch("sys.argv", commands), patch.dict("os.environ", environment, clear=True), \
                    patch.object(pilot.subprocess, "run", return_value=Mock(stdout="fixture-version\n")), \
                    patch.object(pilot, "run_trial", side_effect=trial), patch("builtins.print"):
                code = pilot.main()
            return code, observed, json.loads((output / "manifest.json").read_text()), \
                json.loads((output / "summary.json").read_text())

    def test_manifest_and_execution_share_plan(self):
        code, observed, manifest, summary = self.run_controller()
        self.assertEqual(code, 0)
        self.assertEqual(observed, CONFIRMATION)
        self.assertEqual(manifest["start_repeat"], 2)
        self.assertEqual(manifest["planned_trials"], observed)
        self.assertEqual(manifest["order"], [item["profile"] for item in observed])
        self.assertEqual([audit.trial_identity(item) for item in summary["trials"]], observed)
        self.assertTrue(summary["all_planned_trials_completed"])

    def test_task_failure_retained_and_remaining_trials_run(self):
        code, observed, _, summary = self.run_controller("wall_timeout")
        self.assertEqual(code, 1)
        self.assertEqual(observed, CONFIRMATION)
        self.assertEqual(summary["invalid_trial_count"], 1)
        self.assertEqual(summary["trials"][0]["errors"], ["wall_timeout"])
        self.assertTrue(summary["all_planned_trials_completed"])

    def test_infrastructure_failure_retained_and_spending_stops(self):
        code, observed, manifest, summary = self.run_controller("infra")
        self.assertEqual(code, 1)
        self.assertEqual(observed, CONFIRMATION[:1])
        self.assertEqual(manifest["planned_trials"], CONFIRMATION)
        self.assertEqual(summary["planned_trial_count"], 4)
        self.assertFalse(summary["all_planned_trials_completed"])
        self.assertEqual(summary["invalid_trial_count"], 1)


class ExecutionOrderAuditTests(unittest.TestCase):
    def fixture(self):
        manifest = {"start_repeat": 2, "repeats": 2, "planned_trials": CONFIRMATION,
                    "order": [item["profile"] for item in CONFIRMATION], "tool_budget": 24}
        summary = {"trials": [dict(item) for item in CONFIRMATION], "planned_trial_count": 4,
                   "all_planned_trials_completed": True}
        trials = [{"profile": audit.trial_name(item), "execution": {
            **item, "hook_present": True, "hook_started_at_epoch": 1000 + index * 900}}
            for index, item in enumerate(CONFIRMATION)]
        return manifest, summary, trials

    def test_summary_and_clock_establish_order_despite_directory_sort(self):
        manifest, summary, trials = self.fixture()
        result = audit.audit_execution_order(manifest, summary, sorted(trials, key=lambda item: item["profile"]))
        self.assertTrue(result["valid"], result["errors"])
        self.assertEqual(result["recorded_trial_names"], [audit.trial_name(item) for item in CONFIRMATION])

    def test_forged_summary_order_rejected(self):
        manifest, summary, trials = self.fixture()
        summary["trials"][0], summary["trials"][1] = summary["trials"][1], summary["trials"][0]
        result = audit.audit_execution_order(manifest, summary, trials)
        self.assertIn("summary_order_disagrees_with_plan", result["errors"])
        self.assertTrue(any(error.startswith("hook_start_not_increasing") for error in result["errors"]))

    def test_forged_manifest_order_rejected(self):
        manifest, summary, trials = self.fixture()
        manifest["order"][0], manifest["order"][1] = manifest["order"][1], manifest["order"][0]
        self.assertIn("manifest_order_disagrees_with_plan",
                      audit.audit_execution_order(manifest, summary, trials)["errors"])

    def test_hook_time_must_exist_and_increase_strictly(self):
        for value in (None, float("nan"), 1000, 999):
            with self.subTest(value=value):
                manifest, summary, trials = self.fixture()
                trials[1]["execution"]["hook_started_at_epoch"] = value
                self.assertFalse(audit.audit_execution_order(manifest, summary, trials)["valid"])

    def test_extra_stream_or_forged_identity_rejected(self):
        manifest, summary, trials = self.fixture()
        trials[0]["execution"]["profile"] = "baseline"
        self.assertTrue(any(error.startswith("summary_and_metrics_identity_mismatch")
                            for error in audit.audit_execution_order(manifest, summary, trials)["errors"]))
        manifest, summary, trials = self.fixture()
        summary["trials"] = summary["trials"][:1]
        summary["all_planned_trials_completed"] = False
        self.assertIn("summary_and_stream_inventory_mismatch",
                      audit.audit_execution_order(manifest, summary, trials)["errors"])

    def test_partial_infrastructure_stop_is_valid_prefix_not_complete_batch(self):
        manifest, summary, trials = self.fixture()
        summary["trials"] = summary["trials"][:1]
        summary["all_planned_trials_completed"] = False
        self.assertTrue(audit.audit_execution_order(manifest, summary, trials[:1])["valid"])

    def test_old_v1_without_hook_requires_no_clock(self):
        manifest = {"repeats": 1, "order": ["baseline", "zg"], "tool_budget": None}
        actual = [{"repeat": 1, "profile": profile} for profile in manifest["order"]]
        summary = {"trials": actual, "planned_trial_count": 2, "all_planned_trials_completed": True}
        trials = [{"profile": audit.trial_name(item), "execution": {**item, "hook_present": False}}
                  for item in actual]
        result = audit.audit_execution_order(manifest, summary, trials)
        self.assertTrue(result["valid"], result["errors"])
        self.assertEqual(result["hook_clock_check"], "not_applicable_without_hook")


if __name__ == "__main__":
    unittest.main()
