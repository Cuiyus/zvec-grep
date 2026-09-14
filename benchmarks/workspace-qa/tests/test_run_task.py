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
        native, bridge = [{"type": "system", "subtype": "init", "qodercli_version": "1.1.45",
                           "model": "Qwen3.8-Max", "tools": [module.ZG_SEARCH_TOOL],
                           "mcp_servers": [{"name": "zvec_grep", "status": "connected"}]}], []
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
             "zg_tool_calls": len(statuses), "source_unchanged": True, "original_seed_unchanged": True,
             "working_index_semantic_unchanged": True,
             "input_tokens": 246933, "tool_calls": 31, "wall_seconds": 318.544}]}
        (self.runs / "trial-results.json").write_text(json.dumps(ledger))
        return agent

    def probe_fixture(self):
        probe = self.root / "sdk-preflight/qoder"
        agent = probe / "agent"
        agent.mkdir(parents=True, exist_ok=True)
        report = {"phase": "setup_qoder_mcp_probe", "status": "completed", "included_in_benchmark": False,
                  "embedding_model": "qwen/qwen3.7-text-embedding", "model": "qwen3.8-max",
                  "model_identity": {"valid": True}, "zg_tool_calls_successful": 1,
                  "successful_vector_searches": 1}
        native = [{"type": "system", "subtype": "init", "qodercli_version": "1.1.45",
                   "model": "Qwen3.8-Max", "tools": [module.ZG_SEARCH_TOOL],
                   "mcp_servers": [{"name": "zvec_grep", "status": "connected"}]},
                  {"type": "assistant", "session_id": "probe", "message": {"content": [
                   {"type": "tool_use", "id": "probe-call", "name": module.ZG_SEARCH_TOOL,
                    "input": {"vector": "fixture source"}}]}},
                  {"type": "user", "session_id": "probe", "message": {"content": [
                   {"type": "tool_result", "tool_use_id": "probe-call", "is_error": False,
                    "content": "probe.md:1 fixture source"}]}}]
        bridge = {"event": "search", "origin": "agent-mcp", "status": "success",
                  "request": {"routes": [{"mode": "vector", "query": "fixture source"}]},
                  "text": "probe.md:1 fixture source"}
        (probe / "result.json").write_text(json.dumps(report))
        (agent / "qodercli-stream.jsonl").write_text("\n".join(json.dumps(row) for row in native))
        (agent / "zg-trace.jsonl").write_text(json.dumps(bridge))
        return probe

    def test_natural_non_use_accepts_same_run_vector_proof_without_inflating_qa_usage(self):
        self.fixture([])
        self.probe_fixture()
        before = (self.runs / "trial-results.json").read_bytes()
        result = module.smoke_validation(self.runs, "smoke")
        self.assertEqual(result["schema_version"], 2)
        self.assertEqual(result["status"], "valid")
        self.assertEqual(result["verified_successful_searches"], 0)
        self.assertTrue(result["trials"][0]["natural_non_use"])
        self.assertEqual(result["setup_probe"]["status"], "valid")
        self.assertEqual(result["setup_probe"]["verified_successful_vector_searches"], 1)
        for field in ("native_sha256", "bridge_sha256", "result_sha256"):
            self.assertEqual(len(result["setup_probe"][field]), 64)
        self.assertEqual((self.runs / "trial-results.json").read_bytes(), before)

    def test_successful_setup_never_hides_failed_qa_attempts_or_missing_outcomes(self):
        agent = self.fixture([False])
        self.probe_fixture()
        self.assertEqual(module.smoke_validation(self.runs, "smoke")["status"], "invalid")
        rows = [json.loads(row) for row in (agent / "qodercli-stream.jsonl").read_text().splitlines()]
        (agent / "qodercli-stream.jsonl").write_text("\n".join(json.dumps(row) for row in rows if row.get("type") != "user"))
        self.assertEqual(module.smoke_validation(self.runs, "smoke")["status"], "invalid")

    def test_non_use_requires_raw_vector_evidence_and_successful_probe_report(self):
        self.fixture([])
        for mutation in ("missing_bridge", "native_error", "native_fts", "bridge_fts", "wrong_fixture", "failed_report", "wrong_model", "wrong_count"):
            with self.subTest(mutation=mutation):
                probe = self.probe_fixture()
                native_path, bridge_path = probe / "agent/qodercli-stream.jsonl", probe / "agent/zg-trace.jsonl"
                if mutation == "missing_bridge":
                    bridge_path.unlink()
                elif mutation.startswith("native_"):
                    native = [json.loads(row) for row in native_path.read_text().splitlines()]
                    if mutation == "native_error":
                        native[-1]["message"]["content"][0]["is_error"] = True
                    else:
                        native[1]["message"]["content"][0]["input"] = {"query": "fixture source"}
                    native_path.write_text("\n".join(json.dumps(row) for row in native))
                elif mutation in ("bridge_fts", "wrong_fixture"):
                    bridge = json.loads(bridge_path.read_text())
                    if mutation == "bridge_fts":
                        bridge["request"]["routes"][0]["mode"] = "fts"
                    else:
                        bridge["text"] = "no matching fixture"
                    bridge_path.write_text(json.dumps(bridge))
                else:
                    report = json.loads((probe / "result.json").read_text())
                    if mutation == "failed_report":
                        report["status"] = "failed"
                    elif mutation == "wrong_model":
                        report["embedding_model"] = "local/potion"
                    else:
                        report["successful_vector_searches"] = 2
                    (probe / "result.json").write_text(json.dumps(report))
                self.assertEqual(module.smoke_validation(self.runs, "smoke")["status"], "invalid")

    def test_non_use_requires_qa_registration_connected_status_and_all_integrity_checks(self):
        self.probe_fixture()
        for mutation in ("tools", "connected", "source_unchanged", "original_seed_unchanged", "working_index_semantic_unchanged", "zg_tool_calls"):
            with self.subTest(mutation=mutation):
                agent = self.fixture([])
                if mutation in ("tools", "connected"):
                    path = agent / "qodercli-stream.jsonl"
                    native = json.loads(path.read_text())
                    if mutation == "tools":
                        native["tools"] = []
                    else:
                        native["mcp_servers"][0]["status"] = "failed"
                    path.write_text(json.dumps(native))
                else:
                    path = self.runs / "trial-results.json"
                    ledger = json.loads(path.read_text())
                    ledger["trials"][1][mutation] = 1 if mutation == "zg_tool_calls" else False
                    path.write_text(json.dumps(ledger))
                self.assertEqual(module.smoke_validation(self.runs, "smoke")["status"], "invalid")

    def test_valid_integration_does_not_mark_incomplete_judge_report_complete(self):
        self.fixture([])
        self.probe_fixture()
        output = self.root / "report"
        output.mkdir()
        (output / "summary.json").write_text(json.dumps({"summary": {"complete": False}, "efficacy_claim_ready": True}))
        result = module.smoke_validation(self.runs, "smoke")
        self.assertEqual(result["status"], "valid")
        module.annotate_smoke_report(output, result)
        summary = json.loads((output / "summary.json").read_text())
        self.assertFalse(summary["pipeline_validation_complete"])
        self.assertFalse(summary["efficacy_claim_ready"])

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
        self.assertTrue(summary["summary"]["complete"])
        self.assertTrue(summary["pipeline_validation_complete"])
        self.assertFalse(summary["efficacy_claim_ready"])
        self.assertIn("excluded from formal efficacy estimates", (self.root / "report/summary.md").read_text())

    def test_natural_non_use_completes_smoke_only_with_probe_and_complete_report(self):
        self.probe_fixture()
        self.assertEqual(self.run_main("smoke", []), 0)
        summary = json.loads((self.root / "report/summary.json").read_text())
        self.assertTrue(summary["pipeline_validation_complete"])
        self.assertFalse(summary["efficacy_claim_ready"])
        self.assertEqual(summary["smoke_validation"]["verified_successful_searches"], 0)
        self.assertIn("Natural QA non-use is a valid observation", (self.root / "report/summary.md").read_text())

    def test_formal_batch_keeps_zero_zg_usage_trials(self):
        self.assertEqual(self.run_main("batch", []), 0)
        summary = json.loads((self.root / "report/summary.json").read_text())
        self.assertTrue(summary["efficacy_claim_ready"])
        self.assertNotIn("smoke_validation", summary)


if __name__ == "__main__":
    unittest.main()
