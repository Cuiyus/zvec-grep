import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from zg_bench import native_baseline_e2e as control
from zg_bench.engines.opencode.config import build_opencode_config
from zg_bench.engines.registry import resolve_opencode_model
from zg_bench.metrics.usage import LEGACY_USAGE_SCOPE


class NativeBaselineTests(unittest.TestCase):
    def test_same_frozen_inputs_as_completed_node_rust_matrix(self):
        self.assertEqual(control.validate_inputs(), control.LOCKED_INPUTS)
        self.assertEqual(control.REPETITIONS, 5)

    def test_native_profile_uses_same_model_parameters_without_mcp(self):
        for model in ("glm-5.2", "qwen3.8-max"):
            runtime = resolve_opencode_model(model).configuration
            native = build_opencode_config(runtime, profile="baseline")
            zg = build_opencode_config(runtime, profile="zvec-grep")
            self.assertNotIn("mcp", native)
            zg.pop("mcp")
            self.assertEqual(native, zg)
            command = control.command("reflex-6", model, Path("/evidence"), 1)
            self.assertEqual(command[command.index("--profile") + 1], "baseline")
            self.assertNotIn("--zvec-grep-package", command)
            self.assertNotIn("--embedding-model", command)

    def test_missing_trial_cannot_publish_partial_baseline(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ValueError, "expected one native baseline job"):
                control.collect(Path(temp), "reflex-6", "glm-5.2")

    def test_workflow_runs_exactly_200_trials_after_two_canaries(self):
        import yaml
        root = control.ROOT.parents[1]
        workflow = yaml.safe_load((root / ".github/workflows/swe-qa-bench.yml").read_text())
        self.assertEqual(set(workflow["on"]), {"workflow_dispatch"})
        matrix = next(s for s in workflow["jobs"]["validate"]["steps"] if s.get("id") == "matrix")
        script = matrix["run"].split("python - <<'PYCODE'\n", 1)[1].rsplit("PYCODE", 1)[0]
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "matrix"
            subprocess.run([sys.executable, "-c", script], cwd=root, check=True,
                env=dict(os.environ, GITHUB_OUTPUT=str(output), TASK_SCOPE="", MODEL_SCOPE="all"))
            rows = dict(line.split("=", 1) for line in output.read_text().splitlines())
        canary, remaining = [json.loads(rows[k])["include"] for k in ("canary", "remaining")]
        self.assertEqual(len(canary), 2)
        self.assertEqual(len(remaining), 38)
        self.assertEqual(len({(r["task"], r["model"]) for r in canary + remaining}), 40)
        self.assertEqual(workflow["jobs"]["remaining"]["strategy"]["max-parallel"], 8)
        self.assertEqual(workflow["jobs"]["remaining"]["needs"], ["validate", "canary"])
        for job in workflow["jobs"].values():
            first = job["steps"][0]["run"]
            self.assertIn("Cuiyus/zvec-grep", first)
            self.assertIn("BENCH_TRIGGERING_ACTOR", first)

    def test_collector_accepts_native_config_and_rejects_zg(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            result = dict(config=dict(agent=dict(name="zg_bench.agents.opencode:ResilientOpenCode",
                import_path=None, kwargs=dict(version="1.18.4", collect_session_usage=True,
                opencode_config={"provider": {}}))), agent_info=dict(name="opencode", version="1.18.4"))
            selected = []
            for repetition in range(1, 6):
                job = output / "runs" / f"r{repetition}" / "job"
                agent = job / "trial" / "agent"
                agent.mkdir(parents=True)
                (job / "result.json").write_text("{}")
                (agent / "session-usage.json").write_text(json.dumps(dict(sessions=[dict(
                    provider_models=[dict(provider_id="custom-openai", model_id="glm-5.2")])])) )
                (agent / "opencode-session-snapshot.json").write_text("[]")
                selected.append([(agent.parent, result)])
            with patch.object(control, "_completed_job"), \
                 patch.object(control, "_select_trials", side_effect=selected), \
                 patch.object(control, "_profile_result", side_effect=lambda **kwargs: kwargs["trial_index"]):
                self.assertEqual(control.collect(output, "reflex-6", "glm-5.2"), [1, 2, 3, 4, 5])
            result["config"]["agent"]["kwargs"]["opencode_config"]["mcp"] = {"zvec_grep": {}}
            with patch.object(control, "_completed_job"), \
                 patch.object(control, "_select_trials", return_value=selected[0]):
                with self.assertRaisesRegex(ValueError, "unexpectedly contains zg"):
                    control.collect(output, "reflex-6", "glm-5.2")

    def test_single_arm_judge_calls_original_rubric_once_per_trial(self):
        trials = [dict(trial_index=i, trial_name=f"trial-{i}", answer=f"answer-{i}",
            input_tokens=100, output_tokens=20, tool_calls=1, agent_wall_seconds=2.0,
            cost_usd=None, usage_scope=LEGACY_USAGE_SCOPE) for i in range(1, 6)]
        reference = dict(question="question", reference_answer="reference")
        with patch.object(control, "default_completion", return_value=object()), \
             patch.object(control, "resolve_opencode_model") as runtime, \
             patch.object(control, "judge_candidate", return_value={"total": 90}) as score:
            runtime.return_value.api_key = "test"
            runtime.return_value.configuration.base_url = "https://example.invalid"
            judged = control.judge(trials, reference, "glm-5.2")
        self.assertEqual(len(judged), 5)
        self.assertEqual(score.call_count, 5)
        self.assertEqual({c.kwargs["candidate"] for c in score.call_args_list},
                         {t["answer"] for t in trials})
        self.assertTrue(all(c.kwargs["question"] == "question" and
                            c.kwargs["reference"] == "reference" and
                            c.kwargs["attempts"] == 3 for c in score.call_args_list))


if __name__ == "__main__":
    unittest.main()
