"""Repetition boundaries and unchanged native bodies; no provider or Docker."""
import copy
import inspect
import json
import os
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import repeat_live_v1 as driver
import test_expansion_identity_v1 as identity_tests


class RepeatLiveTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.args = SimpleNamespace(plan=self.root / "plan.json", task_id="q001", repetition=1,
            main_root=self.root / "main", reference_prepared=self.root / "reference",
            prepared_group=self.root / "reserves", candidate=self.root / "candidate.tgz",
            output=self.root / "run", command="run")
        self.references = {"group_sha256": "group", "metadata_sha256": {}, "source_manifest_sha256": {}}
        datasets = [driver.live.pilot.load(driver.REPO / "benchmarks/zvec-issue-qa/dataset.json"),
                    driver.live.pilot.load(driver.REPO / "benchmarks/zvec-issue-qa/dataset-expansion-v1.json")]
        self.tasks = [row for dataset in datasets for row in dataset["tasks"]]
        self.metadata = {}
        for task in self.tasks:
            self.metadata[task["id"]] = {key: task[key] for key in
                ("source_commit", "source_file_count", "source_inventory_sha256")}
            for field, stored in (("instruction", "question"), ("evaluator", "rubric")):
                relative = "benchmarks/zvec-issue-qa/" + task[field]
                target = self.repo / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(driver.REPO / relative, target)
                self.metadata[task["id"]][stored] = {"path": task[field], "sha256": task[field + "_sha256"]}
        base_path = self.repo / driver.BASE_PLAN
        base_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(driver.REPO / driver.BASE_PLAN, base_path)
        self.base, self.proof = driver.live.pilot.load(base_path), {"synthetic": True}
        for relative in driver.CONTROLS - {driver.DECISION}:
            target = self.repo / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(driver.REPO / relative, target)
        self.decision = {"protocol_id": driver.PROTOCOL, "frozen": True, "paid_execution_authorized": True,
            "status": "approved_for_fixed_current_task_repetitions", "task_ids": list(driver.preparation.TASK_IDS),
            "scheduled_pairs": 30, "scheduled_solver_trials": 60}
        driver.live.pilot.write(self.repo / driver.DECISION, self.decision)
        self.plan = {"schema_version": 1, "protocol_id": driver.PROTOCOL, "frozen": True,
            "base_plan": {"path": driver.BASE_PLAN, "sha256": driver.BASE_PLAN_SHA256},
            "current_task_ids": list(driver.preparation.TASK_IDS), "tasks": self.tasks,
            "repetitions_per_task_profile": 10, "n_attempts_per_pair": 1, "max_retries": 0,
            "scheduled_pairs": 30, "scheduled_solver_trials": 60, "max_parallel_pairs": 3,
            "schedule_seed": driver.SEED, "schedule": driver.schedule(), "prepared": self.references,
            "statistics": {"trim_policy": driver.TRIM_POLICY, "task_screen_policy": driver.TASK_SCREEN_POLICY,
                "bootstrap": {"seed": driver.BOOTSTRAP_SEED, "resamples": driver.BOOTSTRAP_RESAMPLES,
                    "confidence_level": 0.95, "unit": "original within-task baseline/zg pair", "task_resampling": False}},
            "execution_workflow": driver.WORKFLOW, "execution_decision": driver.DECISION,
            "scope_policy": {"path": driver.SCOPE_POLICY, "sha256": driver.live.pilot.digest(self.repo / driver.SCOPE_POLICY)},
            "upstream_verification": {"current_main": driver.UPSTREAM_SHA, "rust_tree_sha": driver.RUST_TREE},
            "grading_contracts": [{"path": relative, "sha256": driver.live.pilot.digest(self.repo / relative)}
                for relative in [driver.PREFIX + "grader-contract-v1.json", driver.PREFIX + "expansion-grader-contract-v1.json"]]}
        self.pin()

    def pin(self):
        self.plan["frozen_harness_sha256"] = {p: driver.live.pilot.digest(self.repo / p) for p in driver.CONTROLS}
        driver.live.pilot.write(self.args.plan, self.plan)

    def selected(self, group, reference, task, repo):
        return self.metadata[task], [], reference if task == "q001" else group / task

    def validate(self):
        with patch.object(driver.live, "validate_plan", return_value=(self.base, self.proof)), \
             patch.object(driver.preparation, "references", return_value=self.references), \
             patch.object(driver.preparation, "selected_inputs", side_effect=self.selected):
            return driver.validate_plan(self.args, self.repo)

    def execute(self):
        return driver.execute(self.args, self.plan, self.base, self.proof, self.repo)

    def test_fixed_schedule_balances_orders_without_replacements(self):
        rows = driver.schedule()
        self.assertEqual(len(rows), 30)
        self.assertEqual(len({row["key"] for row in rows}), 30)
        self.assertEqual(rows, driver.schedule())
        for task in driver.preparation.TASK_IDS:
            selected = [row for row in rows if row["task_id"] == task]
            self.assertEqual({row["trial_index"] for row in selected}, set(range(1, 11)))
            self.assertEqual(sum(row["profiles"][0] == "baseline" for row in selected), 5)
        for task, index in (("q004", 1), ("q001", 0), ("q002", 11), ("q003", True)):
            with self.assertRaises(ValueError):
                driver.selected_pair(task, index)

    def test_all_current_tasks_and_extreme_repeat_indexes_validate(self):
        for task in driver.preparation.TASK_IDS:
            for index in (1, 10):
                self.args.task_id, self.args.repetition = task, index
                self.assertEqual(self.validate(), (self.plan, self.base, self.proof))

    def test_draft_runtime_overrides_source_and_statistical_changes_are_rejected(self):
        original = copy.deepcopy(self.plan)
        mutations = [lambda p: p.update(frozen=False), lambda p: p.update(provider={}),
            lambda p: p.update(n_attempts_per_pair=True), lambda p: p.update(max_retries=1),
            lambda p: p.update(repetitions_per_task_profile=9),
            lambda p: p["schedule"][0]["profiles"].reverse(),
            lambda p: p["tasks"][0].update(source_commit="0" * 40),
            lambda p: p["statistics"]["task_screen_policy"].update(minimum_tasks=3),
            lambda p: p["statistics"]["bootstrap"].update(resamples=100),
            lambda p: p.update(frozen_harness_sha256={}),
            lambda p: p["scope_policy"].update(sha256="changed"),
            lambda p: p["upstream_verification"].update(rust_tree_sha="changed")]
        for mutate in mutations:
            self.plan = copy.deepcopy(original)
            mutate(self.plan)
            driver.live.pilot.write(self.args.plan, self.plan)
            with self.subTest(mutate=mutate), self.assertRaises(ValueError):
                self.validate()

    def test_paid_authorization_is_required_separately(self):
        self.decision["paid_execution_authorized"] = False
        driver.live.pilot.write(self.repo / driver.DECISION, self.decision)
        self.pin()
        with self.assertRaisesRegex(ValueError, "execution_decision_missing"):
            self.validate()

    def test_native_function_bodies_and_original_globals_are_unchanged(self):
        for task in driver.preparation.TASK_IDS:
            for index in (1, 2):
                pair = driver.selected_pair(task, index)
                module = driver.private_live(pair)
                self.assertEqual(module.PROFILES, pair["profiles"])
                for name, original in vars(driver.live).items():
                    if inspect.isfunction(original) and original.__module__ == driver.live.__name__:
                        self.assertEqual(inspect.getsource(getattr(module, name)), inspect.getsource(original), name)
        self.assertEqual(driver.live.PROTOCOL, "native-claude-q001-live-v2")
        self.assertEqual(driver.live.PROFILES, ["baseline", "zvec-grep"])

    def test_actual_dry_loop_selects_q001_reference_and_each_reserve(self):
        self.args.command = "dry-run"
        for task in driver.preparation.TASK_IDS:
            for index in (1, 2):
                self.args.task_id, self.args.repetition = task, index
                pair = driver.selected_pair(task, index)
                self.args.output = self.root / pair["key"]
                expected = self.args.reference_prepared if task == "q001" else self.args.prepared_group / task
                module = driver.private_live(pair)
                def suite(path):
                    self.assertEqual(path, expected / "suite.yaml")
                    return SimpleNamespace(tasks=("zvec-" + task,))
                def command(runner, selected_suite, profile, prepared, *_):
                    self.assertEqual(prepared, expected)
                    return ["harbor", "--include-task-name", selected_suite.tasks[0], "--profile", profile]
                with patch.object(driver, "private_live", return_value=module), \
                     patch.object(module.pilot, "native", return_value=SimpleNamespace(load_suite=suite)), \
                     patch.object(module, "build_command", side_effect=command), \
                     patch.object(module, "run_slot", side_effect=AssertionError("no provider")), \
                     patch.dict(os.environ, {}, clear=True):
                    self.assertEqual(self.execute(), 0)
                    with self.assertRaises(FileExistsError):
                        self.execute()
                record = driver.live.pilot.load(self.args.output / "execution.json")
                context = driver.live.pilot.load(driver.context_path(self.args.output))
                self.assertEqual([row["profile"] for row in record["slots"]], pair["profiles"])
                self.assertEqual(context["recorded_prepared_root"], expected.resolve().as_posix())
                self.assertEqual(context["trial_index"], index)

    def test_dry_context_cannot_be_collected_as_real_evidence(self):
        self.args.command = "dry-run"
        with patch.object(driver.private_live(driver.selected_pair("q001", 1)).pilot, "native"), \
             patch.object(driver, "private_live") as module:
            module.return_value.run_pair.return_value = {"slots": []}
            self.assertEqual(self.execute(), 0)
        self.args.command = "collect"
        with self.assertRaisesRegex(ValueError, "repeat_context_mismatch"):
            self.execute()

    def test_cleanup_failure_stops_second_profile_without_retry(self):
        self.args.task_id, self.args.repetition = "q001", 2
        module = driver.private_live(driver.selected_pair("q001", 2))
        failed = {"profile": "zvec-grep", "status": "exited", "returncode": 0,
                  "process_cleanup": {"complete": False}, "container_cleanup": {"complete": True}}
        with patch.object(driver, "private_live", return_value=module), \
             patch.object(module, "docker_info", return_value={}), \
             patch.object(module, "run_slot", return_value=failed) as slot, \
             patch.dict(os.environ, {"ANTHROPIC_API_KEY": "synthetic", "ZVEC_GREP_API_KEY": "synthetic"}, clear=True):
            self.assertEqual(self.execute(), 1)
        self.assertEqual(slot.call_count, 1)
        record = driver.live.pilot.load(self.args.output / "execution.json")
        self.assertEqual(record["slots"][1]["status"], "not_started")

    def test_reused_identity_checker_accepts_existing_q002_fixture(self):
        fixture = identity_tests.IdentityTests("runTest")
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        result = driver.identity.verify(fixture.fixture.output, "q002", fixture.output,
            reference_prepared=fixture.fixture.reference, recorded_prepared_root=fixture.remote,
            expected_profiles=["baseline", "zvec-grep"], repo_root=fixture.fixture.repo)
        self.assertTrue(result["task_identity_valid"], result["errors"])


if __name__ == "__main__":
    unittest.main()
