"""Diagnostic adapter: frozen zg/OpenCode binaries with transparent HTTP tracing."""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
from pathlib import Path
import tempfile
from urllib.parse import urlsplit

from .zvec_opencode import ZvecOpenCode

MODEL_PORT = 17997
RPC_PORT = 17998


def traced_config(config):
    result = copy.deepcopy(config)
    provider = result["provider"]["custom-openai"]["options"]
    upstream = urlsplit(provider["baseURL"])
    if upstream.scheme not in ("https", "http") or upstream.username or upstream.password or upstream.query or upstream.fragment:
        raise ValueError("Trace upstream must be an HTTP origin/path without credentials or query")
    provider["baseURL"] = f"http://127.0.0.1:{MODEL_PORT}{upstream.path}"
    rpc = urlsplit(result["mcp"]["zvec_grep"]["url"])
    if rpc.hostname != "127.0.0.1" or rpc.port != 7999 or rpc.path != "/mcp":
        raise ValueError("Expected the frozen local zg MCP endpoint")
    result["mcp"]["zvec_grep"]["url"] = f"http://127.0.0.1:{RPC_PORT}/mcp"
    return result, dict(model_origin=f"{upstream.scheme}://{upstream.netloc}",
        rpc_origin="http://127.0.0.1:7999", model_port=MODEL_PORT, rpc_port=RPC_PORT,
        output="/logs/agent/wire-trace")


class TracedZvecOpenCode(ZvecOpenCode):
    def __init__(self, *args, opencode_config=None, **kwargs):
        self._untraced_config = copy.deepcopy(opencode_config)
        config, self._trace_config = traced_config(opencode_config)
        super().__init__(*args, opencode_config=config, **kwargs)

    async def setup(self, environment):
        await super().setup(environment)
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "trace-config.json"
            source.write_text(json.dumps(self._trace_config))
            await environment.upload_file(source, "/tmp/zg-bench-wire-trace-config.json")
        await environment.upload_file(Path(__file__).with_name("wire_trace.mjs"), "/tmp/zg-bench-wire-trace.mjs")
        await self.exec_as_agent(environment, command=(
            "set -e; mkdir -p /logs/agent/wire-trace; . ~/.nvm/nvm.sh; "
            "nohup node /tmp/zg-bench-wire-trace.mjs /tmp/zg-bench-wire-trace-config.json "
            "> /logs/agent/wire-trace/proxy-console.log 2>&1 </dev/null & "
            "echo $! > /logs/agent/wire-trace/proxy.pid"
        ))
        await self.exec_as_agent(environment, command=(
            "set -e; curl --silent --show-error --fail --retry 10 --retry-connrefused --retry-delay 1 "
            f"--max-time 15 http://127.0.0.1:{MODEL_PORT}/__trace/health; "
            f"curl --silent --show-error --fail --max-time 15 http://127.0.0.1:{RPC_PORT}/__trace/health"
        ))
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        (self.logs_dir / "wire-trace-contract.json").write_text(json.dumps(dict(
            original_opencode_config=self._untraced_config, traced_opencode_config=self._opencode_config,
            wire_trace_sha256=hashlib.sha256(Path(__file__).with_name("wire_trace.mjs").read_bytes()).hexdigest(),
            model_deadline_added=False, model_retries_added=False, product_binary_modified=False,
        ), indent=2) + "\n")

    async def run(self, instruction, environment, context):
        try:
            await super().run(instruction, environment, context)
        finally:
            try:
                await asyncio.wait_for(self.exec_as_agent(environment, command=(
                    f"curl --silent --show-error --fail --max-time 10 http://127.0.0.1:{MODEL_PORT}/__trace/snapshot "
                    "> /logs/agent/wire-trace/final-snapshot-status.json; "
                    "kill -TERM $(cat /logs/agent/wire-trace/proxy.pid)"
                ), timeout_sec=15), timeout=20)
            except BaseException as error:
                (self.logs_dir / "wire-trace-capture-error.json").write_text(json.dumps({"error": type(error).__name__}) + "\n")
