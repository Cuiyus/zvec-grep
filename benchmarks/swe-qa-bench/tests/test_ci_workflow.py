"""The workflow must wire the selected Rust package into each paired run."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
BENCHMARK = ROOT / "benchmarks/swe-qa-bench"
WORKFLOW = ROOT / ".github/workflows/swe-qa-bench.yml"


class BenchmarkWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow = yaml.load(WORKFLOW.read_text(), Loader=yaml.BaseLoader)

    def test_selected_rust_package_reaches_each_pair(self) -> None:
        package = self.workflow["jobs"]["package-candidate"]
        pair = self.workflow["jobs"]["run-pair"]
        build = next(
            step for step in package["steps"] if step.get("id") == "rust-candidate"
        )
        self.assertEqual(build["uses"], "./.github/actions/rust-candidate-package")
        self.assertEqual(build["with"]["candidate_ref"], "${{ inputs.candidate_ref }}")
        self.assertEqual(
            package["outputs"]["candidate-commit"],
            "${{ steps.rust-candidate.outputs.commit }}",
        )
        self.assertIn("package-candidate", pair["needs"])
        pair_commands = "\n".join(step.get("run", "") for step in pair["steps"])
        self.assertIn("rust-package-cache.mjs verify", pair_commands)
        self.assertIn("needs.package-candidate.outputs.candidate-commit", pair_commands)
        self.assertIn(
            '--zvec-grep-package "$RUNNER_TEMP/package/candidate.tgz"', pair_commands
        )

    def test_task_scope_and_trial_count_use_versioned_data(self) -> None:
        config = json.loads((BENCHMARK / "ci-config.json").read_text())
        self.assertEqual(config["trials_per_profile"], 5)
        self.assertEqual(config["max_retries"], 2)
        self.assertEqual(config["embedding_model"], "local/potion-code-16m-v2")
        workflow_text = WORKFLOW.read_text()
        self.assertIn("benchmarks/swe-qa-bench/ci-config.json", workflow_text)
        self.assertNotIn("SWE_QA_TRIALS_PER_PROFILE", workflow_text)
        self.assertNotIn("SWE_QA_MAX_RETRIES", workflow_text)

        selection_path = BENCHMARK / "zg_bench/swe_qa/data/selection.json"
        selection = json.loads(selection_path.read_text())
        slugs_by_id = {
            task["task_id"]: task["task_slug"] for task in selection["tasks"]
        }
        all_ids = list(slugs_by_id)
        scopes = {
            "repro-3": ["reflex:6", "requests:16", "conan:39"],
            "smoke": selection["gate"]["auto_tasks"],
            "all-full": all_ids,
            "gate-20": all_ids,
        }
        for scope, expected_ids in scopes.items():
            with self.subTest(scope=scope):
                result = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "zg_bench.swe_qa",
                        "matrix",
                        "--selection",
                        str(selection_path),
                        "--scope",
                        scope,
                    ],
                    cwd=ROOT,
                    env={**os.environ, "PYTHONPATH": str(BENCHMARK)},
                    check=True,
                    capture_output=True,
                    text=True,
                )
                outputs = dict(
                    line.split("=", 1) for line in result.stdout.splitlines()
                )
                self.assertEqual(json.loads(outputs["task_ids_json"]), expected_ids)
                self.assertEqual(int(outputs["count"]), len(expected_ids))
                self.assertEqual(
                    json.loads(outputs["tasks"]),
                    [slugs_by_id[task_id] for task_id in expected_ids],
                )
