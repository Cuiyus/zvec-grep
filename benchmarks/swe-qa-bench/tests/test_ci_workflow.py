from __future__ import annotations

import json
import subprocess
import sys
import tempfile
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
        candidate_checkout = next(
            step
            for step in package_job["steps"]
            if step.get("uses", "").startswith("actions/checkout@")
            and step.get("with", {}).get("path") == "candidate"
        )
        self.assertEqual(candidate_checkout["with"]["ref"], "${{ inputs.candidate_ref }}")
        build = next(
            step["run"]
            for step in package_job["steps"]
            if step.get("name") == "Build and pack the selected Rust candidate"
        )
        self.assertEqual(
            next(
                step["working-directory"]
                for step in package_job["steps"]
                if step.get("name") == "Build and pack the selected Rust candidate"
            ),
            "candidate/rust",
        )
        self.assertIn("npm run pack:local", build)
        self.assertIn("rust-package-cache.mjs create", build)
        verify = next(
            step["run"]
            for step in pair_job["steps"]
            if step.get("name") == "Verify the candidate package identity"
        )
        self.assertIn("rust-package-cache.mjs verify", verify)
        self.assertIn("needs.package-candidate.outputs.candidate-commit", verify)
        self.assertLess(
            next(i for i, step in enumerate(validate_job["steps"]) if step.get("name") == "Download the selected Rust package for preflight"),
            next(i for i, step in enumerate(validate_job["steps"]) if step.get("name") == "Verify the Harbor command without credentials"),
        )
        run_pair = next(
            step["run"]
            for step in pair_job["steps"]
            if step.get("name") == "Run baseline and zvec-grep on the same runner"
        )
        self.assertIn('--zvec-grep-package "$RUNNER_TEMP/package/candidate.tgz"', run_pair)
        self.assertNotIn('--zvec-grep-package "$GITHUB_WORKSPACE"', run_pair)

    def test_ci_scope_resolves_locked_tasks_and_five_trials_per_profile(self) -> None:
        workflow = yaml.load(
            (ROOT / ".github/workflows/swe-qa-bench.yml").read_text(),
            Loader=yaml.BaseLoader,
        )
        selection = json.loads(
            (ROOT / "benchmarks/swe-qa-bench/zg_bench/swe_qa/data/selection.json")
            .read_text()
        )
        script = next(
            step["run"]
            for step in workflow["jobs"]["validate"]["steps"]
            if step.get("id") == "task-matrix"
        )
        python_script = script.split("python - <<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
        default_scope = workflow["on"]["workflow_dispatch"]["inputs"]["scope"]["default"]
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
            with self.subTest(scope=scope), tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / "outputs"
                subprocess.run(
                    [sys.executable, "-c", python_script],
                    cwd=ROOT,
                    env={"SCOPE": scope, "GITHUB_OUTPUT": str(output)},
                    check=True,
                    capture_output=True,
                    text=True,
                )
                values = dict(line.split("=", 1) for line in output.read_text().splitlines())
                self.assertEqual(json.loads(values["task_ids_json"]), expected_ids)
                self.assertEqual(
                    json.loads(values["tasks"]),
                    [tasks_by_id[task_id]["task_slug"] for task_id in expected_ids],
                )
                count = int(values["count"])
                self.assertEqual(count, len(expected_ids))
                self.assertEqual(count * 2 * trials, len(expected_ids) * 10)
