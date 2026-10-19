#!/usr/bin/env python3
"""Verify Claude tool exposure and request settings against a local API fixture.

Run inside the pilot image with Docker --network=none. No real credentials are
inherited and no inference or embedding request is sent to an external service.
The default treatment uses the image's real zg MCP tools/list; --fixture-mcp
exists only for checking the Claude binary on a host without zg installed.
"""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading

from run import parse_stream

NATIVE_TOOLS = {"Glob", "Grep", "Read"}
ZG_TOOL = "mcp__zvec_grep__zvec_grep_search"
SENTINEL = "PILOT_CLAUDE_MD_MUST_NOT_BE_LOADED_20261019"
ANSWER = "Offline fixture response."


def serve_mcp_fixture() -> None:
    for line in sys.stdin:
        request = json.loads(line)
        if "id" not in request:
            continue
        if request.get("method") == "initialize":
            result = {"protocolVersion": request["params"]["protocolVersion"],
                      "capabilities": {"tools": {}},
                      "serverInfo": {"name": "fixture-zvec-grep", "version": "0.0.0"}}
        elif request.get("method") == "tools/list":
            result = {"tools": [{"name": "zvec_grep_search",
                                "description": "Offline transport fixture; never searches real data.",
                                "inputSchema": {"type": "object", "properties": {
                                    "query": {"type": "string"}}, "required": ["query"]}}]}
        else:
            result = {}
        print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}), flush=True)


def handler_for(records: list[dict]):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers.get("content-length", 0))))
            record = {key: body[key] for key in (
                "model", "max_tokens", "temperature", "top_p", "top_k", "thinking",
                "output_config", "stream") if key in body}
            record.update(path=self.path, tool_names=[tool.get("name") for tool in body.get("tools", [])],
                          claude_md_loaded=SENTINEL in json.dumps(body))
            records.append(record)
            message = {"id": "msg_offline_fixture", "type": "message", "role": "assistant",
                       "content": [], "model": body.get("model"), "stop_reason": None,
                       "stop_sequence": None, "usage": {"input_tokens": 100, "output_tokens": 0,
                       "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}}
            events = [
                ("message_start", {"type": "message_start", "message": message}),
                ("content_block_start", {"type": "content_block_start", "index": 0,
                                         "content_block": {"type": "text", "text": ""}}),
                ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                         "delta": {"type": "text_delta", "text": ANSWER}}),
                ("content_block_stop", {"type": "content_block_stop", "index": 0}),
                ("message_delta", {"type": "message_delta",
                                   "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                                   "usage": {"output_tokens": 7}}),
                ("message_stop", {"type": "message_stop"}),
            ]
            payload = "".join("event: " + name + "\ndata: " + json.dumps(event) + "\n\n"
                              for name, event in events).encode()
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.send_header("content-length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
    return Handler


def probe_profile(args, profile: str, root: Path) -> dict:
    root.mkdir()
    home, workspace = root / "home", root / "workspace"
    home.mkdir()
    workspace.mkdir()
    (workspace / "CLAUDE.md").write_text(SENTINEL)
    mcp = {"mcpServers": {}}
    if profile == "zg":
        command, argv = (sys.executable, [str(Path(__file__).resolve()), "--serve-mcp-fixture"]) \
            if args.fixture_mcp else (args.zg, ["server", "--stdio", "--mcp-toolset", "agent"])
        mcp["mcpServers"]["zvec_grep"] = {"type": "stdio", "command": command, "args": argv}
    (root / "mcp.json").write_text(json.dumps(mcp))
    (root / "settings.json").write_text(json.dumps({"permissions": {"deny": ["Read(//root/**)"]}}))
    records = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(records))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    # Deliberately construct, rather than copy, the environment. A caller may
    # already have production API keys set when this paid-free check runs.
    env = {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "HOME": str(home), "CLAUDE_CONFIG_DIR": str(home / ".claude"),
        "ZVEC_GREP_HOME": str(home / ".zvec-grep"),
        "ANTHROPIC_API_KEY": "offline-fixture-not-a-real-api-key",
        "ANTHROPIC_BASE_URL": "http://127.0.0.1:" + str(server.server_port),
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1", "DISABLE_NONESSENTIAL_TRAFFIC": "1",
        "DISABLE_AUTOUPDATER": "1", "CLAUDE_CODE_DISABLE_CLAUDE_MDS": "1",
        "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1", "CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING": "1",
        "CLAUDE_CODE_MAX_OUTPUT_TOKENS": str(args.max_output_tokens),
        "IS_SANDBOX": "1", "NO_PROXY": "127.0.0.1,localhost", "NO_COLOR": "1",
    }
    for name in ("ANTHROPIC_MODEL", "ANTHROPIC_DEFAULT_SONNET_MODEL", "ANTHROPIC_DEFAULT_OPUS_MODEL",
                 "ANTHROPIC_DEFAULT_HAIKU_MODEL", "CLAUDE_CODE_SUBAGENT_MODEL"):
        env[name] = args.model
    allowed = NATIVE_TOOLS | ({ZG_TOOL} if profile == "zg" else set())
    command = [args.claude, "--print", "--verbose", "--output-format", "stream-json",
               "--model", args.model, "--max-turns", "2", "--effort", args.effort,
               "--permission-mode", "dontAsk", "--tools", ",".join(sorted(NATIVE_TOOLS)),
               "--allowedTools", ",".join(sorted(allowed)), "--disable-slash-commands",
               "--no-session-persistence", "--setting-sources", "", "--settings", str(root / "settings.json"),
               "--strict-mcp-config", "--mcp-config", str(root / "mcp.json"),
               "--append-system-prompt", "This is an offline protocol fixture, not a QA trial.",
               "--", "Reply with Offline fixture response."]
    try:
        result = subprocess.run(command, env=env, cwd=workspace, text=True, capture_output=True, timeout=90)
    finally:
        server.shutdown()
        server.server_close()
    metrics = parse_stream(result.stdout, args.model, profile, ZG_TOOL)
    errors = list(metrics["errors"])
    if result.returncode:
        errors.append("process_exit:" + str(result.returncode))
    if len(records) != 1:
        errors.append("expected_one_local_request")
    for request in records:
        if set(request["tool_names"]) != allowed:
            errors.append("request_tool_inventory_mismatch")
        if request.get("model") != args.model:
            errors.append("request_model_mismatch")
        if request.get("max_tokens") != args.max_output_tokens:
            errors.append("request_output_limit_mismatch")
        if request.get("output_config", {}).get("effort") != args.effort:
            errors.append("request_effort_mismatch")
        if request.get("claude_md_loaded"):
            errors.append("repository_instructions_loaded")
        if "temperature" in request or "top_p" in request:
            errors.append("unexpected_explicit_sampling")
    if metrics.get("answer") != ANSWER or metrics.get("input_tokens_including_cache") != 100 \
            or metrics.get("output_tokens") != 7:
        errors.append("fixture_response_or_usage_mismatch")
    return {"profile": profile, "valid": not errors, "errors": errors,
            "requests": records, "metrics": metrics, "stderr": result.stderr,
            "stream": result.stdout}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--claude", default="claude")
    parser.add_argument("--zg", default="zg")
    parser.add_argument("--model", default="qwen3.8-max")
    parser.add_argument("--effort", default="high", choices=("low", "medium", "high"))
    parser.add_argument("--max-output-tokens", default=4096, type=int)
    parser.add_argument("--fixture-mcp", action="store_true")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="zg-cli-probe-") as directory:
        rows = [probe_profile(args, profile, Path(directory) / profile) for profile in ("baseline", "zg")]
    report = {"schema_version": 1, "purpose": "offline CLI protocol validation; not benchmark evidence",
              "external_inference_requests": 0, "credentials": "dummy fixture only",
              "mcp_source": "stdio fixture" if args.fixture_mcp else "runtime zg agent toolset",
              "valid": all(row["valid"] for row in rows), "profiles": rows}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"valid": report["valid"], "output": str(args.output),
                      "errors": {row["profile"]: row["errors"] for row in rows}}))
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    if "--serve-mcp-fixture" in sys.argv:
        serve_mcp_fixture()
    else:
        raise SystemExit(main())
