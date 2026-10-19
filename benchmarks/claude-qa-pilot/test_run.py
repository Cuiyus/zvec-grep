import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("pilot", Path(__file__).with_name("run.py"))
pilot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pilot)
ci_spec = importlib.util.spec_from_file_location("pilot_ci", Path(__file__).with_name("ci_run.py"))
pilot_ci = importlib.util.module_from_spec(ci_spec)
ci_spec.loader.exec_module(pilot_ci)
MODEL = "qwen3.8-max"
TOOL = "mcp__zvec_grep__zvec_grep_search"


def event_stream(*events):
    return "\n".join(json.dumps(event) for event in events)


def init_event(profile="baseline", tools=None):
    expected = sorted(pilot.BUILTIN_TOOLS | ({TOOL} if profile == "zg" else set()))
    return {"type": "system", "subtype": "init", "tools": expected if tools is None else tools,
            "mcp_servers": [{"name": "zvec_grep", "status": "connected"}] if profile == "zg" else []}


def assistant(model=MODEL):
    return {"type": "assistant", "message": {"id": "msg-1", "model": model,
        "usage": {"input_tokens": 20, "cache_read_input_tokens": 40,
                  "cache_creation_input_tokens": 10, "output_tokens": 5},
        "content": [{"type": "tool_use", "id": "tool-1", "name": "Read"}]}}


def result():
    return {"type": "result", "subtype": "success", "is_error": False, "result": "Answer.",
            "usage": {"input_tokens": 80, "cache_read_input_tokens": 120,
                      "cache_creation_input_tokens": 40, "output_tokens": 50}}


class MetricsTests(unittest.TestCase):
    def test_trial_uses_host_identity_for_private_seed_permissions(self):
        # Prepared/copy_seed files retain private host-owned modes. Root with
        # all capabilities dropped cannot read those files or write this HOME.
        args = pilot.argparse.Namespace(source=Path('/source'))
        with patch.object(pilot.os, 'getuid', return_value=1001), \
                patch.object(pilot.os, 'getgid', return_value=1002):
            command = pilot.docker_base(args, 'trial', Path('/trial'), Path('/runtime'))
        self.assertEqual(command[command.index('--user') + 1], '1001:1002')
        self.assertIn('--read-only', command)
        self.assertEqual(command[command.index('--cap-drop') + 1], 'ALL')
        self.assertIn('type=bind,src=/runtime/home,dst=/root', command)

    def test_rust_index_command_and_model_defaults(self):
        command = pilot_ci.index_command('local/potion-code-16m-v2')
        self.assertIn('--index', command)
        self.assertIn('--ignore-file', command)
        self.assertNotIn('index', command)
        self.assertNotIn('--allow-remote', command)

    def test_stale_node_or_wrong_commit_runtime_rejected(self):
        for provenance in ({}, {'commit': 'older-commit'}, {'package': '@zvec/zvec-grep@0.2.2'}):
            with self.assertRaises(RuntimeError):
                pilot_ci.verify_runtime_provenance(provenance)
        pilot_ci.verify_runtime_provenance({'commit': pilot_ci.UPSTREAM_COMMIT})

    def test_cancelled_partial_stream_collected_and_redacted_without_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            work, reports = root / 'work', root / 'reports'
            trial = work / 'trials' / 'r01-baseline'
            trial.mkdir(parents=True)
            (trial / 'stream.jsonl').write_text('partial event secret-fixture-value')
            (trial / 'home').mkdir()
            (trial / 'home' / 'private').write_text('private runtime state')
            with patch.dict('os.environ', {'ANTHROPIC_API_KEY': 'secret-fixture-value'}):
                scan = pilot_ci.collect_evidence(work, reports)
            self.assertTrue(scan['passed'])
            self.assertEqual((reports / 'r01-baseline' / 'stream.jsonl').read_text(), 'partial event [REDACTED]')
            self.assertFalse((reports / 'r01-baseline' / 'home').exists())

    def test_session_usage_authoritative_and_duplicate_tool_not_double_counted(self):
        metrics = pilot.parse_stream(event_stream(init_event(), assistant(), assistant(), result()), MODEL, "baseline", TOOL)
        self.assertEqual(metrics["input_tokens_including_cache"], 240)
        self.assertEqual(metrics["tool_calls"], 1)
        self.assertEqual(metrics["errors"], [])

    def test_message_fallback_deduplicates_usage(self):
        end = result()
        end.pop("usage")
        metrics = pilot.parse_stream(event_stream(assistant(), assistant(), end), MODEL, "baseline", TOOL)
        self.assertEqual(metrics["input_tokens_including_cache"], 70)
        self.assertEqual(metrics["usage_source"], "deduplicated_assistant_messages")

    def test_model_fallback_and_unexpected_tool_fail(self):
        event = assistant("claude-opus-5")
        event["message"]["content"][0]["name"] = "Bash"
        metrics = pilot.parse_stream(event_stream(event, result()), MODEL, "baseline", TOOL)
        self.assertIn("model_mismatch", metrics["errors"])
        self.assertIn("unexpected_tools", metrics["errors"])

    def test_missing_usage_and_model_not_zero_cost_success(self):
        end = result()
        end.pop("usage")
        metrics = pilot.parse_stream(event_stream(end), MODEL, "baseline", TOOL)
        self.assertIn("missing_input_usage", metrics["errors"])
        self.assertIn("missing_resolved_model", metrics["errors"])
        self.assertIsNone(metrics["input_tokens_including_cache"])

    def test_mcp_connection_required_but_adoption_not_forced(self):
        events = event_stream(assistant(), result())
        self.assertIn("zg_mcp_not_connected", pilot.parse_stream(events, MODEL, "zg", TOOL)["errors"])
        init = init_event("zg")
        metrics = pilot.parse_stream(event_stream(init, assistant(), result()), MODEL, "zg", TOOL)
        self.assertEqual(metrics["errors"], [])
        self.assertFalse(metrics["zg_used"])

    def test_only_read_inventory_rejects_silently_handicapped_baseline(self):
        metrics = pilot.parse_stream(event_stream(init_event(tools=["Read"]), assistant(), result()), MODEL, "baseline", TOOL)
        self.assertIn("missing_required_tools", metrics["errors"])
        self.assertEqual(metrics["missing_required_tools"], ["Glob", "Grep"])

    def test_missing_init_inventory_invalidates_even_successful_answer(self):
        metrics = pilot.parse_stream(event_stream(assistant(), result()), MODEL, "baseline", TOOL)
        self.assertIn("missing_init_tool_inventory", metrics["errors"])

    def test_unexpected_exposed_tool_rejects_trial_even_if_unused(self):
        metrics = pilot.parse_stream(event_stream(init_event(tools=["Read", "Grep", "Glob", "Bash"]), assistant(), result()), MODEL, "baseline", TOOL)
        self.assertIn("unexpected_exposed_tools", metrics["errors"])
        self.assertEqual(metrics["unexpected_exposed_tools"], ["Bash"])

    def test_connected_mcp_without_search_schema_invalidates_treatment(self):
        metrics = pilot.parse_stream(event_stream(init_event("zg", tools=["Read", "Grep", "Glob"]), assistant(), result()), MODEL, "zg", TOOL)
        self.assertIn("missing_required_tools", metrics["errors"])
        self.assertEqual(metrics["missing_required_tools"], [TOOL])

    def test_budget_exhaustion_preserves_valid_protocol(self):
        end = result()
        end.update({"subtype": "error_max_turns", "is_error": True, "result": ""})
        metrics = pilot.parse_stream(event_stream(init_event(), assistant(), end), MODEL, "baseline", TOOL)
        self.assertTrue(metrics["protocol_valid"])
        self.assertEqual(metrics["infrastructure_errors"], [])
        self.assertIn("missing_final_answer", metrics["errors"])
        self.assertIn("agent_result_error:error_max_turns", metrics["errors"])

    def test_interrupted_stream_after_model_response_preserves_valid_protocol(self):
        metrics = pilot.parse_stream(event_stream(init_event(), assistant()), MODEL, "baseline", TOOL)
        self.assertTrue(metrics["protocol_valid"])
        self.assertIn("missing_final_result", metrics["errors"])

    def test_no_model_response_is_infrastructure_failure(self):
        metrics = pilot.parse_stream(event_stream(init_event()), MODEL, "baseline", TOOL)
        self.assertFalse(metrics["protocol_valid"])
        self.assertIn("missing_resolved_model", metrics["infrastructure_errors"])

    def test_source_hash_excludes_index_but_detects_source_change(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "x.py").write_text("x = 1")
            (root / ".zvec-grep").mkdir()
            index = root / ".zvec-grep" / "state"
            index.write_text("old")
            first = pilot.source_manifest(root)
            index.write_text("new")
            self.assertEqual(first, pilot.source_manifest(root))
            (root / "x.py").write_text("x = 2")
            self.assertNotEqual(first["sha256"], pilot.source_manifest(root)["sha256"])


if __name__ == "__main__":
    unittest.main()
