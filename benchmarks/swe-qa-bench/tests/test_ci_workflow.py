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
        self.assertEqual(default_scope, "all-full")
        trials = int(workflow["env"]["SWE_QA_TRIALS_PER_PROFILE"])
        self.assertEqual(trials, 5)
        tasks_by_id = {task["task_id"]: task for task in selection["tasks"]}
        for scope in ("auto", default_scope, "smoke"):
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
                expected_ids = (
                    selection["gate"]["auto_tasks"]
                    if scope == "smoke"
                    else list(tasks_by_id)
                )
                self.assertEqual(json.loads(values["task_ids_json"]), expected_ids)
                self.assertEqual(
                    json.loads(values["tasks"]),
                    [tasks_by_id[task_id]["task_slug"] for task_id in expected_ids],
                )
                count = int(values["count"])
                self.assertEqual(count, 5 if scope == "smoke" else 20)
                self.assertEqual(count * 2 * trials, 50 if scope == "smoke" else 200)
