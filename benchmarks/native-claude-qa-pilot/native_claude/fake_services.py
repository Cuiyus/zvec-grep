"""Bounded fake Anthropic transport; never a model or a QA-quality oracle.

Only advertised tool schemas are used. The original q001 instruction is unchanged.
Root/child classification uses direct user text, never assistant tool arguments.
"""
from __future__ import annotations

import copy
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import math
from pathlib import Path
import threading
import time
import traceback
from urllib.parse import urlsplit


_LEGACY_PATH = Path(__file__).resolve().parents[2] / "opencode-qa-pilot/repair_v2/fake_services.py"
_SPEC = importlib.util.spec_from_file_location("native_claude_frozen_embedding_fixture", _LEGACY_PATH)
_LEGACY = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_LEGACY)
embedding_response = _LEGACY.embedding_response
EMBEDDING_KEY = _LEGACY.EMBEDDING_KEY
QUERY = _LEGACY.QUERY
CHAT_KEY = "native-claude-v1-fake-anthropic-not-a-real-key"
ANTHROPIC_KEY = CHAT_KEY
MODEL = "qwen3.8-max"
ROOT_MARKER = "A service inserts documents into a zvec collection and exits abruptly before an explicit flush."
CHILD_MARKER = "NATIVE_CLAUDE_V1_CHILD_REQUEST"
FINAL_SENTINEL = "NATIVE_CLAUDE_V1_ROOT_COMPLETE"
CHILD_SENTINEL = "NATIVE_CLAUDE_V1_CHILD_COMPLETE"
BACKGROUND_SENTINEL = "NATIVE_CLAUDE_V1_BACKGROUND_COMPLETE"
PROFILES = ("baseline", "zvec-grep")
_STAGES = ("root_discover_mcp", "root_read", "root_mcp", "root_child", "child_read", "child_final", "root_final")


def _text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(block.get("text", "") for block in content
                         if isinstance(block, dict) and block.get("type") == "text")
    return ""


def classify_request(body: dict) -> str:
    fixture_history = any(
        isinstance(block, dict)
        and ((message.get("role") == "assistant" and block.get("type") == "tool_use"
              and str(block.get("id", "")).startswith("tool_native_claude_v1_"))
             or (message.get("role") == "user" and block.get("type") == "tool_result"
                 and str(block.get("tool_use_id", "")).startswith("tool_native_claude_v1_")))
        for message in body.get("messages", [])
        for block in (message.get("content", []) if isinstance(message.get("content"), list) else [])
    )
    # Native title/background requests may quote the entire original question.
    # They cannot satisfy the separately required root/child scripted stages.
    if not body.get("tools") and not fixture_history:
        return "background"
    direct = [_text(m.get("content")) for m in body.get("messages", []) if m.get("role") == "user"]
    # A tool_result block may contain the child sentinel, but is not direct text.
    if any(CHILD_MARKER in text for text in direct):
        return "child"
    if any(ROOT_MARKER in text for text in direct):
        return "root"
    return "unexpected" if body.get("tools") else "background"


def _tool(body: dict, names: tuple[str, ...]) -> dict:
    tools = body.get("tools", [])
    for name in names:
        matches = [t for t in tools if t.get("name", "").lower() == name.lower()]
        if len(matches) == 1:
            return matches[0]
        if matches:
            raise ValueError("ambiguous_tool_schema")
    raise ValueError("required_tool_not_advertised:" + names[0])


def _matches_json_type(value: object, kind: str) -> bool:
    if kind == "null":
        return value is None
    if kind == "boolean":
        return type(value) is bool
    if kind == "string":
        return isinstance(value, str)
    if kind == "object":
        return isinstance(value, dict)
    if kind == "array":
        return isinstance(value, list)
    if kind in ("integer", "number"):
        if type(value) is int:
            return True
        if type(value) is float and math.isfinite(value):
            return kind == "number" or value.is_integer()
    return False


def _checked_args(tool: dict, args: dict) -> dict:
    schema = tool.get("input_schema", {})
    props = schema.get("properties", {})
    if not isinstance(props, dict) or not args.keys() <= props.keys():
        raise ValueError("unsupported_tool_schema:" + tool["name"])
    if not set(schema.get("required", [])) <= args.keys():
        raise ValueError("unfilled_required_tool_argument:" + tool["name"])
    for name, value in args.items():
        spec = props[name]
        if "type" in spec:
            declared = spec["type"]
            kinds = [declared] if isinstance(declared, str) else declared
            keywords = {"string", "number", "integer", "object", "array", "boolean", "null"}
            if (not isinstance(kinds, list) or not kinds
                    or any(not isinstance(kind, str) or kind not in keywords for kind in kinds)
                    or len(set(kinds)) != len(kinds)):
                raise ValueError("unsupported_tool_schema_type:" + name)
            if not any(_matches_json_type(value, kind) for kind in kinds):
                raise ValueError("tool_argument_type:" + name + ":expected=" + "|".join(kinds)
                                 + ":actual=" + type(value).__name__)
        if "enum" in spec and value not in spec["enum"]:
            raise ValueError("tool_argument_enum:" + name)
    return args


def _history(body: dict, role: str) -> tuple[dict, dict]:
    calls, results = {}, {}
    for message in body.get("messages", []):
        blocks = message.get("content", [])
        if not isinstance(blocks, list):
            continue
        for block in blocks:
            if not isinstance(block, dict):
                continue
            if message.get("role") == "assistant" and block.get("type") == "tool_use":
                calls[block.get("id")] = block
            if message.get("role") == "user" and block.get("type") == "tool_result":
                results[block.get("tool_use_id")] = block
    for tool_id in calls:
        if str(tool_id).startswith("tool_native_claude_v1_"):
            # A child's inherited context can contain its still-running parent's
            # Agent call. Only this request's role must have completed its tools.
            if role == "child" and "_child_read" not in str(tool_id):
                continue
            if tool_id not in results:
                raise ValueError("fixture_tool_result_missing")
            if results[tool_id].get("is_error"):
                raise ValueError("fixture_tool_result_error")
    return calls, results


def scripted_response(body: dict, profile: str, *, background_index: int = 0) -> dict:
    """Derive one reply from the actual incoming transcript, with no fake child logs."""
    if profile not in PROFILES:
        raise ValueError("invalid_profile")
    if body.get("model") != MODEL:
        raise ValueError("wrong_model")
    if type(body.get("max_tokens")) is not int or not 1 <= body["max_tokens"] <= 131072:
        raise ValueError("invalid_max_tokens")
    role = classify_request(body)
    if role == "unexpected":
        raise ValueError("unmarked_request_with_tools")
    calls, results = _history(body, role)
    prefix = "tool_native_claude_v1_" + profile.replace("-", "_") + "_"
    seen = {tool_id[len(prefix):] for tool_id in calls if isinstance(tool_id, str) and tool_id.startswith(prefix)}
    selected, args = None, None
    if role == "background":
        stage, text = "background_" + str(background_index), BACKGROUND_SENTINEL
    elif role == "child" and "child_read" not in seen:
        stage = "child_read"
    elif role == "child":
        stage, text = "child_final", CHILD_SENTINEL
    elif profile == "baseline" and "root_read" not in seen:
        if any("zvec_grep_search" in t.get("name", "") for t in body.get("tools", [])):
            raise ValueError("baseline_has_zg_tool")
        stage = "root_read"
    elif profile == "zvec-grep" and "root_mcp" not in seen:
        matches = [t for t in body.get("tools", []) if "zvec_grep_search" in t.get("name", "")]
        if len(matches) > 1:
            raise ValueError("ambiguous_mcp_search")
        selected = matches[0] if matches else None
        if (selected is None or selected.get("defer_loading")) and "root_discover_mcp" not in seen:
            stage = "root_discover_mcp"
            deferred_name = selected.get("name") if selected else None
            selected = _tool(body, ("ToolSearch",))
            args = {"query": "select:" + deferred_name if deferred_name else "+zvec_grep_search"}
        else:
            if selected is None:
                raise ValueError("mcp_schema_not_loaded_after_discovery")
            stage, args = "root_mcp", {"root": "/workspace", "query": QUERY}
    elif "root_child" not in seen:
        stage = "root_child"
        selected = _tool(body, ("Agent", "Task"))
        props = selected.get("input_schema", {}).get("properties", {})
        child_type = "general-purpose"
        choices = props.get("subagent_type", {}).get("enum")
        if choices and child_type not in choices:
            raise ValueError("general_purpose_child_not_advertised")
        args = {"description": "Native offline child read fixture", "subagent_type": child_type,
                "prompt": CHILD_MARKER + "\nRead the first twelve lines of /workspace/README.md. This is only an offline transport fixture; return the completion sentinel supplied by the fixture."}
        if "run_in_background" in props:
            args["run_in_background"] = False
    else:
        if CHILD_SENTINEL not in _text(results[prefix + "root_child"].get("content")):
            raise ValueError("child_completion_missing_from_tool_result")
        stage, text = "root_final", FINAL_SENTINEL
    if stage in ("root_read", "child_read"):
        selected = _tool(body, ("Read",))
        props = selected.get("input_schema", {}).get("properties", {})
        if "file_path" not in props:
            raise ValueError("unexpected_read_schema")
        args = {"file_path": "/workspace/README.md"}
        if "limit" in props:
            args["limit"] = 12
    if selected:
        args = _checked_args(selected, args)
        content = [{"type": "tool_use", "id": prefix + stage, "name": selected["name"], "input": args}]
        stop = "tool_use"
    else:
        content, stop = [{"type": "text", "text": text}], "end_turn"
    ordinal = _STAGES.index(stage) + 1 if stage in _STAGES else 20 + background_index
    usage = {"input_tokens": 100 + ordinal, "output_tokens": 20 + ordinal,
             "cache_read_input_tokens": 30 + ordinal, "cache_creation_input_tokens": 4 + ordinal}
    message_id = "msg_native_claude_v1_" + profile.replace("-", "_") + "_" + stage
    response = {"id": message_id, "type": "message", "role": "assistant", "model": MODEL,
                "content": content, "stop_reason": stop, "stop_sequence": None, "usage": usage}
    return {"classification": role, "stage": stage, "message_id": message_id,
            "usage": usage, "selected_tool_schema": copy.deepcopy(selected), "response": response}


def stream_events(response: dict) -> list[dict]:
    message = {**copy.deepcopy(response), "content": [], "stop_reason": None,
               "usage": {**response["usage"], "output_tokens": 0}}
    events = [{"type": "message_start", "message": message}]
    for index, block in enumerate(response["content"]):
        if block["type"] == "tool_use":
            start = {**block, "input": {}}
            delta = {"type": "input_json_delta", "partial_json": json.dumps(block["input"], separators=(",", ":"))}
        else:
            start = {"type": "text", "text": ""}
            delta = {"type": "text_delta", "text": block["text"]}
        events.extend([{"type": "content_block_start", "index": index, "content_block": start},
                       {"type": "content_block_delta", "index": index, "delta": delta},
                       {"type": "content_block_stop", "index": index}])
    events.extend([{"type": "message_delta", "delta": {"stop_reason": response["stop_reason"], "stop_sequence": None},
                    "usage": dict(response["usage"])}, {"type": "message_stop"}])
    return events


class FakeServices:
    """One profile per instance. Full Anthropic bodies are kept only after fake auth.

    Embeddings reuse the frozen deterministic implementation and retain compact
    input hashes/shape plus response hash, avoiding duplicated 1024-vector traces.
    URLs returned here are host-loopback URLs; containers use the same port with
    their explicitly configured host-gateway name.
    """
    def __init__(self, host="0.0.0.0", *, profile="baseline", output: Path | None = None,
                 max_chat_requests=40, max_embedding_requests=2000):
        self.host, self._profile = host, profile
        if profile not in PROFILES:
            raise ValueError("invalid_profile")
        self.output = Path(output) if output is not None else None
        if self.output:
            self.output.mkdir(parents=True, exist_ok=True)
        self.rows, self.errors, self.rejected_requests = [], [], []
        self.lock = threading.RLock()
        self.max_chat_requests, self.max_embedding_requests = max_chat_requests, max_embedding_requests
        self._started = self._closed = False
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                at = time.time()
                rejected_chat = None
                accepted = False
                try:
                    path = urlsplit(self.path).path
                    kind = "embedding" if path == "/v1/embeddings" else "count_tokens" if path in ("/anthropic/v1/messages/count_tokens", "/v1/messages/count_tokens") else "chat" if path in ("/anthropic/v1/messages", "/v1/messages") else None
                    if kind is None:
                        raise ValueError("unexpected_path")
                    expected = EMBEDDING_KEY if kind == "embedding" else CHAT_KEY
                    api_key, authorization = self.headers.get("x-api-key"), self.headers.get("Authorization")
                    if (api_key not in (None, expected) or authorization not in (None, "Bearer " + expected)
                            or not (api_key == expected or authorization == "Bearer " + expected)):
                        raise ValueError("credential_not_fixture")
                    size = int(self.headers.get("Content-Length", "0"))
                    if not 0 < size <= 32_000_000:
                        raise ValueError("invalid_request_size")
                    body = json.loads(self.rfile.read(size))
                    if not isinstance(body, dict):
                        raise ValueError("request_not_object")
                    if kind == "chat":
                        # Only fake-authenticated, parsed chat bodies may enter
                        # rejection evidence; never capture authentication headers.
                        rejected_chat = {"kind": kind, "profile": owner.profile, "at_unix": at,
                                         "path": self.path, "authorization_is_exact_fixture": True,
                                         "body": copy.deepcopy(body)}
                    with owner.lock:
                        row = {"sequence": len(owner.rows) + 1, "kind": kind, "profile": owner.profile,
                               "at_unix": at, "path": self.path, "authorization_is_exact_fixture": True}
                        row["request_id"] = "req_native_claude_v1_" + str(row["sequence"])
                        if kind == "embedding":
                            if owner.profile != "zvec-grep":
                                raise ValueError("embedding_outside_treatment")
                            if sum(r["kind"] == kind for r in owner.rows) >= owner.max_embedding_requests:
                                raise ValueError("embedding_request_budget_exceeded")
                            response, shape = embedding_response(body)
                            row["body_shape"] = shape
                            content_type = "application/json"
                            payload = json.dumps(response, separators=(",", ":")).encode()
                        elif kind == "count_tokens":
                            if body.get("model") != MODEL:
                                raise ValueError("wrong_model")
                            if sum(r["kind"] == kind for r in owner.rows) >= owner.max_chat_requests:
                                raise ValueError("count_tokens_budget_exceeded")
                            response = {"input_tokens": 100}
                            row.update(body=body, response=response, classification="token_count")
                            content_type, payload = "application/json", json.dumps(response).encode()
                        else:
                            chats = [r for r in owner.rows if r["kind"] == "chat"]
                            if len(chats) >= owner.max_chat_requests:
                                raise ValueError("chat_request_budget_exceeded")
                            scripted = scripted_response(body, owner.profile, background_index=sum(r["classification"] == "background" for r in chats) + 1)
                            if any(r.get("message_id") == scripted["message_id"] for r in chats):
                                raise ValueError("duplicate_scripted_stage_or_retry")
                            if scripted["stage"] == "root_final" and not any(r.get("stage") == "child_final" for r in chats):
                                raise ValueError("root_completed_without_actual_child_request")
                            row.update(body=body, **scripted)
                            if body.get("stream") is True:
                                events = stream_events(scripted["response"])
                                row["events"] = events
                                payload = "".join("event: " + e["type"] + "\ndata: " + json.dumps(e, separators=(",", ":")) + "\n\n" for e in events).encode()
                                content_type = "text/event-stream"
                            else:
                                content_type, payload = "application/json", json.dumps(scripted["response"]).encode()
                        row.update(response_body_sha256=hashlib.sha256(payload).hexdigest(), response_body_bytes=len(payload), response_content_type=content_type)
                        owner.rows.append(row)
                        accepted = True
                        owner._flush_row(row)
                    self.send_response(200)
                    self.send_header("Content-Type", content_type)
                    self.send_header("Content-Length", str(len(payload)))
                    self.send_header("request-id", row["request_id"])
                    self.end_headers()
                    self.wfile.write(payload)
                except Exception as exc:
                    # Never include request headers or arbitrary invalid JSON in errors.
                    reason = str(exc) if isinstance(exc, ValueError) and not isinstance(exc, json.JSONDecodeError) else type(exc).__name__
                    error = {"at_unix": at, "reason": reason, "exception_type": type(exc).__name__,
                             "traceback": [{"file": Path(frame.filename).name,
                                            "function": frame.name, "line": frame.lineno}
                                           for frame in traceback.extract_tb(exc.__traceback__)]}
                    with owner.lock:
                        owner.errors.append(error)
                        if rejected_chat is not None and not accepted:
                            rejected_chat.update(rejected_sequence=len(owner.rejected_requests) + 1, error=error)
                            owner.rejected_requests.append(rejected_chat)
                            if owner.output:
                                with (owner.output / "rejected-requests.jsonl").open("a") as handle:
                                    handle.write(json.dumps(rejected_chat, sort_keys=True) + "\n")
                        if owner.output:
                            (owner.output / "errors.json").write_text(json.dumps(owner.errors, indent=2) + "\n")
                    self.send_error(400, "native_offline_fixture_rejected_request")

        self.server = ThreadingHTTPServer((host, 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def profile(self):
        return self._profile

    @profile.setter
    def profile(self, value):
        if value not in PROFILES or self.rows:
            raise ValueError("profile_frozen_after_first_request")
        self._profile = value

    @property
    def port(self):
        return self.server.server_port

    @property
    def base_url(self):
        return f"http://127.0.0.1:{self.port}"

    @property
    def anthropic_base_url(self):
        return self.base_url + "/anthropic"

    @property
    def embedding_url(self):
        return self.base_url + "/v1/embeddings"

    def _flush_row(self, row):
        if self.output:
            with (self.output / "requests.jsonl").open("a") as handle:
                handle.write(json.dumps(row, sort_keys=True) + "\n")

    def start(self):
        if self._closed:
            raise RuntimeError("service_already_closed")
        if not self._started:
            self.thread.start()
            self._started = True
        return self

    def shutdown(self):
        if not self._closed:
            if self._started:
                self.server.shutdown()
                self.thread.join(timeout=5)
            self.server.server_close()
            self._closed = True
            if self.output:
                self.dump_trace(self.output / "trace.json")

    def __enter__(self):
        return self.start()

    def __exit__(self, *_):
        self.shutdown()

    def dump_trace(self, path: Path | None = None):
        with self.lock:
            trace = {"schema_version": 1, "scope": "native-claude-fake-services-v1", "profile": self.profile,
                     "quality_evaluated": False, "real_provider_calls_authorized": False,
                     "embedding_fixture_source_sha256": hashlib.sha256(_LEGACY_PATH.read_bytes()).hexdigest(),
                     "rows": copy.deepcopy(self.rows), "errors": copy.deepcopy(self.errors),
                     "rejected_requests": copy.deepcopy(self.rejected_requests)}
        if path is not None:
            Path(path).write_text(json.dumps(trace, indent=2) + "\n")
        return trace

    def validate(self, *, require_embeddings=True):
        with self.lock:
            if self.errors:
                raise ValueError("fake_service_errors")
            chat = [r for r in self.rows if r["kind"] == "chat"]
            actual = [r["stage"] for r in chat if r["classification"] != "background"]
            first = "root_read" if self.profile == "baseline" else "root_mcp"
            expected = [first, "root_child", "child_read", "child_final", "root_final"]
            if actual and actual[0] == "root_discover_mcp" and self.profile == "zvec-grep":
                expected.insert(0, "root_discover_mcp")
            if actual != expected:
                raise ValueError("unexpected_scripted_stage_sequence")
            embeds = [r for r in self.rows if r["kind"] == "embedding"]
            if self.profile == "zvec-grep" and require_embeddings:
                if not any(r["body_shape"]["is_query_text"] for r in embeds):
                    raise ValueError("query_embedding_missing")
                if not any(r["at_unix"] < chat[0]["at_unix"] for r in embeds):
                    raise ValueError("setup_embedding_missing")
            return {"valid": True, "profile": self.profile, "stages": actual,
                    "root_requests": sum(r["classification"] == "root" for r in chat),
                    "child_requests": sum(r["classification"] == "child" for r in chat),
                    "background_requests": sum(r["classification"] == "background" for r in chat),
                    "embedding_requests": len(embeds), "quality_evaluated": False}
