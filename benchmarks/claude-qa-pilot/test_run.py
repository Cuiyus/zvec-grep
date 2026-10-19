import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("pilot", Path(__file__).with_name("run.py"))
pilot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pilot)
MODEL = "qwen3.8-max"
TOOL = "mcp__zvec_grep__zvec_grep_search"


def event_stream(*events):
    return "\n".join(json.dumps(event) for event in events)


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
    def test_session_usage_authoritative_and_duplicate_tool_not_double_counted(self):
        metrics = pilot.parse_stream(event_stream(assistant(), assistant(), result()), MODEL, "baseline", TOOL)
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
        init = {"type": "system", "subtype": "init", "mcp_servers": [{"name": "zvec_grep", "status": "connected"}]}
        metrics = pilot.parse_stream(event_stream(init, assistant(), result()), MODEL, "zg", TOOL)
        self.assertEqual(metrics["errors"], [])
        self.assertFalse(metrics["zg_used"])

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
