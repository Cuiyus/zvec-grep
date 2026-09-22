from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]


class BenchmarkWorkflowTests(unittest.TestCase):
    def test_rust_candidate_is_built_once_and_verified_for_each_pair(self) -> None:
        workflow = yaml.load(
            (ROOT / ".github/workflows/swe-qa-bench.yml").read_text(),
            Loader=yaml.BaseLoader,
        )
        dispatch = workflow["on"]["workflow_dispatch"]
        self.assertEqual(dispatch["inputs"]["candidate_ref"]["default"], "main")
        package_job = workflow["jobs"]["package-candidate"]
        validate_job = workflow["jobs"]["validate"]
        pair_job = workflow["jobs"]["run-pair"]
        self.assertEqual(validate_job["needs"], "package-candidate")
        self.assertIn("package-candidate", pair_job["needs"])
        package_artifact = next(
            step
            for step in package_job["steps"]
            if step.get("uses", "").startswith("actions/upload-artifact@")
        )
        self.assertEqual(
            package_artifact["with"]["name"],
            "swe-qa-rust-candidate-${{ github.run_id }}",
        )
        self.assertEqual(package_artifact["with"]["overwrite"], "true")
        build_step = next(
            step for step in package_job["steps"] if step.get("id") == "rust-candidate"
        )
        self.assertEqual(build_step["uses"], "./.github/actions/rust-candidate-package")
        self.assertEqual(
            build_step["with"]["candidate_ref"], "${{ inputs.candidate_ref }}"
        )
        self.assertEqual(build_step["with"]["cache_namespace"], "swe-qa-rust")
        self.assertEqual(
            package_job["outputs"]["candidate-commit"],
            "${{ steps.rust-candidate.outputs.commit }}",
        )
        action = yaml.load(
            (ROOT / ".github/actions/rust-candidate-package/action.yml").read_text(),
            Loader=yaml.BaseLoader,
        )
        action_steps = action["runs"]["steps"]
        candidate_checkout = next(
            step
            for step in action_steps
            if step.get("uses", "").startswith("actions/checkout@")
        )
        self.assertEqual(
            candidate_checkout["with"]["ref"], "${{ inputs.candidate_ref }}"
        )
        build = next(
            step
            for step in action_steps
            if step.get("name") == "Build and pack the selected Rust candidate"
        )
        self.assertEqual(build["working-directory"], "candidate/rust")
        self.assertIn("npm run pack:local", build["run"])
        self.assertIn("benchmarks/shared/rust-package-cache.mjs", build["run"])
        self.assertIn("--source ..", build["run"])
        verify = next(
            step["run"]
            for step in pair_job["steps"]
            if step.get("name") == "Verify the candidate package identity"
        )
        self.assertIn("rust-package-cache.mjs verify", verify)
        self.assertIn("needs.package-candidate.outputs.candidate-commit", verify)
        self.assertLess(
            next(
                i
                for i, step in enumerate(validate_job["steps"])
                if step.get("name")
                == "Download the selected Rust package for preflight"
            ),
            next(
                i
                for i, step in enumerate(validate_job["steps"])
                if step.get("name") == "Verify the Harbor command without credentials"
            ),
        )
        run_pair = next(
            step["run"]
            for step in pair_job["steps"]
            if step.get("name") == "Run baseline and zvec-grep on the same runner"
        )
        self.assertIn(
            '--zvec-grep-package "$RUNNER_TEMP/package/candidate.tgz"', run_pair
        )
        self.assertNotIn('--zvec-grep-package "$GITHUB_WORKSPACE"', run_pair)

    def test_ci_scope_resolves_locked_tasks_and_five_trials_per_profile(self) -> None:
        workflow = yaml.load(
            (ROOT / ".github/workflows/swe-qa-bench.yml").read_text(),
            Loader=yaml.BaseLoader,
        )
        selection = json.loads(
            (
                ROOT / "benchmarks/swe-qa-bench/zg_bench/swe_qa/data/selection.json"
            ).read_text()
        )
        matrix_step = next(
            step
            for step in workflow["jobs"]["validate"]["steps"]
            if step.get("id") == "task-matrix"
        )
        self.assertIn("python -m zg_bench.swe_qa matrix", matrix_step["run"])
        self.assertIn('--scope "$SCOPE"', matrix_step["run"])
        selection_path = (
            ROOT / "benchmarks/swe-qa-bench/zg_bench/swe_qa/data/selection.json"
        )
        default_scope = workflow["on"]["workflow_dispatch"]["inputs"]["scope"][
            "default"
        ]
        self.assertEqual(default_scope, "repro-3")
        trials = int(workflow["env"]["SWE_QA_TRIALS_PER_PROFILE"])
        self.assertEqual(trials, 5)
        tasks_by_id = {task["task_id"]: task for task in selection["tasks"]}
        scopes = {
            "all-full": list(tasks_by_id),
            "gate-20": list(tasks_by_id),
            "smoke": selection["gate"]["auto_tasks"],
            "repro-3": ["reflex:6", "requests:16", "conan:39"],
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
                    env={
                        **os.environ,
                        "PYTHONPATH": str(ROOT / "benchmarks/swe-qa-bench"),
                    },
                    check=True,
                    capture_output=True,
                    text=True,
                )
                values = dict(line.split("=", 1) for line in result.stdout.splitlines())
                self.assertEqual(json.loads(values["task_ids_json"]), expected_ids)
                self.assertEqual(
                    json.loads(values["tasks"]),
                    [tasks_by_id[task_id]["task_slug"] for task_id in expected_ids],
                )
                count = int(values["count"])
                self.assertEqual(count, len(expected_ids))
                self.assertEqual(count * 2 * trials, len(expected_ids) * 10)
