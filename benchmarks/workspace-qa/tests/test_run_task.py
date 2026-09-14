from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from subprocess import CompletedProcess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SPEC = importlib.util.spec_from_file_location("workspace_smoke_gate_test", ROOT / "run_task.py")
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


class SmokeGateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.runs = self.root / "runs"
        self.runs.mkdir()

    def fixture(self, statuses, measured=None):
        trial_id = "3-r01-with-zg"
        agent = self.runs / trial_id / "agent"
        agent.mkdir(parents=True, exist_ok=True)
        native, bridge = [], []
        for index, success in enumerate(statuses):
            call_id = f"call-{index}"
            native.extend([
                {"type": "assistant", "session_id": "session", "message": {"content": [
                    {"type": "tool_use", "id": call_id, "name": module.ZG_SEARCH_TOOL, "input": {"query": "fixture"}}]}},
                {"type": "user", "session_id": "session", "message": {"content": [
                    {"type": "tool_result", "tool_use_id": call_id, "is_error": not success,
                     "content": "source snippet" if success else "Qwen3.7 text embedding model requires an API key"}]}}
            ])
            bridge.append({"event": "search", "origin": "agent-mcp", "sequence": index + 1,
                           "status": "success" if success else "error"})
        (agent / "qodercli-stream.jsonl").write_text("\n".join(json.dumps(row) for row in native))
        (agent / "zg-trace.jsonl").write_text("\n".join(json.dumps(row) for row in bridge))
        ledger = {"task_id": "3", "repetitions_per_profile": 1, "trials": [
            {"trial_id": "3-r01-baseline", "task_id": "3", "profile": "baseline", "repetition": 1,
             "status": "completed", "input_tokens": 374316, "tool_calls": 43, "wall_seconds": 409.309},
            {"trial_id": trial_id, "task_id": "3", "profile": "with-zg", "repetition": 1,
             "status": "completed", "zg_tool_calls_successful": sum(statuses) if measured is None else measured,
             "input_tokens": 246933, "tool_calls": 31, "wall_seconds": 318.544}]}
        (self.runs / "trial-results.json").write_text(json.dumps(ledger))
        return agent

    def test_real_regression_one_failed_mcp_search_is_invalid(self):
        self.fixture([False])
        result = module.smoke_validation(self.runs, "smoke")
        self.assertEqual(result["status"], "invalid")
        self.assertEqual(result["verified_successful_searches"], 0)
        self.assertEqual(result["trials"][0]["native_errors"], 1)
        self.assertEqual(result["trials"][0]["bridge_errors"], 1)

    def test_success_passes_and_earlier_errors_remain_visible(self):
        self.fixture([False, True])
        result = module.smoke_validation(self.runs, "smoke")
        self.assertEqual(result["status"], "valid")
        self.assertEqual(result["verified_successful_searches"], 1)
        self.assertEqual(result["trials"][0]["native_errors"], 1)
        self.assertTrue(result["trials"][0]["success_counts_reconcile"])
        self.assertEqual(len(result["trials"][0]["native_sha256"]), 64)

    def test_metrics_alone_cannot_claim_success(self):
        self.fixture([False], measured=1)
        result = module.smoke_validation(self.runs, "smoke")
        self.assertEqual(result["status"], "invalid")
        self.assertFalse(result["trials"][0]["success_counts_reconcile"])

    def test_missing_raw_bridge_evidence_or_native_error_cannot_claim_success(self):
        agent = self.fixture([True])
        (agent / "zg-trace.jsonl").unlink()
        self.assertEqual(module.smoke_validation(self.runs, "smoke")["status"], "invalid")
        agent = self.fixture([False], measured=1)
        (agent / "zg-trace.jsonl").write_text(json.dumps({"event": "search", "origin": "agent-mcp", "status": "success"}))
        self.assertEqual(module.smoke_validation(self.runs, "smoke")["status"], "invalid")

    def test_setup_probe_does_not_count_as_agent_search(self):
        agent = self.fixture([True])
        (agent / "zg-trace.jsonl").write_text(json.dumps({"event": "search", "origin": "setup-probe", "status": "success"}))
        self.assertEqual(module.smoke_validation(self.runs, "smoke")["status"], "invalid")

    def test_batch_does_not_require_or_filter_tool_choice(self):
        self.fixture([])
        before = (self.runs / "trial-results.json").read_bytes()
        result = module.smoke_validation(self.runs, "batch")
        self.assertEqual(result["status"], "not_applicable")
        self.assertEqual((self.runs / "trial-results.json").read_bytes(), before)
        self.assertEqual(module.smoke_validation(self.runs, "smoke")["status"], "invalid")

    def run_main(self, phase, statuses):
        self.fixture(statuses)
        code = self.root / "fake-code"
        (code / "data").mkdir(parents=True)
        (code / "data/lock.json").write_text(json.dumps({"tasks": [{"task_id": "3", "answer_filename": "answer.md"}]}))
        def subprocess_run(command, **kwargs):
            if str(command[1]).endswith("report.py"):
                report = self.root / "report"
                report.mkdir()
                (report / "summary.json").write_text(json.dumps({"efficacy_claim_ready": True, "summary": {"complete": True},
                                                                "scores": {"baseline": 19 / 21, "with-zg": 18 / 21}}))
                (report / "summary.md").write_text("Coverage: COMPLETE\nRubric baseline 19/21, with zg 18/21\n")
                (report / "rows.json").write_text("original observations")
            return CompletedProcess(command, 0)
        before = (self.runs / "trial-results.json").read_bytes()
        with patch.object(module, "HERE", code), patch.object(module, "embedding_preflight"), patch.object(module, "sdk_preflight"), \
                patch.object(module.subprocess, "run", side_effect=subprocess_run), \
                patch.dict("os.environ", {"QODER_PERSONAL_ACCESS_TOKEN": "fixture", "GLM_API_KEY": "fixture", "QWEN_API_KEY": "fixture"}):
            exit_code = module.main(["--task-id", "3", "--repetitions", "1", "--phase", phase,
                                     "--output", str(self.root), "--upstream", str(self.root / "upstream")])
        self.assertEqual((self.runs / "trial-results.json").read_bytes(), before)
        self.assertEqual((self.root / "report/rows.json").read_text(), "original observations")
        return exit_code

    def test_invalid_smoke_exits_nonzero_but_preserves_measurements_and_judgements(self):
        self.assertEqual(self.run_main("smoke", [False]), 1)
        summary = json.loads((self.root / "report/summary.json").read_text())
        self.assertFalse(summary["efficacy_claim_ready"])
        self.assertFalse(summary["pipeline_validation_complete"])
        self.assertEqual(summary["scores"], {"baseline": 19 / 21, "with-zg": 18 / 21})
        self.assertEqual(json.loads((self.root / "smoke_validation.json").read_text())["status"], "invalid")
        self.assertIn("Smoke MCP validation: INVALID", (self.root / "report/summary.md").read_text())

    def test_valid_smoke_exits_zero(self):
        self.assertEqual(self.run_main("smoke", [True]), 0)
        summary = json.loads((self.root / "report/summary.json").read_text())
        self.assertTrue(summary["pipeline_validation_complete"])

    def test_formal_batch_keeps_zero_zg_usage_trials(self):
        self.assertEqual(self.run_main("batch", []), 0)
        summary = json.loads((self.root / "report/summary.json").read_text())
        self.assertTrue(summary["efficacy_claim_ready"])
        self.assertNotIn("smoke_validation", summary)


if __name__ == "__main__":
    unittest.main()
