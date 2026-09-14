from __future__ import annotations
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import qoder_probe
from native_fixtures import PROTOCOL, dump, installation_stub, write_probe


class QoderProbeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.output = self.root / "qoder"
        self.proof = patch.object(qoder_probe, "installation_evidence", side_effect=installation_stub)
        self.proof.start()
        self.addCleanup(self.proof.stop)

    def test_success_uses_native_call_result_and_install_artifacts_without_bridge(self):
        write_probe(self.output)
        result = qoder_probe.validate_probe(self.output)
        self.assertEqual(result["status"], "valid")
        self.assertEqual(result["protocol"], PROTOCOL)
        self.assertEqual(result["verified_successful_vector_searches"], 1)
        self.assertEqual(len(result["installation"]["manifest_sha256"]), 64)
        self.assertFalse((self.output / "agent/zg-trace.jsonl").exists())

    def test_no_retrieval_wrong_fixture_exact_query_empty_or_failed_result_fail(self):
        for mutation in ("connected_only", "wrong_fixture", "exact", "empty", "failed", "orphan", "missing"):
            with self.subTest(mutation=mutation):
                write_probe(self.output)
                path = self.output / "agent/qodercli-stream.jsonl"
                events = [json.loads(line) for line in path.read_text().splitlines()]
                if mutation == "connected_only":
                    events = events[:1]
                elif mutation == "wrong_fixture":
                    events[-1]["message"]["content"][0]["content"] = "probe.md wrong"
                elif mutation == "exact":
                    events[1]["message"]["content"][0]["input"] = {"query": "repository"}
                elif mutation == "empty":
                    events[-1]["message"]["content"][0]["content"] = ""
                elif mutation == "failed":
                    events[-1]["message"]["content"][0]["is_error"] = True
                elif mutation == "orphan":
                    events[-1]["message"]["content"][0]["tool_use_id"] = "another"
                else:
                    events.pop()
                path.write_text("\n".join(json.dumps(event) for event in events))
                with self.assertRaises(ValueError):
                    qoder_probe.validate_probe(self.output)

    def test_old_protocol_source_change_unknown_usage_and_failed_model_are_invalid(self):
        for field, value in (("protocol", "workspace-qa-qoder-v1"), ("source_unchanged", False),
                             ("input_tokens", None), ("model_identity", {"valid": False}),
                             ("zg_tool_calls_successful", 2), ("included_in_benchmark", True),
                             ("installation", {"manifest_sha256": "stale"})):
            with self.subTest(field=field):
                result = write_probe(self.output)
                result[field] = value
                dump(self.output / "result.json", result)
                with self.assertRaises(ValueError):
                    qoder_probe.validate_probe(self.output)

    def test_missing_install_artifact_is_not_a_valid_native_probe(self):
        write_probe(self.output)
        (self.output / "agent/install-manifest.json").unlink()
        with self.assertRaises(OSError):
            qoder_probe.validate_probe(self.output)

    def test_list_content_and_identical_duplicates_count_once(self):
        write_probe(self.output)
        path = self.output / "agent/qodercli-stream.jsonl"
        events = [json.loads(line) for line in path.read_text().splitlines()]
        block = events[-1]["message"]["content"][0]
        block["content"] = [{"type": "text", "text": block["content"]}]
        events.extend(events[1:])
        path.write_text("\n".join(json.dumps(event) for event in events))
        result = qoder_probe.validate_probe(self.output)
        self.assertEqual(result["native_attempts"], 1)

    def test_conflicting_result_is_invalid(self):
        write_probe(self.output)
        path = self.output / "agent/qodercli-stream.jsonl"
        events = [json.loads(line) for line in path.read_text().splitlines()]
        conflict = json.loads(json.dumps(events[-1]))
        conflict["message"]["content"][0]["is_error"] = True
        path.write_text("\n".join(json.dumps(event) for event in [*events, conflict]))
        with self.assertRaisesRegex(ValueError, "Conflicting"):
            qoder_probe.validate_probe(self.output)

    def test_preflight_delegates_native_runner_then_retains_both_raw_and_validation(self):
        source = self.root / "source"
        source.mkdir()
        def run(actual_source, actual_output):
            self.assertEqual(actual_source, source)
            self.assertEqual(actual_output, self.output)
            return write_probe(actual_output)
        with patch.dict(sys.modules, {"native_runner": types.SimpleNamespace(run_native_probe=run)}):
            qoder_probe.qoder_mcp_preflight(source, self.output)
        self.assertEqual(json.loads((self.output / "validation.json").read_text())["status"], "valid")
        self.assertEqual(json.loads((self.output / "result.json").read_text())["input_tokens"], 123)

    def test_invalid_probe_keeps_original_result_and_propagates_failure_without_retry(self):
        calls = []
        def run(source, output):
            calls.append(1)
            return write_probe(output, outcomes=(False,))
        with patch.dict(sys.modules, {"native_runner": types.SimpleNamespace(run_native_probe=run)}), self.assertRaises(ValueError):
            qoder_probe.qoder_mcp_preflight(self.root, self.output)
        self.assertEqual(calls, [1])
        self.assertEqual(json.loads((self.output / "validation.json").read_text())["status"], "invalid")
        self.assertEqual(json.loads((self.output / "result.json").read_text())["zg_tool_calls_successful"], 0)

    def test_standalone_fixture_contains_unique_evidence_and_is_cleaned_on_failure(self):
        root = self.root / "probe"
        def fail(source, output):
            self.assertEqual((source / "probe.md").read_text(), qoder_probe.PROBE_TEXT)
            dump(output / "result.json", {"status": "failed"})
            raise RuntimeError("native installation failed")
        with patch.object(qoder_probe.subprocess, "run") as git, \
                patch.object(qoder_probe, "qoder_mcp_preflight", side_effect=fail), self.assertRaises(RuntimeError):
            qoder_probe.standalone_native_probe(root)
        self.assertEqual(git.call_count, 3)
        self.assertFalse((root / "source").exists())
        self.assertTrue((root / "qoder/result.json").exists())


if __name__ == "__main__":
    unittest.main()
