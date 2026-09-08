from __future__ import annotations

import http.server
import importlib.util
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("qa_session", Path(__file__).parents[1] / "scripts" / "qa-session.py")
session = importlib.util.module_from_spec(spec)
spec.loader.exec_module(session)


class SessionTest(unittest.TestCase):
    def test_native_counts_deduplicate_and_include_cached_input_once(self):
        counter = session.Counters()
        event = {"type": "step_finish", "part": {"id": "s1", "tokens": {"input": 30, "cache": {"read": 100, "write": 4}}}}
        counter.observe(event)
        counter.observe(event)
        counter.observe({"type": "tool_use", "part": {"callID": "t1"}})
        counter.observe({"type": "tool_use", "part": {"callID": "t1"}})
        self.assertEqual(counter.snapshot()["input_tokens"], 130)
        self.assertEqual(counter.snapshot()["cache_write_tokens"], 4)
        self.assertEqual(counter.snapshot()["model_requests"], 1)
        self.assertEqual(counter.snapshot()["tool_calls"], 1)
        self.assertEqual(counter.exceeded({"input_tokens": 100}), "input_tokens")

    def test_qoder_uses_message_identity_not_stream_deltas(self):
        counter = session.Counters()
        event = {"type": "assistant", "message": {"id": "msg1", "model": "qmodel_38max",
            "usage": {"input_tokens": 20, "cache_read_input_tokens": 80},
            "content": [{"type": "tool_use", "id": "call1"}]}}
        counter.observe(event)
        counter.observe(event)
        self.assertEqual(counter.snapshot()["model_requests"], 1)
        self.assertEqual(counter.snapshot()["tool_calls"], 1)
        self.assertEqual(counter.snapshot()["input_tokens"], 20)
        self.assertEqual(counter.snapshot()["native_models"], ["qmodel_38max"])

    def test_qoder_later_nonzero_snapshot_updates_masked_thinking_without_cache_double_count(self):
        counter = session.Counters()
        def event(usage):
            return {"type": "assistant", "message": {"id": "m", "usage": usage}}
        masked = {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0}
        counter.observe(event(masked))
        self.assertIsNone(counter.snapshot()["input_tokens"])
        counter.observe(event({"input_tokens": 100, "output_tokens": 5, "cache_read_input_tokens": 80, "cache_creation_input_tokens": 3, "service_tier": "standard"}))
        counter.observe(event({"input_tokens": 120, "output_tokens": 8, "cache_read_input_tokens": 80, "cache_creation_input_tokens": 3}))
        counter.observe(event(masked))
        self.assertEqual(counter.snapshot()["input_tokens"], 120)
        self.assertEqual(counter.snapshot()["cache_write_tokens"], 3)
        self.assertEqual(counter.snapshot()["model_requests"], 1)
        self.assertEqual(counter.snapshot()["invalid_usage_events"], 0)
        self.assertIsNone(counter.exceeded({"input_tokens": 150}))

    def test_unknown_turn_usage_remains_unknown_but_known_spend_can_stop_budget(self):
        counter = session.Counters()
        counter.observe({"type": "assistant", "message": {"id": "missing", "usage": {"input_tokens": 0}}})
        counter.observe({"type": "assistant", "message": {"id": "known", "usage": {"input_tokens": 100}}})
        self.assertIsNone(counter.snapshot()["input_tokens"])
        self.assertEqual(counter.snapshot()["input_tokens_observed_lower_bound"], 100)
        self.assertEqual(counter.snapshot()["input_usage_missing_turns"], 1)
        self.assertEqual(counter.exceeded({"input_tokens": 100}), "input_tokens")

    def test_parent_child_message_and_tool_ids_do_not_collide(self):
        counter = session.Counters()
        for parent in (None, "child"):
            counter.observe({"type": "assistant", "parent_tool_use_id": parent, "message": {"id": "m", "usage": {"input_tokens": 30},
                "content": [{"type": "tool_use", "id": "c"}]}})
        self.assertEqual(counter.snapshot()["model_requests"], 2)
        self.assertEqual(counter.snapshot()["tool_calls"], 2)
        self.assertEqual(counter.snapshot()["input_tokens"], 60)

    def test_invalid_token_values_do_not_fabricate_spend(self):
        for invalid in (True, -1, "10", float("nan"), float("inf")):
            counter = session.Counters()
            counter.observe({"type": "step_finish", "part": {"id": "one", "tokens": {"input": invalid, "cache": {"read": 3}}}})
            self.assertIsNone(counter.snapshot()["input_tokens"])
            self.assertGreater(counter.snapshot()["invalid_usage_events"], 0)

    def test_launch_failure_retains_session_status_without_exception_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            code = session.run({"log_dir": tmp, "command": ["/does-not-exist/qa-command"], "limits": {"wall_seconds": 3}})
            self.assertEqual(code, 127)
            report = json.loads((Path(tmp) / "session.json").read_text())
            self.assertEqual(report["status"], "launch_failure")
            self.assertEqual(report["error_type"], "FileNotFoundError")

    def test_wire_tap_preserves_request_and_sse_and_redacts_saved_secrets(self):
        received = []
        response = b'data: {"id":"x","model":"glm-5.2","usage":{"prompt_tokens":8,"completion_tokens":2}}\n\ndata: [DONE]\n\n'
        class Upstream(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_POST(self):
                received.append((self.path, self.rfile.read(int(self.headers["Content-Length"])), self.headers.get("Authorization")))
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                self.wfile.write(response)
        upstream = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
        threading.Thread(target=upstream.serve_forever, daemon=True).start()
        try:
            with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"OPENAI_API_KEY": "test-secret"}):
                root = Path(tmp)
                tap = session.WireTap(root, f"http://127.0.0.1:{upstream.server_port}/v1")
                address = tap.start()
                try:
                    body = json.dumps({"model": "glm-5.2", "temperature": 0, "messages": [{"content": "test-secret"}],
                        "tools": [{"function": {"name": "zvec_grep_zvec_grep_search"}}]}).encode()
                    request = urllib.request.Request(address + "/chat/completions", body, {"Authorization": "Bearer test-secret"})
                    self.assertEqual(urllib.request.urlopen(request).read(), response)
                    self.assertEqual(received, [("/v1/chat/completions", body, "Bearer test-secret")])
                finally:
                    tap.server.shutdown()
                    tap.server.server_close()
                rows = [json.loads(line) for line in (root / "wire.jsonl").read_text().splitlines()]
                self.assertEqual(rows[0]["temperature"], 0)
                self.assertEqual(rows[0]["tool_names"], ["zvec_grep_zvec_grep_search"])
                self.assertEqual(rows[1]["usage"]["prompt_tokens"], 8)
                self.assertNotIn("test-secret", (root / "wire-requests/request-001.json").read_text())
        finally:
            upstream.shutdown()
            upstream.server_close()

    def test_session_retains_budget_exhaustion_and_complete_native_trace(self):
        with tempfile.TemporaryDirectory() as tmp:
            code = 'import json,time; print(json.dumps({"type":"tool_use","part":{"callID":"t1"}}),flush=True);time.sleep(30)'
            result = session.run({"log_dir": tmp, "command": [sys.executable, "-c", code],
                "limits": {"wall_seconds": 5, "tool_calls": 1, "model_requests": 30, "input_tokens": 300000}})
            self.assertEqual(result, 3)
            report = json.loads((Path(tmp) / "session.json").read_text())
            self.assertEqual(report["status"], "budget_exhausted")
            self.assertEqual(report["limit_reason"], "tool_calls")
            self.assertIsNone(report["observed"]["input_tokens"])
            self.assertEqual(report["observed"]["tool_calls"], 1)
