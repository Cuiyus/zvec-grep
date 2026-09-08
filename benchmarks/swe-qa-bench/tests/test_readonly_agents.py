from __future__ import annotations

import json
import os
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from zg_bench.swe_qa.readonly_agents import (
    QODER_SEARCH_TOOL,
    agent_environment,
    agent_spec,
    build_agent_command,
    build_agent_config,
    control_manifest,
    convert_agent_trace,
    expected_tools,
    qoder_contract,
    read_native_events,
)


def qoder_events(*, zg=False, usage=None, model="Qwen3.8-Max", result_model="qmodel_38max"):
    spec = agent_spec("qodercli", "qwen3.8-max")
    return [
        {"type": "system", "subtype": "init", "session_id": "trial", "model": "Qwen3.8-Max",
         "tools": expected_tools(spec, zg=zg), "permissionMode": "dontAsk",
         "mcp_servers": [{"name": "zvec_grep", "status": "connected"}] if zg else []},
        {"type": "assistant", "session_id": "trial", "message": {
            "id": "a1", "model": model, "content": [
                {"type": "tool_use", "id": "read-1", "name": "Read", "input": {"file_path": "/app/a.py"}}]}},
        {"type": "user", "session_id": "trial", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "read-1", "content": "file missing", "is_error": True}]}},
        {"type": "assistant", "session_id": "trial", "message": {
            "id": "a2", "model": model, "content": [{"type": "text", "text": "The final answer."}]}},
        {"type": "result", "session_id": "trial", "subtype": "success", "is_error": False,
         "result": "The final answer.", "usage": usage or {}, "modelUsage": {result_model: {}},
         "total_cost_usd": 0, "total_credits": 2.5},
    ]


class ReadonlyAgentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)

    def convert(self, events, *, zg=False):
        spec = agent_spec("qodercli", "qwen3.8-max")
        (self.directory / spec.stream_filename).write_text("\n".join(map(json.dumps, events)) + "\n")
        result = convert_agent_trace(self.directory, spec, "Question", zg=zg)
        data = json.loads((self.directory / "trajectory.json").read_text())
        return result, data

    def test_authorized_combinations_and_no_implicit_byok(self):
        spec = agent_spec("qoder", "qwen3.8-max")
        self.assertEqual(spec.version, "1.1.45")
        self.assertEqual(spec.cli_model, "Qwen3.8-Max")
        self.assertEqual(spec.credential_env, "QODER_PERSONAL_ACCESS_TOKEN")
        for args in (("qodercli", "auto"), ("qodercli", "glm-5.2"), ("unknown", "qwen3.8-max")):
            with self.subTest(args=args), self.assertRaises(ValueError):
                agent_spec(*args)
        for kwargs in ({"base_url": "https://example.com/v1"}, {"api_key_env": "OPENAI_API_KEY"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                agent_spec("qodercli", "qwen3.8-max", **kwargs)

    def test_opencode_provider_requires_an_explicit_credential_free_url(self):
        for url in (None, "https://key@example.com", "https://example.com?api_key=secret", "file:///tmp/provider"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                agent_spec("opencode", "glm-5.2", base_url=url)
        spec = agent_spec("opencode", "qwen3.8-max", base_url="https://example.com/v1", api_key_env="QWEN_API_KEY")
        cfg = build_agent_config(spec, zg=False, max_model_turns=30)
        self.assertEqual(cfg["provider"]["custom-openai"]["options"]["apiKey"], "{env:QWEN_API_KEY}")
        self.assertEqual(cfg["agent"]["build"], {"temperature": 0, "steps": 30})
        self.assertIs(cfg["provider"]["custom-openai"]["models"]["qwen3.8-max"]["temperature"], True)
        controls = control_manifest(spec, max_model_turns=30)
        self.assertIn("provider.models.<model>.temperature=true", controls["temperature_control"])
        self.assertTrue(controls["temperature_wire_verification_required"])
        self.assertNotIn("mcp", cfg)
        self.assertEqual(cfg["permission"]["*"], "deny")

    def test_qoder_config_and_argv_remove_write_and_external_tools(self):
        spec = agent_spec("qodercli", "qwen3.8-max")
        cfg = build_agent_config(spec, zg=True, mcp_command=["node", "/bridge.mjs", "serve"])
        self.assertEqual(cfg["tools"]["core"], ["Read", "Grep", "Glob"])
        self.assertEqual(cfg["mcpServers"]["zvec_grep"]["includeTools"], ["zvec_grep_search"])
        self.assertIn(QODER_SEARCH_TOOL, cfg["permissions"]["allow"])
        self.assertIn("Bash", cfg["permissions"]["deny"])
        self.assertTrue(cfg["disableAllHooks"])
        prompt = "Read `$HOME`; $(do-not-run) 'quoted'"
        cmd = build_agent_command(spec, prompt, config_path="/run/qa/qoder.json", zg=True)
        self.assertEqual(cmd[-2:], ["--", prompt])
        self.assertIn("--strict-mcp-config", cmd)
        self.assertEqual(cmd[cmd.index("--setting-sources") + 1], "")
        self.assertEqual(cmd[cmd.index("--permission-mode") + 1], "dont_ask")
        self.assertNotIn("bypass_permissions", cmd)
        self.assertNotIn(spec.credential_env, cmd)
        self.assertEqual(agent_environment(spec, config_path="x")["QODER_EXPOSE_TOKEN_USAGE"], "1")

    def test_zg_command_is_explicit_and_baseline_has_no_mcp(self):
        spec = agent_spec("qodercli", "qwen3.8-max")
        with self.assertRaises(ValueError):
            build_agent_config(spec, zg=True)
        with self.assertRaises(ValueError):
            build_agent_config(spec, zg=False, mcp_command=["node", "bridge"])
        self.assertEqual(build_agent_config(spec, zg=False)["mcpServers"], {})

    def test_qoder_unavailable_controls_are_not_claimed(self):
        spec = agent_spec("qodercli", "qwen3.8-max")
        controls = control_manifest(spec, max_model_turns=30)
        self.assertIsNone(controls["temperature"])
        self.assertEqual(controls["native_model_turn_limit"], "--max-turns (pinned bundle verified)")
        self.assertTrue(controls["external_budget_enforcement_required"])
        for value in (0, -1, True, 2.5):
            with self.subTest(value=value), self.assertRaises(ValueError):
                control_manifest(spec, max_model_turns=value)

    def test_raw_usage_tool_error_and_native_model_are_preserved(self):
        summary, data = self.convert(qoder_events(usage={"input_tokens": 100, "output_tokens": 20, "cache_read_input_tokens": 70}))
        self.assertEqual(summary["error_event_count"], 0)
        self.assertTrue(summary["has_final_answer"])
        self.assertTrue(summary["model_identity"]["valid"])
        self.assertEqual(summary["model_identity"]["sources"], ["assistant.message.model", "result.modelUsage"])
        self.assertEqual(summary["tool_error_count"], 1)
        self.assertEqual(data["final_metrics"]["total_prompt_tokens"], 100)
        self.assertEqual(data["final_metrics"]["total_cached_tokens"], 70)
        self.assertNotIn("total_cost_usd", data["final_metrics"])
        self.assertEqual(data["final_metrics"]["extra"]["qoder_total_credits"], 2.5)
        self.assertEqual(data["steps"][0]["observation"]["results"][0]["content"], "file missing")

    def test_masked_zero_tokens_stay_unavailable(self):
        summary, data = self.convert(qoder_events(usage={"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0}))
        self.assertEqual(summary["error_event_count"], 0)
        self.assertNotIn("total_prompt_tokens", data["final_metrics"])
        self.assertFalse(data["final_metrics"]["extra"]["token_usage_available"])

    def test_conflicting_executed_model_cannot_pass_via_alias_or_init(self):
        for kwargs in ({"model": "auto"}, {"result_model": "auto"}, {"model": "Qwen3.7-Max"}):
            with self.subTest(kwargs=kwargs):
                summary, _ = self.convert(qoder_events(**kwargs))
                self.assertFalse(summary["model_identity"]["valid"])
                self.assertGreater(summary["error_event_count"], 0)

    def test_model_must_be_reported_by_response_not_only_requested_init(self):
        events = qoder_events()
        for event in events:
            if event["type"] == "assistant":
                event["message"].pop("model")
        events[-1]["modelUsage"] = {}
        summary, _ = self.convert(events)
        self.assertFalse(summary["model_identity"]["valid"])
        self.assertGreater(summary["error_event_count"], 0)
        self.assertEqual(summary["contract_error_count"], 0)

    def test_missing_init_is_not_an_affirmative_configuration_mismatch(self):
        summary, _ = self.convert(qoder_events()[1:])
        self.assertFalse(summary["tool_contract"]["valid"])
        self.assertGreater(summary["error_event_count"], 0)
        self.assertEqual(summary["contract_error_count"], 0)

    def test_tools_and_permission_init_are_a_required_runtime_contract(self):
        for mutation in ("extra_tool", "bypass", "missing_read", "missing_init"):
            events = qoder_events()
            if mutation == "extra_tool":
                events[0]["tools"].append("Bash")
            elif mutation == "bypass":
                events[0]["permissionMode"] = "bypassPermissions"
            elif mutation == "missing_read":
                events[0]["tools"].remove("Read")
            else:
                events.pop(0)
            with self.subTest(mutation=mutation):
                self.assertFalse(qoder_contract(events, zg=False)["valid"])

    def test_failed_mcp_and_undeclared_tool_call_are_not_silently_aliased(self):
        events = qoder_events(zg=True)
        events[0]["mcp_servers"][0]["status"] = "failed"
        self.assertFalse(qoder_contract(events, zg=True)["valid"])
        events = qoder_events(zg=True)
        events[1]["message"]["content"][0]["name"] = "zvec_grep_search"
        contract = qoder_contract(events, zg=True)
        self.assertEqual(contract["unexpected_tool_calls"], ["zvec_grep_search"])
        self.assertTrue(contract["valid"])
        summary, _ = self.convert(events, zg=True)
        self.assertEqual(summary["contract_error_count"], 0)
        self.assertEqual(summary["error_event_count"], 0)
        self.assertTrue(summary["has_final_answer"])

    def test_message_usage_snapshots_and_reasoning_are_retained_without_double_counting(self):
        events = qoder_events(usage={"input_tokens": 60, "output_tokens": 10})
        events[1]["message"]["usage"] = {"input_tokens": 20, "output_tokens": 3, "cache_read_input_tokens": 10}
        repeated = json.loads(json.dumps(events[1]))
        repeated["message"]["content"] = [{"type": "thinking", "thinking": "Reasoning from the native event."}]
        repeated["message"]["usage"]["output_tokens"] = 4
        events.insert(2, repeated)
        summary, data = self.convert(events)
        metrics = data["steps"][0]["metrics"]
        self.assertEqual(metrics["prompt_tokens"], 20)
        self.assertEqual(metrics["completion_tokens"], 4)
        self.assertEqual(len(metrics["extra"]["qoder_usage_snapshots"]), 2)
        self.assertEqual(data["steps"][0]["reasoning_content"], "Reasoning from the native event.")
        self.assertEqual(data["final_metrics"]["total_prompt_tokens"], 60)
        self.assertEqual(summary["contract_error_count"], 0)
        masked = json.loads(json.dumps(repeated))
        masked["message"]["usage"] = {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0}
        events.insert(3, masked)
        _, data = self.convert(events)
        self.assertEqual(data["steps"][0]["metrics"]["prompt_tokens"], 20)
        self.assertEqual(data["steps"][0]["metrics"]["completion_tokens"], 4)

    def test_error_result_or_missing_result_cannot_be_a_final_answer(self):
        events = qoder_events()
        events[-1]["subtype"] = "error_during_execution"
        events[-1]["is_error"] = True
        summary, _ = self.convert(events)
        self.assertFalse(summary["has_final_answer"])
        self.assertGreater(summary["error_event_count"], 0)
        summary, _ = self.convert(qoder_events()[:-1])
        self.assertFalse(summary["has_final_answer"])

    def test_duplicate_message_tool_block_does_not_duplicate_calls(self):
        events = qoder_events()
        events.insert(2, events[1].copy())
        summary, data = self.convert(events)
        self.assertEqual(sum(len(s.get("tool_calls", [])) for s in data["steps"]), 1)
        self.assertEqual(summary["tool_error_count"], 1)

    def test_truncated_stream_is_reported_not_hidden(self):
        p = self.directory / "stream.jsonl"
        p.write_text('{"type":"system"}\n{"broken":')
        events, diagnostics = read_native_events(p)
        self.assertEqual(events, [{"type": "system"}])
        self.assertEqual(diagnostics["invalid_json_lines"], [2])
        self.assertTrue(diagnostics["last_line_incomplete"])


@unittest.skipUnless(os.environ.get("OPENCODE_READONLY_TEST_BINARY"), "requires pinned OpenCode binary; uses only a local fake provider")
class OpenCodeRequestContractTests(unittest.TestCase):
    def test_actual_task_requests_include_zero_temperature_for_both_models(self):
        """Catch capability gating that a config-dict assertion cannot detect."""
        binary = os.environ["OPENCODE_READONLY_TEST_BINARY"]
        self.assertEqual(subprocess.check_output([binary, "--version"], text=True, timeout=10).strip(), "1.18.4")
        for model in ("glm-5.2", "qwen3.8-max"):
            with self.subTest(model=model), tempfile.TemporaryDirectory(prefix="opencode-temperature-contract-") as directory:
                root = Path(directory)
                corpus = root / "corpus"
                corpus.mkdir()
                (corpus / "README.md").write_text("The fixture value is 42.\n")
                requests: list[dict] = []

                class FakeProvider(BaseHTTPRequestHandler):
                    def log_message(self, *_args):
                        pass

                    def do_POST(self):
                        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                        requests.append(body)
                        task = bool(body.get("tools"))
                        after_read = any(m.get("role") == "tool" for m in body.get("messages", []))
                        if task and not after_read:
                            delta = {"role": "assistant", "tool_calls": [{"index": 0, "id": "read-fixture", "type": "function",
                                     "function": {"name": "read", "arguments": json.dumps({"filePath": str(corpus / "README.md")})}}]}
                            finish = "tool_calls"
                        else:
                            delta = {"role": "assistant", "content": "The fixture value is 42." if task else "Read fixture"}
                            finish = "stop"
                        chunks = [
                            {"id": "fixture", "object": "chat.completion.chunk", "created": 1, "model": body["model"],
                             "choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
                            {"id": "fixture", "object": "chat.completion.chunk", "created": 1, "model": body["model"],
                             "choices": [{"index": 0, "delta": {}, "finish_reason": finish}],
                             "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}},
                        ]
                        payload = "".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks) + "data: [DONE]\n\n"
                        self.send_response(200)
                        self.send_header("Content-Type", "text/event-stream")
                        self.send_header("Content-Length", str(len(payload.encode())))
                        self.end_headers()
                        self.wfile.write(payload.encode())

                server = ThreadingHTTPServer(("127.0.0.1", 0), FakeProvider)
                server.daemon_threads = True
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    spec = agent_spec("opencode", model, base_url=f"http://127.0.0.1:{server.server_port}/v1")
                    cfg_path = root / "opencode.json"
                    cfg_path.write_text(json.dumps(build_agent_config(spec, zg=False, max_model_turns=3)))
                    env = {"PATH": "/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin", "LANG": "en_US.UTF-8",
                           "OPENAI_API_KEY": "offline-fixture-not-a-real-key", "OPENCODE_DISABLE_MODELS_FETCH": "true",
                           "XDG_CONFIG_HOME": str(root / "config"), "XDG_DATA_HOME": str(root / "data"),
                           "XDG_STATE_HOME": str(root / "state"), "XDG_CACHE_HOME": str(root / "cache"),
                           **agent_environment(spec, config_path=str(cfg_path))}
                    command = build_agent_command(spec, "Read README.md and report its fixture value.", config_path=str(cfg_path), max_model_turns=3)
                    command[0] = binary
                    result = subprocess.run(command, env=env, cwd=corpus, capture_output=True, text=True, timeout=40)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    tasks = [body for body in requests if body.get("tools")]
                    self.assertEqual(len(tasks), 2, "Expected read request then final-answer request")
                    self.assertTrue(all(body.get("temperature") == 0 for body in tasks), [(body.get("model"), body.get("temperature")) for body in tasks])
                    self.assertTrue(all(body["model"] == model for body in tasks))
                    self.assertTrue(all({tool["function"]["name"] for tool in body["tools"]} == {"read", "grep", "glob"} for body in tasks))
                finally:
                    server.shutdown()
                    server.server_close()
                    thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
