#!/usr/bin/env python3
"""Exercise the real budget hook with Claude 2.1.212 and offline API/MCP fixtures.

Run inside the pilot image with --network=none. No external inference, real
credentials, task source, or reference answer is used by this preflight.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import threading
import time

from run import parse_stream
from tool_budget import FINAL_MESSAGE

MODEL = "qwen3.8-max"
ZG_TOOL = "mcp__zvec_grep__zvec_grep_search"
ANSWER = "Offline final answer using existing evidence."
NATIVE_MARKER = "NATIVE_READ_EXECUTED_SENTINEL"
MCP_MARKER = "MCP_EXECUTED_SENTINEL"


def initial_state(path: Path, elapsed: int = 0) -> None:
    path.write_text(json.dumps({"schema_version": 1, "started_at_epoch": time.time() - elapsed, "calls": {}}))


def hook_command(args, state: Path, limit: int) -> list[str]:
    return [sys.executable, str(args.hook), "--state", str(state),
            "--limit", str(limit), "--research-seconds", "600"]


def serve_mcp(log_path: str) -> None:
    for line in sys.stdin:
        call = json.loads(line)
        if "id" not in call:
            continue
        method = call.get("method")
        if method == "initialize":
            result = {"protocolVersion": call["params"]["protocolVersion"], "capabilities": {"tools": {}},
                      "serverInfo": {"name": "budget-fixture", "version": "1"}}
        elif method == "tools/list":
            result = {"tools": [{"name": "zvec_grep_search", "description": "Offline budget fixture.",
                                 "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}},
                                                 "required": ["query"]}}]}
        elif method == "tools/call":
            with open(log_path, "a") as handle:
                handle.write(json.dumps(call) + "\n")
            result = {"content": [{"type": "text", "text": MCP_MARKER}], "isError": False}
        else:
            result = {}
        print(json.dumps({"jsonrpc": "2.0", "id": call["id"], "result": result}), flush=True)


def handler_for(records: list[dict], batches: list[list[dict]]):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            records.append(body)
            number = len(records)
            content = batches[number - 1] if number <= len(batches) else [{"type": "text", "text": ANSWER}]
            stop = "tool_use" if any(block["type"] == "tool_use" for block in content) else "end_turn"
            message = {"id": f"msg_fixture_{number}", "type": "message", "role": "assistant", "content": [],
                       "model": body["model"], "stop_reason": None, "stop_sequence": None,
                       "usage": {"input_tokens": 100, "output_tokens": 0,
                                 "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}}
            events = [("message_start", {"type": "message_start", "message": message})]
            for index, block in enumerate(content):
                if block["type"] == "tool_use":
                    opening = {**block, "input": {}}
                    delta = {"type": "input_json_delta", "partial_json": json.dumps(block["input"])}
                else:
                    opening = {"type": "text", "text": ""}
                    delta = {"type": "text_delta", "text": block["text"]}
                events.extend([
                    ("content_block_start", {"type": "content_block_start", "index": index, "content_block": opening}),
                    ("content_block_delta", {"type": "content_block_delta", "index": index, "delta": delta}),
                    ("content_block_stop", {"type": "content_block_stop", "index": index}),
                ])
            events.extend([
                ("message_delta", {"type": "message_delta", "delta": {"stop_reason": stop, "stop_sequence": None},
                                   "usage": {"output_tokens": 8}}),
                ("message_stop", {"type": "message_stop"}),
            ])
            payload = "".join("event: " + name + "\ndata: " + json.dumps(event) + "\n\n"
                              for name, event in events).encode()
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.send_header("content-length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
    return Handler


def probe_case(args, root: Path, name: str, profile: str, limit: int, elapsed: int = 0) -> dict:
    root.mkdir()
    home, workspace = root / "home", root / "workspace"
    home.mkdir(); workspace.mkdir()
    for index in range(4):
        (workspace / f"{NATIVE_MARKER}_{index}.txt").write_text(f"{NATIVE_MARKER}_{index}\n")
    state, mcp_log = root / "budget.json", root / "mcp-executed.jsonl"
    initial_state(state, elapsed)
    settings = {"hooks": {"PreToolUse": [{"matcher": "Read|Grep|Glob|" + ZG_TOOL, "hooks": [
        {"type": "command", "command": shlex.join(hook_command(args, state, limit)), "timeout": 5}]}]}}
    (root / "settings.json").write_text(json.dumps(settings))
    mcp = {"mcpServers": {}}
    if profile == "zg":
        mcp["mcpServers"]["zvec_grep"] = {"type": "stdio", "command": sys.executable,
            "args": [str(Path(__file__).resolve()), "--serve-mcp", str(mcp_log)]}
    (root / "mcp.json").write_text(json.dumps(mcp))

    def read(index):
        return {"type": "tool_use", "id": f"tool_read_{index}", "name": "Read",
                "input": {"file_path": str(workspace / f"{NATIVE_MARKER}_{index}.txt")}}

    def search(index):
        return {"type": "tool_use", "id": f"tool_mcp_{index}", "name": ZG_TOOL, "input": {"query": "fixture"}}

    if profile == "zg":
        batches = [[search(0)], [search(1), read(0)]]
    elif elapsed:
        batches = [[read(0)]]
    else:
        batches = [[read(0),
                    {"type": "tool_use", "id": "tool_grep", "name": "Grep", "input": {
                        "pattern": NATIVE_MARKER, "path": str(workspace), "output_mode": "content"}},
                    {"type": "tool_use", "id": "tool_glob", "name": "Glob", "input": {
                        "pattern": f"{NATIVE_MARKER}_*.txt", "path": str(workspace)}},
                    read(1)]]
    records = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(records, batches))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    # Do not inherit production auth, providers, proxy configuration, or settings.
    env = {"PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"), "HOME": str(home),
           "CLAUDE_CONFIG_DIR": str(home / ".claude"), "ANTHROPIC_API_KEY": "offline-fixture-dummy-key",
           "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{server.server_port}",
           "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1", "DISABLE_NONESSENTIAL_TRAFFIC": "1",
           "DISABLE_AUTOUPDATER": "1", "CLAUDE_CODE_DISABLE_CLAUDE_MDS": "1",
           "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1", "CLAUDE_CODE_MAX_OUTPUT_TOKENS": "4096",
           "IS_SANDBOX": "1", "NO_PROXY": "127.0.0.1,localhost", "NO_COLOR": "1"}
    tools = "Glob,Grep,Read"
    allowed = tools + ("," + ZG_TOOL if profile == "zg" else "")
    command = [args.claude, "--print", "--verbose", "--output-format", "stream-json", "--model", MODEL,
               "--max-turns", "6", "--effort", "high", "--permission-mode", "dontAsk", "--tools", tools,
               "--allowedTools", allowed, "--disable-slash-commands", "--no-session-persistence",
               "--setting-sources", "", "--settings", str(root / "settings.json"),
               "--strict-mcp-config", "--mcp-config", str(root / "mcp.json"),
               "--", "Offline hook fixture. Return a final answer after tools are denied."]
    try:
        process = subprocess.run(command, env=env, cwd=workspace, capture_output=True, text=True, timeout=60)
    finally:
        server.shutdown(); server.server_close()
    metrics = parse_stream(process.stdout, MODEL, profile, ZG_TOOL)
    errors = list(metrics["errors"])
    if process.returncode:
        errors.append("process_exit:" + str(process.returncode))
    if metrics["answer"] != ANSWER or not metrics["usage_complete"]:
        errors.append("missing_fixture_answer_or_session_usage")
    saved = json.loads(state.read_text())
    expected_admitted = 0 if elapsed else limit
    if sum(call["admitted"] for call in saved["calls"].values()) != expected_admitted:
        errors.append("wrong_admitted_count")
    expected_calls = {call["id"]: call for batch in batches for call in batch}
    if set(saved["calls"]) != set(expected_calls):
        errors.append("hook_did_not_account_for_every_call")
    events = [json.loads(line) for line in process.stdout.splitlines() if line.startswith("{")]
    results = {block["tool_use_id"]: block for event in events
               for block in event.get("message", {}).get("content", []) if block.get("type") == "tool_result"}
    for tool_id, call in saved["calls"].items():
        reply = results.get(tool_id, {})
        if not reply or bool(reply.get("is_error")) == call["admitted"]:
            errors.append("tool_execution_disagrees_with_hook:" + tool_id)
        marker = MCP_MARKER if call["name"] == ZG_TOOL else NATIVE_MARKER
        if (marker in json.dumps(reply)) != call["admitted"]:
            errors.append("execution_marker_disagrees_with_hook:" + tool_id)
    executions = mcp_log.read_text().splitlines() if mcp_log.exists() else []
    if len(executions) != (1 if profile == "zg" else 0):
        errors.append("denied_mcp_executed_or_admitted_mcp_missing")
    if not any(FINAL_MESSAGE in json.dumps(body.get("messages")) for body in records):
        errors.append("final_answer_directive_not_delivered")
    if "BUDGET_HOOK_INFRASTRUCTURE_ERROR" in process.stdout + process.stderr:
        errors.append("hook_infrastructure_error")
    return {"case": name, "valid": not errors, "errors": errors, "metrics": metrics,
            "hook_state": saved, "mcp_execution_count": len(executions), "requests": records,
            "stream": process.stdout, "stderr": process.stderr}


def probe_atomic(args, state: Path) -> dict:
    initial_state(state)
    ids = list(range(8)) + list(range(6))

    def invoke(index):
        event = {"hook_event_name": "PreToolUse", "session_id": "offline-concurrency",
                 "tool_use_id": f"parallel-{index}", "tool_name": "Read", "tool_input": {}}
        process = subprocess.run(hook_command(args, state, 3), input=json.dumps(event),
                                 capture_output=True, text=True, timeout=15)
        if process.returncode:
            raise RuntimeError("parallel hook failed: " + process.stderr)
        return event["tool_use_id"], json.loads(process.stdout)["hookSpecificOutput"]

    with ThreadPoolExecutor(max_workers=12) as pool:
        rows = list(pool.map(invoke, ids))
    saved = json.loads(state.read_text())
    errors = []
    if len(saved["calls"]) != 8 or sum(call["admitted"] for call in saved["calls"].values()) != 3:
        errors.append("parallel_budget_or_dedup_failure")
    for tool_id, output in rows:
        expected = None if saved["calls"][tool_id]["admitted"] else "deny"
        if output.get("permissionDecision") != expected:
            errors.append("duplicate_decision_changed_or_permission_granted")
    return {"valid": not errors, "errors": errors, "invocations": len(rows),
            "unique_calls": len(saved["calls"]), "hook_state": saved}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--claude", default="claude")
    parser.add_argument("--hook", type=Path, default=Path(__file__).with_name("tool_budget.py"))
    args = parser.parse_args()
    args.hook = args.hook.resolve()
    report = {"schema_version": 1, "purpose": "offline budget-hook preflight; not QA evidence",
              "external_inference_requests": 0, "credentials": "dummy fixture only", "valid": False}
    try:
        version = subprocess.check_output([args.claude, "--version"], text=True, timeout=10).strip()
        report["claude_version"] = version
        if version != "2.1.212 (Claude Code)":
            raise RuntimeError("budget probe requires pinned Claude Code 2.1.212")
        with tempfile.TemporaryDirectory(prefix="zg-budget-probe-") as directory:
            root = Path(directory)
            report["atomic"] = probe_atomic(args, root / "atomic.json")
            report["cases"] = [
                probe_case(args, root / "native", "parallel-native-budget", "baseline", 2),
                probe_case(args, root / "mcp", "mcp-budget", "zg", 1),
                probe_case(args, root / "time", "elapsed-time-budget", "baseline", 24, elapsed=601),
            ]
        report["valid"] = report["atomic"]["valid"] and all(case["valid"] for case in report["cases"])
    except Exception as error:
        report["error"] = str(error)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"valid": report["valid"], "output": str(args.output), "error": report.get("error"),
                      "cases": [{"case": case["case"], "errors": case["errors"]} for case in report.get("cases", [])]}))
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    if "--serve-mcp" in sys.argv:
        serve_mcp(sys.argv[sys.argv.index("--serve-mcp") + 1])
    else:
        raise SystemExit(main())
