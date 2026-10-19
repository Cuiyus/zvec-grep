#!/usr/bin/env python3
"""Probe the real index under the exact trial mounts/UID, without an LLM call."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import selectors
import subprocess
import tempfile
import time
import uuid

from run import cleanup_runtime, copy_seed, docker_base, redact, write_json


PROBE_QUERY = "source code implementation"
SEARCH_TOOL = "zvec_grep_search"


def require_successful_search(response: dict) -> None:
    if response.get("isError"):
        raise RuntimeError("native zg search returned isError")
    content = response.get("content")
    structured = response.get("structuredContent")
    if not (isinstance(content, list) and content) and not structured:
        raise RuntimeError("native zg search returned no content")


class StdioSession:
    def __init__(self, process: subprocess.Popen, stream, timeout: int):
        self.process, self.stream, self.timeout = process, stream, timeout
        self.buffer = b""
        self.pending = []
        self.selector = selectors.DefaultSelector()
        self.selector.register(process.stdout, selectors.EVENT_READ)

    def send(self, message: dict) -> None:
        self.process.stdin.write((json.dumps(message) + "\n").encode())
        self.process.stdin.flush()

    def receive(self, request_id: int) -> dict:
        deadline = time.monotonic() + self.timeout
        while True:
            for index, message in enumerate(self.pending):
                if message.get("id") == request_id:
                    self.pending.pop(index)
                    if "error" in message:
                        raise RuntimeError("JSON-RPC error: " + json.dumps(message["error"]))
                    result = message.get("result")
                    if not isinstance(result, dict):
                        raise RuntimeError("JSON-RPC response has no object result")
                    return result
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not self.selector.select(max(0, remaining)):
                raise TimeoutError(f"MCP request {request_id} timed out")
            chunk = os.read(self.process.stdout.fileno(), 65536)
            if not chunk:
                raise RuntimeError(f"MCP process closed stdout before response {request_id}")
            self.stream.write(chunk)
            self.stream.flush()
            self.buffer += chunk
            while b"\n" in self.buffer:
                line, self.buffer = self.buffer.split(b"\n", 1)
                if not line.strip():
                    continue
                message = json.loads(line)
                if not isinstance(message, dict):
                    raise RuntimeError("MCP stdout contains a non-object JSON message")
                self.pending.append(message)


def probe(args) -> dict:
    args.source = args.source.resolve()
    args.output = args.output.resolve()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    stream_path = args.output.with_suffix(".stream.jsonl")
    stderr_path = args.output.with_suffix(".stderr.txt")
    requests_path = args.output.with_suffix(".requests.jsonl")
    private = Path(tempfile.mkdtemp(prefix="zg-mcp-preflight-"))
    runtime = private / "runtime"
    runtime.mkdir()
    (runtime / "home").mkdir()
    (private / "config").mkdir()
    copy_seed(args.home_seed.resolve(), runtime / "home" / ".zvec-grep")
    copy_seed(args.index_seed.resolve(), runtime / "index")
    name = "zg-qa-probe-" + uuid.uuid4().hex[:12]
    command = docker_base(args, name, private, runtime) + [
        "--interactive", "--mount", f"type=bind,src={runtime / 'index'},dst=/workspace/.zvec-grep",
        args.image, args.zg, "--server", "--stdio", "--mcp-toolset", "agent",
    ]
    report = {"schema_version": 1, "purpose": "infrastructure-only real-index MCP preflight",
              "external_llm_calls": 0, "query": PROBE_QUERY, "command": command,
              "host_uid": os.getuid(), "host_gid": os.getgid(),
              "seed_policy": "private clones discarded; original index/home seeds never mounted writable",
              "source": str(args.source), "valid": False, "errors": []}
    process = None
    start = time.monotonic()
    try:
        with stream_path.open("wb") as stream, stderr_path.open("wb") as stderr, requests_path.open("w") as requests:
            process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=stderr, bufsize=0)
            session = StdioSession(process, stream, args.timeout)

            def send(message):
                requests.write(json.dumps(message) + "\n")
                requests.flush()
                session.send(message)

            try:
                send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                    "protocolVersion": "2025-03-26", "capabilities": {},
                    "clientInfo": {"name": "zg-qa-infrastructure-probe", "version": "1"}}})
                report["initialize"] = session.receive(1)
                send({"jsonrpc": "2.0", "method": "notifications/initialized"})
                send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
                listing = session.receive(2)
                report["tools"] = listing
                tool_names = [tool.get("name") for tool in listing.get("tools", [])]
                if tool_names != [SEARCH_TOOL]:
                    raise RuntimeError("agent MCP tool inventory mismatch: " + repr(tool_names))
                report["tool_schema_sha256"] = hashlib.sha256(json.dumps(listing, sort_keys=True).encode()).hexdigest()
                send({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
                    "name": SEARCH_TOOL, "arguments": {"root": "/workspace", "query": PROBE_QUERY, "limit": 1}}})
                report["search"] = session.receive(3)
                require_successful_search(report["search"])
                report["valid"] = True
            finally:
                session.selector.close()
                if process.stdin:
                    process.stdin.close()
                    process.stdin = None
                try:
                    tail, _ = process.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    subprocess.run(["docker", "kill", name], capture_output=True, timeout=30)
                    tail, _ = process.communicate(timeout=30)
                if tail:
                    stream.write(tail)
                    stream.flush()
    except Exception as error:
        report["valid"] = False
        report["errors"].append(redact(str(error)))
    finally:
        report["wall_seconds"] = time.monotonic() - start
        report["transport_exit_code"] = process.returncode if process else None
        try:
            cleanup_runtime(private, args.image)
        except Exception as error:
            report["valid"] = False
            report["errors"].append("private runtime cleanup failed: " + redact(str(error)))
        for path in (stream_path, stderr_path, requests_path):
            if path.exists():
                path.write_text(redact(path.read_text(errors="replace")))
        report = json.loads(redact(json.dumps(report)))
        write_json(args.output, report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--index-seed", type=Path, required=True)
    parser.add_argument("--home-seed", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--image", default="zg-claude-qa-pilot:20261019")
    parser.add_argument("--zg", default="zg")
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args()
    report = probe(args)
    print(json.dumps({"valid": report["valid"], "errors": report["errors"], "output": str(args.output)}))
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
