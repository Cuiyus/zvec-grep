"""Integration contracts for the native benchmark driver; no Docker/model calls."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from zg_bench.swe_qa import e2e_stability as es
from zg_bench.swe_qa import native_benchmark as nb
from zg_bench.swe_qa import prompt_diagnostics as pd
from zg_bench.swe_qa.readonly_judge import _plan as judge_plan


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2))


CASE = {"case_id": "case", "question": "Explain the relevant implementation.",
        "repo": {"url": "https://example.invalid/repo.git", "commit": "abc123"}}
CONVERSION = {"error_event_count": 0, "contract_error_count": 0, "has_final_answer": True,
              "parse": {"invalid_json_lines": []}}


class NativeBenchmarkTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.case_path = self.root / "case.json"
        self.selection_path = self.root / "selection.json"
        dump(self.case_path, CASE)
        dump(self.selection_path, {"ready": True, "candidate": None,
                                   "status": "retain_current_insufficient_evidence",
                                   "prompt_runtime_overrides": nb.frozen_overrides(None, CASE),
                                   "case_sha256": nb.sha256(self.case_path)})
        self.args = argparse.Namespace(output=self.root / "run", case=self.case_path,
            group="opencode-glm52", selection=self.selection_path, image="native:test",
            judge_case=self.root / "judge.json", entries=self.root / "entries.json")
        dump(self.args.entries, {})

    def checkout(self, case, destination):
        destination.mkdir(parents=True)
        (destination / ".zvec-grep").mkdir()
        (destination / "source.py").write_text("def evidence(): pass\n")
        return destination

    def completed_launch(self, **kwargs):
        logs = kwargs["logs"]
        dump(logs / "session.json", {"status": "completed", "observed": {
            "input_tokens": 123, "tool_calls": 3, "invalid_usage_events": 0}})
        dump(logs / "install-manifest.json", {"integration": "released zg install"})
        return 0

    def test_runtime_specs_pin_actual_models_temperature_and_search_only_catalog(self):
        for group, (agent, model) in nb.GROUPS.items():
            for variant in ("P00", "P10", "P01", "P11"):
                spec, runtime = nb.runtime_spec(group, CASE, zg=True, variant=variant)
                self.assertEqual(spec.provider_model, model)
                self.assertEqual(runtime["model"], model)
                self.assertEqual(runtime["arm"], "zg")
                self.assertEqual(runtime["limits"], nb.LIMITS)
                self.assertEqual("guidance_override" in runtime, variant in {"P10", "P11"})
                self.assertEqual("description_overrides" in runtime, variant in {"P01", "P11"})
                if "description_overrides" in runtime:
                    self.assertEqual(set(runtime["description_overrides"]), {"zvec_grep_search"})
                self.assertNotIn("zvec_grep_rg", " ".join(nb.native_tools(agent, True)))
                if agent == "opencode":
                    self.assertEqual(runtime["base_config"]["agent"]["build"]["temperature"], 0)
                    self.assertEqual(runtime["base_config"]["agent"]["build"]["options"]["seed"], nb.MODEL_SEED)
                    self.assertEqual(runtime["model_seed"], nb.MODEL_SEED)
            _, baseline = nb.runtime_spec(group, CASE, zg=False)
            self.assertEqual(baseline["arm"], "baseline")
            self.assertNotIn("guidance_override", baseline)
            self.assertNotIn("description_overrides", baseline)

    def test_unknown_variant_is_rejected_before_it_can_create_fake_candidate_arm(self):
        with self.assertRaises(ValueError):
            nb.runtime_spec("opencode-glm52", CASE, zg=True, variant="Pxx")

    def test_wire_contract_requires_the_fixed_seed_on_every_task_request(self):
        directory = self.root / "wire"
        directory.mkdir()
        expected = nb.native_tools("opencode", False)
        rows = [
            {"event": "request", "request_id": 1, "model": "glm-5.2", "temperature": 0,
             "seed": nb.MODEL_SEED, "tool_names": expected},
            {"event": "response", "request_id": 1, "model": "glm-5.2", "status": 200},
        ]
        (directory / "wire.jsonl").write_text("\n".join(map(json.dumps, rows)) + "\n")
        contract = nb.wire_contract(directory, "glm-5.2", expected, expected_seed=nb.MODEL_SEED)
        self.assertTrue(contract["valid"])
        rows[0].pop("seed")
        (directory / "wire.jsonl").write_text("\n".join(map(json.dumps, rows)) + "\n")
        contract = nb.wire_contract(directory, "glm-5.2", expected, expected_seed=nb.MODEL_SEED)
        self.assertFalse(contract["valid"])
        self.assertTrue(contract["configuration_mismatch"])

    def test_no_promotion_runs_ten_each_baseline_current_and_fresh_workspaces(self):
        calls = []
        def launch(**kwargs):
            calls.append(kwargs)
            return self.completed_launch(**kwargs)
        with patch.object(nb, "source_checkout", side_effect=self.checkout), patch.object(nb, "launch", side_effect=launch), \
             patch.object(nb, "convert_agent_trace", return_value=CONVERSION), patch.object(nb, "wire_contract", return_value={"valid": True}), \
             patch("sys.stdout", new=io.StringIO()):
            self.assertEqual(nb.run_group(self.args), 0)
        report = json.loads((self.args.output / "e2e-stability.json").read_text())
        self.assertEqual(report["planned_trials"], 20)
        self.assertEqual(report["observed_trials"], 20)
        self.assertEqual(set(report["arms"]), {"B", "C"})
        self.assertEqual(len({str(call["workspace"]) for call in calls}), 20)
        self.assertEqual(sum(call["spec"]["arm"] == "zg" for call in calls), 10)
        self.assertEqual({call["spec"]["model_seed"] for call in calls}, {nb.MODEL_SEED})
        self.assertTrue(all(call["spec"]["prompt_variant"] == "P00" for call in calls))
        self.assertEqual(report["comparisons"]["C-B"]["metrics"]["input_tokens"]["eligible_pairs"], 0)
        self.assertEqual(report["arms"]["C"]["cost"]["input_tokens"]["observed_all_statuses"]["values"], [123] * 10)
        manifest = json.loads((self.args.output / "manifest.json").read_text())
        self.assertEqual(manifest["package"], "@zvec/zvec-grep@0.2.2")
        self.assertEqual(manifest["embedding"], "local/potion-code-16m-v2")
        self.assertIn("fresh independent", manifest["index_policy"])

    def test_setup_contract_failure_stops_spend_and_preserves_all_planned_rows(self):
        def failed(**kwargs):
            kwargs["logs"].mkdir(parents=True)
            return 2
        with patch.object(nb, "source_checkout", side_effect=self.checkout), patch.object(nb, "launch", side_effect=failed) as launch, \
             patch.object(nb, "convert_agent_trace", return_value=CONVERSION), patch.object(nb, "wire_contract", return_value={"valid": True}), \
             patch("sys.stdout", new=io.StringIO()):
            self.assertEqual(nb.run_group(self.args), 1)
        self.assertEqual(launch.call_count, 1)
        report = json.loads((self.args.output / "e2e-stability.json").read_text())
        self.assertEqual(report["planned_trials"], 20)
        self.assertEqual(report["observed_trials"], 1)
        self.assertEqual(report["execution_counts"]["missing"], 19)

    def test_selection_must_be_ready_before_any_external_work(self):
        dump(self.selection_path, {"ready": False, "candidate": "P10"})
        with patch.object(nb, "source_checkout") as checkout, patch.object(nb, "launch") as launch:
            with self.assertRaises(ValueError):
                nb.run_group(self.args)
        checkout.assert_not_called()
        launch.assert_not_called()

    def test_confirmation_rejects_changed_prompt_or_different_case_before_launch(self):
        for change in ("prompt", "case"):
            with self.subTest(change=change):
                self.args.output = self.root / ("run-" + change)
                value = {"ready": True, "candidate": "P10", "case_sha256": nb.sha256(self.case_path),
                         "prompt_runtime_overrides": nb.frozen_overrides("P10", CASE)}
                if change == "prompt":
                    value["prompt_runtime_overrides"] = {"changed": "text"}
                else:
                    value["case_sha256"] = "other-case"
                dump(self.selection_path, value)
                with patch.object(nb, "source_checkout") as checkout, patch.object(nb, "launch") as launch:
                    with self.assertRaises(ValueError):
                        nb.run_group(self.args)
                checkout.assert_not_called()
                launch.assert_not_called()

    def test_launcher_uses_isolated_writable_copy_and_preserves_source(self):
        source = self.checkout(CASE, self.root / "corpus")
        _, runtime = nb.runtime_spec("opencode-glm52", CASE, zg=True)
        process = Mock()
        process.wait.return_value = 0
        secret = "very-private-token-never-serialize"
        with patch.dict(os.environ, {"GLM_API_KEY": secret}), patch.object(nb.subprocess, "Popen", return_value=process) as popen:
            code = nb.launch(image="native:test", source=source, logs=self.root / "logs", cache=self.root / "cache",
                workspace=self.root / "workspace", spec=runtime, credential="GLM_API_KEY")
        self.assertEqual(code, 0)
        command = popen.call_args.args[0]
        self.assertNotIn(secret, json.dumps(command))
        self.assertIn("OPENAI_API_KEY", command)
        self.assertEqual(popen.call_args.kwargs["env"]["OPENAI_API_KEY"], secret)
        source_mount = next(value for value in command if "target=/app" in value)
        self.assertNotIn("readonly", source_mount)
        self.assertIn("workspace/source", source_mount)
        self.assertTrue((self.root / "workspace/index").is_dir())
        self.assertEqual((source / "source.py").read_text(), "def evidence(): pass\n")
        self.assertEqual(json.loads((self.root / "logs/source-integrity.json").read_text())["status"], "unchanged")
        for file in (self.root / "logs").glob("*.json"):
            self.assertNotIn(secret, file.read_text())

    def test_launcher_fails_a_session_that_changes_qa_source(self):
        source = self.checkout(CASE, self.root / "corpus")
        _, runtime = nb.runtime_spec("opencode-glm52", CASE, zg=False)
        process = Mock()
        def mutate(*, timeout):
            self.assertEqual(timeout, 3000)
            (self.root / "workspace/source/source.py").write_text("changed\n")
            return 0
        process.wait.side_effect = mutate
        with patch.object(nb.subprocess, "Popen", return_value=process):
            code = nb.launch(image="native:test", source=source, logs=self.root / "logs", cache=self.root / "cache",
                             workspace=self.root / "workspace", spec=runtime)
        self.assertEqual(code, 86)
        self.assertEqual((source / "source.py").read_text(), "def evidence(): pass\n")
        self.assertEqual(json.loads((self.root / "logs/source-integrity.json").read_text())["status"], "changed")

    def test_trial_result_uses_metrics_expected_by_pair_analysis_and_preserves_unknown_usage(self):
        trial = es.make_plan("case", candidate=None)["trials"][0]
        agent_dir = self.root / "agent"
        dump(agent_dir / "session.json", {"status": "completed", "observed": {"input_tokens": None, "tool_calls": 4}})
        spec, _ = nb.runtime_spec("opencode-glm52", CASE, zg=trial["arm"] != "B")
        with patch.object(nb, "convert_agent_trace", return_value=CONVERSION), patch.object(nb, "wire_contract", return_value={"valid": True}):
            row = nb.trial_result(trial, agent_dir, spec, "instruction", 0, True)
        self.assertEqual(row["metrics"], {"input_tokens": None, "tool_calls_attempted": 4})
        self.assertFalse(row["usage_complete"])
        self.assertTrue(row["tools_complete"])

    def test_source_change_cannot_be_reported_as_success(self):
        trial = es.make_plan("case", candidate=None)["trials"][0]
        self.completed_launch(logs=self.root / "agent")
        spec, _ = nb.runtime_spec("opencode-glm52", CASE, zg=trial["arm"] != "B")
        with patch.object(nb, "convert_agent_trace", return_value=CONVERSION), patch.object(nb, "wire_contract", return_value={"valid": True}):
            row = nb.trial_result(trial, self.root / "agent", spec, "instruction", 0, False)
        self.assertEqual(row["execution_status"], "source_integrity_failure")

    def test_partial_mcp_log_is_a_visible_measurement_failure_not_uncaught_exception(self):
        trial = next(t for t in es.make_plan("case", candidate=None)["trials"] if t["arm"] == "C")
        agent_dir = self.root / "agent"
        self.completed_launch(logs=agent_dir)
        (agent_dir / "native-mcp.jsonl").write_text('{"direction":"agent_to_zg","message":')
        spec, _ = nb.runtime_spec("opencode-glm52", CASE, zg=True)
        with patch.object(nb, "convert_agent_trace", return_value=CONVERSION), patch.object(nb, "wire_contract", return_value={"valid": True}):
            row = nb.trial_result(trial, agent_dir, spec, "instruction", 0, True)
        self.assertEqual(row["execution_status"], "measurement_failure")
        self.assertIsNone(row["behavior"]["zg_adopted"])

    def quality_fixture(self, candidate="P10"):
        output = self.args.output
        output.mkdir()
        plan = es.make_plan("case", candidate=candidate)
        dump(output / "plan.json", plan)
        rows = []
        for trial in plan["trials"]:
            dump(output / trial["trajectory_path"], {"trial_id": trial["trial_id"], "answer": "source-based answer"})
            rows.append({"trial_id": trial["trial_id"], "execution_status": "completed", "usage_complete": True,
                         "tools_complete": True, "metrics": {"input_tokens": 100, "tool_calls_attempted": 2}})
        dump(output / "results.json", {"trials": rows})
        dump(output / "manifest.json", {"controls": {"temperature": 0}})
        return plan

    def test_quality_compatibility_view_preserves_ids_and_keeps_three_arm_statistics(self):
        plan = self.quality_fixture()
        def review(**kwargs):
            self.assertIsNone(kwargs["expected_per_profile"])
            rows = judge_plan(kwargs["runs_dir"], "case")
            self.assertEqual({r["profile"] for r in rows}, {"baseline", "zvec-grep"})
            self.assertEqual({r["trial_id"] for r in rows}, {r["trial_id"] for r in plan["trials"]})
            self.assertTrue(all(r["path"].is_file() for r in rows))
            return {"trials": [{"trial_id": row["trial_id"], "consensus_status": "pass"} for row in rows]}
        with patch("zg_bench.swe_qa.quality_review.review_runs", side_effect=review):
            nb.review_group(self.args)
        original = json.loads((self.args.output / "plan.json").read_text())
        self.assertEqual(original, plan)
        report = json.loads((self.args.output / "e2e-stability.json").read_text())
        self.assertEqual(set(report["arms"]), {"B", "C", "N"})
        self.assertEqual(report["comparisons"]["N-C"]["metrics"]["input_tokens"]["eligible_pairs"], 10)

    def test_quality_view_rejects_path_escape_before_copy_or_judge(self):
        plan = self.quality_fixture(candidate=None)
        outside = self.root / "outside.json"
        dump(outside, {"untouched": True})
        plan["trials"][0]["trajectory_path"] = "../outside.json"
        dump(self.args.output / "plan.json", plan)
        before = outside.read_bytes()
        with patch("zg_bench.swe_qa.quality_review.review_runs") as review:
            with self.assertRaises(ValueError):
                nb.review_group(self.args)
        review.assert_not_called()
        self.assertEqual(outside.read_bytes(), before)
        self.assertFalse((self.args.output / "outside.json").exists())

    def test_initial_capture_failure_stops_before_any_diagnostic_or_annotation_model_call(self):
        def failed(**kwargs):
            return 2
        with patch.object(nb, "source_checkout", side_effect=self.checkout), patch.object(nb, "launch", side_effect=failed), \
             patch.object(pd, "run_plan") as model, patch.object(nb, "annotate") as annotate:
            with self.assertRaises(ValueError):
                nb.screen(self.args)
        model.assert_not_called()
        annotate.assert_not_called()

    def test_screen_empty_decisions_retains_current_with_only_opencode_groups(self):
        def capture(**kwargs):
            directory, runtime = kwargs["logs"], kwargs["spec"]
            name = nb.native_tools("opencode", True)[-1]
            body = {"model": runtime["model"], "temperature": 0, "seed": nb.MODEL_SEED, "stream": True,
                    "messages": [{"role": "system", "content": "ORIGINAL GUIDANCE"}, {"role": "user", "content": CASE["question"]}],
                    "tools": [{"type": "function", "function": {"name": name, "description": "native search", "parameters": {"type": "object"}}}]}
            raw = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()
            dump(directory / "wire-requests/request-001.json", body)
            (directory / "wire-requests/request-001.raw.json").write_bytes(raw)
            event = {"event": "request", "request_id": 1, "model": runtime["model"], "temperature": 0,
                     "seed": nb.MODEL_SEED, "tool_names": [name], "request_sha256": hashlib.sha256(raw).hexdigest()}
            (directory / "wire.jsonl").write_text(json.dumps(event) + "\n")
            dump(directory / "session-spec.json", {"tap_upstream": runtime["tap_upstream"]})
            dump(directory / "native-install.json", {"installed_guidance_text": "ORIGINAL GUIDANCE"})
            dump(directory / "capture-manifest.json", {"status": "captured", "capture_only": True,
                "installed_guidance_verified": True, "intended_endpoint": runtime["tap_upstream"], "paid_model_calls": 0})
            return 0
        def annotate(**kwargs):
            dump(kwargs["output"] / "query-intents.json", {})
        def evidence(plan_path, *args):
            plan = pd.load_plan(plan_path)
            return {"plan_sha256": plan["plan_sha256"], "assessment_frozen": True, "rows": []}
        with patch.object(nb, "source_checkout", side_effect=self.checkout), patch.object(nb, "launch", side_effect=capture), \
             patch.object(pd, "run_plan", return_value={}) as model, patch.object(nb, "annotate", side_effect=annotate), \
             patch.object(nb, "replay_catalog", return_value=[]), \
             patch("zg_bench.swe_qa.native_query_diagnosis.build_decision_catalog", return_value={"request_catalog": []}), \
             patch("zg_bench.swe_qa.native_query_diagnosis.screening_evidence", side_effect=evidence):
            self.assertEqual(nb.screen(self.args), 0)
        self.assertEqual(model.call_count, 2)
        plan = pd.load_plan(self.args.output / "decisions/plan.json")
        self.assertEqual(len(plan["samples"]), 80)
        selected = json.loads((self.args.output / "selection.json").read_text())
        self.assertIsNone(selected["candidate"])
        self.assertEqual(selected["status"], "retain_current_insufficient_evidence")
        self.assertFalse(selected["decision_sampling_complete"])
        self.assertEqual(selected["unavailable"], [])
        self.assertEqual(selected["confirmation_trials_per_arm"], 10)

    def capture_module(self):
        path = Path(__file__).resolve().parents[1] / "scripts/capture-native-state.py"
        spec = importlib.util.spec_from_file_location("capture_native_state_driver_test", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_offline_capture_nonzero_exit_blocks_screening_and_restores_credentials(self):
        module = self.capture_module()
        runtime = SimpleNamespace(run=lambda spec: 4)
        loader = SimpleNamespace(exec_module=lambda module: None)
        values = {"agent": "opencode", "log_dir": str(self.root / "capture"), "tap_upstream": "https://intended.example/v1",
                  "model_seed": nb.MODEL_SEED, "base_config": {"agent": {"build": {"temperature": 0}}}}
        with patch.dict(os.environ, {"OPENAI_API_KEY": "existing-credential"}), \
             patch.object(module.importlib.util, "spec_from_file_location", return_value=SimpleNamespace(loader=loader)), \
             patch.object(module.importlib.util, "module_from_spec", return_value=runtime):
            with self.assertRaises(ValueError):
                module.capture(values)
            self.assertEqual(os.environ["OPENAI_API_KEY"], "existing-credential")
        self.assertFalse((self.root / "capture/capture-manifest.json").exists())

    def test_offline_capture_records_fake_route_and_verified_body_without_claiming_e2e(self):
        module = self.capture_module()
        directory = self.root / "capture"
        native_specs = []
        def fake_native(spec):
            native_specs.append(spec)
            self.assertEqual(os.environ["OPENAI_API_KEY"], "offline-native-capture")
            body = {"model": "glm-5.2", "temperature": 0, "seed": nb.MODEL_SEED,
                    "messages": [{"role": "system", "content": "installed guidance"}],
                    "tools": [{"type": "function", "function": {"name": "zvec_grep_zvec_grep_search"}}]}
            raw = json.dumps(body).encode()
            path = directory / "wire-requests/request-002.raw.json"
            path.parent.mkdir(parents=True)
            path.write_bytes(raw)
            event = {"event": "request", "request_id": 2, "temperature": 0, "seed": nb.MODEL_SEED,
                     "tool_names": ["zvec_grep_zvec_grep_search"],
                     "raw_body_path": "wire-requests/request-002.raw.json", "raw_body_redacted": False,
                     "request_sha256": hashlib.sha256(raw).hexdigest()}
            (directory / "wire.jsonl").write_text(json.dumps(event) + "\n")
            dump(directory / "install-manifest.json", {"guidance_text": "installed guidance\n"})
            return 0
        runtime = SimpleNamespace(run=fake_native, instruction_texts=lambda body: [m["content"] for m in body["messages"]])
        loader = SimpleNamespace(exec_module=lambda module: None)
        values = {"agent": "opencode", "log_dir": str(directory), "tap_upstream": "https://intended.example/v1",
                  "model_seed": nb.MODEL_SEED, "base_config": {"agent": {"build": {"temperature": 0}}}}
        with patch.object(module.importlib.util, "spec_from_file_location", return_value=SimpleNamespace(loader=loader)), \
             patch.object(module.importlib.util, "module_from_spec", return_value=runtime):
            self.assertEqual(module.capture(values), 0)
        manifest = json.loads((directory / "capture-manifest.json").read_text())
        self.assertEqual(manifest["intended_endpoint"], "https://intended.example/v1")
        self.assertTrue(manifest["actual_endpoint"].startswith("http://127.0.0.1:"))
        self.assertEqual(values["tap_upstream"], "https://intended.example/v1")
        self.assertEqual(manifest["paid_model_calls"], 0)
        self.assertTrue(manifest["not_an_e2e_sample"])
        self.assertTrue(manifest["installed_guidance_verified"])
        self.assertTrue(manifest["sampling_controls_verified"])


if __name__ == "__main__":
    unittest.main()
