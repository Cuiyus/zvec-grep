from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from native_claude.fake_services import (
    CHAT_KEY, CHILD_MARKER, CHILD_SENTINEL, EMBEDDING_KEY, FINAL_SENTINEL,
    MODEL, QUERY, ROOT_MARKER, FakeServices, classify_request,
    embedding_response, scripted_response, stream_events,
)


def tool(name, props, required=()):
    return {"name": name, "description": "Native schema fixture for " + name,
            "input_schema": {"type": "object", "properties": props, "required": list(required)}}


def request(*, child=False, treatment=False, agent_name="Agent", deferred=False):
    tools = [tool("Read", {"file_path": {"type": "string"}, "limit": {"type": "integer"}}, ["file_path"]),
             tool(agent_name, {"description": {"type": "string"}, "prompt": {"type": "string"},
                               "subagent_type": {"type": "string"}, "run_in_background": {"type": "boolean"}},
                  ["description", "prompt", "subagent_type"])]
    if treatment:
        mcp = tool("mcp__zvec_grep__zvec_grep_search", {"root": {"type": "string"}, "query": {"type": "string"}}, ["root"])
        if deferred:
            mcp["defer_loading"] = True
            tools.append(tool("ToolSearch", {"query": {"type": "string"}}, ["query"]))
        tools.append(mcp)
    return {"model": MODEL, "max_tokens": 8192, "stream": True, "output_config": {"effort": "high"},
            "messages": [{"role": "user", "content": CHILD_MARKER if child else ROOT_MARKER}], "tools": tools}


def advance(body, reply, result="native tool returned source bytes"):
    body["messages"].append({"role": "assistant", "content": reply["content"]})
    calls = [b for b in reply["content"] if b["type"] == "tool_use"]
    for call in calls:
        body["messages"].append({"role": "user", "content": [{"type": "tool_result", "tool_use_id": call["id"], "content": result}]})


def post(service, body, *, path="/anthropic/v1/messages?beta=true", key=CHAT_KEY):
    req = Request(service.base_url + path, data=json.dumps(body).encode(),
                  headers={"x-api-key": key, "Content-Type": "application/json"})
    with urlopen(req, timeout=5) as response:
        return response.read(), dict(response.headers)


def exchange(service, body):
    post(service, body)
    return service.rows[-1]["response"]


class FakeServicesTests(unittest.TestCase):
    def test_root_child_classification_ignores_assistant_arguments_and_tool_results(self):
        body = request()
        body["messages"].extend([
            {"role": "assistant", "content": [{"type": "tool_use", "name": "Agent", "id": "parent", "input": {"prompt": CHILD_MARKER}}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "parent", "content": CHILD_MARKER + CHILD_SENTINEL}]}])
        self.assertEqual(classify_request(body), "root")
        child = copy.deepcopy(body)
        child["messages"].append({"role": "user", "content": [{"type": "text", "text": CHILD_MARKER}]})
        self.assertEqual(classify_request(child), "child")
        self.assertEqual(classify_request({"messages": [{"role": "user", "content": "title"}]}), "background")
        self.assertEqual(classify_request({"messages": [], "tools": body["tools"]}), "unexpected")

    def test_dynamic_agent_and_task_schema_names_and_required_arguments(self):
        for agent_name in ("Agent", "Task"):
            body = request(agent_name=agent_name)
            first = scripted_response(body, "baseline")
            advance(body, first["response"])
            child = scripted_response(body, "baseline")
            call = child["response"]["content"][0]
            self.assertEqual(call["name"], agent_name)
            self.assertIn(CHILD_MARKER, call["input"]["prompt"])
            self.assertFalse(call["input"]["run_in_background"])
            self.assertEqual(call["input"]["subagent_type"], "general-purpose")
            body["tools"][1]["input_schema"]["required"].append("unknown_required")
            with self.assertRaisesRegex(ValueError, "unfilled_required"):
                scripted_response(body, "baseline")

    def test_toolless_quoted_question_is_background_only_without_fixture_history(self):
        for marker in (ROOT_MARKER, CHILD_MARKER):
            body = {"model": MODEL, "max_tokens": 20, "messages": [{"role": "user", "content": marker}]}
            self.assertEqual(classify_request(body), "background")
            self.assertEqual(scripted_response(body, "baseline")["classification"], "background")
            with self.assertRaisesRegex(ValueError, "wrong_model"):
                scripted_response({**body, "model": "unexpected-fallback-model"}, "baseline")
        child = request(child=True)
        advance(child, scripted_response(child, "baseline")["response"])
        child["tools"] = []
        self.assertEqual(classify_request(child), "child")
        self.assertEqual(scripted_response(child, "baseline")["stage"], "child_final")
        with FakeServices(host="127.0.0.1") as service:
            post(service, {"model": MODEL, "max_tokens": 20,
                           "messages": [{"role": "user", "content": ROOT_MARKER}]})
            with self.assertRaisesRegex(ValueError, "unexpected_scripted_stage_sequence"):
                service.validate()

    def test_deferred_mcp_discovery_requires_actual_loaded_schema(self):
        body = request(treatment=True, deferred=True)
        discovery = scripted_response(body, "zvec-grep")
        self.assertEqual(discovery["stage"], "root_discover_mcp")
        self.assertEqual(discovery["response"]["content"][0]["input"], {"query": "select:mcp__zvec_grep__zvec_grep_search"})
        advance(body, discovery["response"], "loaded mcp__zvec_grep__zvec_grep_search")
        mcp = scripted_response(body, "zvec-grep")
        self.assertEqual(mcp["stage"], "root_mcp")
        self.assertEqual(mcp["response"]["content"][0]["input"], {"root": "/workspace", "query": QUERY})
        body["tools"][-1]["input_schema"] = {}
        with self.assertRaisesRegex(ValueError, "unsupported_tool_schema"):
            scripted_response(body, "zvec-grep")

    def test_child_may_inherit_pending_parent_call_but_not_skip_own_tool_result(self):
        child = request(child=True)
        child["messages"].insert(0, {"role": "assistant", "content": [{"type": "tool_use", "id": "tool_native_claude_v1_baseline_root_child", "name": "Agent", "input": {}}]})
        reply = scripted_response(child, "baseline")
        self.assertEqual(reply["stage"], "child_read")
        child["messages"].append({"role": "assistant", "content": reply["response"]["content"]})
        with self.assertRaisesRegex(ValueError, "tool_result_missing"):
            scripted_response(child, "baseline")

    def test_sse_preserves_unique_message_and_complete_four_component_final_usage(self):
        seen = set()
        for profile in ("baseline", "zvec-grep"):
            for child in (False, True):
                reply = scripted_response(request(child=child, treatment=profile == "zvec-grep"), profile)
                events = stream_events(reply["response"])
                self.assertEqual(events[0]["type"], "message_start")
                self.assertEqual(events[0]["message"]["usage"]["output_tokens"], 0)
                self.assertEqual(events[-2]["usage"], reply["usage"])
                self.assertEqual(set(reply["usage"]), {"input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"})
                self.assertTrue(all(type(v) is int and v > 0 for v in reply["usage"].values()))
                self.assertNotIn(reply["message_id"], seen)
                seen.add(reply["message_id"])
                self.assertEqual(events[-1], {"type": "message_stop"})
                self.assertEqual(json.loads(events[2]["delta"]["partial_json"]), reply["response"]["content"][0]["input"])

    def test_actual_http_root_child_sequence_and_complete_trace_for_both_profiles(self):
        for profile in ("baseline", "zvec-grep"):
            with self.subTest(profile=profile), tempfile.TemporaryDirectory() as tmp, FakeServices(host="127.0.0.1", profile=profile, output=Path(tmp)) as service:
                root = request(treatment=profile == "zvec-grep")
                if profile == "zvec-grep":
                    embedding = {"model": "qwen3.7-text-embedding", "dimensions": 1024, "encoding_format": "float", "input": ["source setup text"]}
                    post(service, embedding, path="/v1/embeddings", key=EMBEDDING_KEY)
                first = exchange(service, root)
                advance(root, first)
                if profile == "zvec-grep":
                    post(service, {**embedding, "input": [QUERY]}, path="/v1/embeddings", key=EMBEDDING_KEY)
                delegation = exchange(service, root)
                child = request(child=True, treatment=profile == "zvec-grep")
                advance(child, exchange(service, child))
                child_final = exchange(service, child)
                self.assertEqual(child_final["content"][0]["text"], CHILD_SENTINEL)
                advance(root, delegation, CHILD_SENTINEL)
                root_final = exchange(service, root)
                self.assertEqual(root_final["content"][0]["text"], FINAL_SENTINEL)
                summary = service.validate()
                self.assertEqual((summary["root_requests"], summary["child_requests"]), (3, 2))
                trace = service.dump_trace(Path(tmp) / "captured.json")
                self.assertEqual(trace, json.loads((Path(tmp) / "captured.json").read_text()))
                self.assertEqual(len((Path(tmp) / "requests.jsonl").read_text().splitlines()), len(trace["rows"]))
                self.assertEqual(trace["errors"], [])
                for row in trace["rows"]:
                    if row["kind"] == "chat":
                        encoded = "".join("event: " + e["type"] + "\ndata: " + json.dumps(e, separators=(",", ":")) + "\n\n" for e in row["events"]).encode()
                        self.assertEqual(hashlib.sha256(encoded).hexdigest(), row["response_body_sha256"])

    def test_root_final_requires_real_child_requests_and_retries_rejected(self):
        with FakeServices(host="127.0.0.1") as service:
            body = request()
            advance(body, exchange(service, body))
            advance(body, exchange(service, body), CHILD_SENTINEL)
            with self.assertRaises(HTTPError):
                post(service, body)
            self.assertEqual(service.errors[-1]["reason"], "root_completed_without_actual_child_request")
        with FakeServices(host="127.0.0.1") as service:
            post(service, request())
            with self.assertRaises(HTTPError):
                post(service, request())
            self.assertEqual(service.errors[-1]["reason"], "duplicate_scripted_stage_or_retry")

    def test_nonfixture_auth_not_saved_and_baseline_embeddings_rejected(self):
        with tempfile.TemporaryDirectory() as tmp, FakeServices(host="127.0.0.1", output=Path(tmp)) as service:
            with self.assertRaises(HTTPError):
                post(service, request(), key="must-not-be-recorded")
            self.assertEqual(service.rows, [])
            self.assertNotIn("must-not-be-recorded", json.dumps(service.dump_trace()))
            with self.assertRaises(HTTPError):
                post(service, {}, path="/v1/embeddings", key=EMBEDDING_KEY)
            self.assertEqual(service.errors[-1]["reason"], "embedding_outside_treatment")

    def test_model_tool_result_errors_and_baseline_mcp_fail_closed(self):
        for changed in (dict(model="other"), dict(max_tokens=0)):
            with self.assertRaises(ValueError):
                scripted_response({**request(), **changed}, "baseline")
        with self.assertRaisesRegex(ValueError, "baseline_has_zg_tool"):
            scripted_response(request(treatment=True), "baseline")
        body = request()
        advance(body, scripted_response(body, "baseline")["response"])
        body["messages"][-1]["content"][0]["is_error"] = True
        with self.assertRaisesRegex(ValueError, "tool_result_error"):
            scripted_response(body, "baseline")

    def test_background_count_tokens_nonstream_and_lifecycle_are_separate(self):
        service = FakeServices(host="127.0.0.1")
        self.assertIs(service.start(), service.start())
        background = {"model": MODEL, "max_tokens": 20, "messages": [{"role": "user", "content": "title fixture"}]}
        payload, headers = post(service, background)
        self.assertEqual(json.loads(payload)["stop_reason"], "end_turn")
        self.assertEqual(service.rows[-1]["classification"], "background")
        self.assertEqual(headers["request-id"], service.rows[-1]["request_id"])
        payload, _ = post(service, {"model": MODEL, "messages": []}, path="/anthropic/v1/messages/count_tokens")
        self.assertEqual(json.loads(payload), {"input_tokens": 100})
        self.assertEqual(service.rows[-1]["kind"], "count_tokens")
        with self.assertRaisesRegex(ValueError, "profile_frozen"):
            service.profile = "zvec-grep"
        service.shutdown()
        service.shutdown()
        with self.assertRaisesRegex(RuntimeError, "already_closed"):
            service.start()

    def test_reused_embedding_contract_and_dimensions(self):
        body = {"model": "qwen3.7-text-embedding", "dimensions": 1024, "encoding_format": "float", "input": [QUERY, "source"]}
        first, shape = embedding_response(body)
        self.assertEqual(first, embedding_response(body)[0])
        self.assertEqual(shape["batch_size"], 2)
        self.assertTrue(all(len(row["embedding"]) == 1024 and sum(row["embedding"]) == 2 for row in first["data"]))


if __name__ == "__main__":
    unittest.main()
