"""Real loopback HTTP tests: no external model calls or zg product modifications."""
import gzip
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import unittest

from zg_bench.agents.traced_opencode import MODEL_PORT, RPC_PORT, traced_config
from zg_bench.engines.opencode.config import build_opencode_config
from zg_bench.engines.registry import resolve_opencode_model


class Server(ThreadingHTTPServer):
    daemon_threads = True


class TraceTests(unittest.TestCase):
    def test_workflow_covers_exactly_historical_cases_with_original_artifacts(self):
        import yaml
        workflow_path = Path(__file__).parents[3] / ".github/workflows/swe-qa-bench.yml"
        workflow = yaml.safe_load(workflow_path.read_text())
        self.assertEqual(set(workflow["on"]), {"workflow_dispatch"})
        for job in workflow["jobs"].values():
            self.assertIn("Cuiyus/zvec-grep", job["steps"][0]["run"])
            self.assertIn("BENCH_TRIGGERING_ACTOR", job["steps"][0]["run"])
        self.assertEqual(workflow["jobs"]["trace"]["strategy"]["max-parallel"], 8)
        artifacts = [s["with"] for s in workflow["jobs"]["trace"]["steps"] if s.get("uses") == "actions/download-artifact@v8"]
        self.assertEqual({s["name"] for s in artifacts}, {"version-package-node", "version-package-rust"})
        self.assertTrue(all(s["run-id"] == 38413351617 for s in artifacts))
        matrix_step = next(s for s in workflow["jobs"]["validate"]["steps"] if s.get("id") == "matrix")
        script = matrix_step["run"].split("<<'PYCODE'\n")[1].rsplit("\nPYCODE", 1)[0]
        expected = {("glm-5.2", "conan-19"), ("glm-5.2", "matplotlib-37"), ("glm-5.2", "requests-16"),
            ("qwen3.8-max", "conan-19"), ("qwen3.8-max", "conan-39"), ("qwen3.8-max", "requests-16"),
            ("qwen3.8-max", "streamlink-14"), ("qwen3.8-max", "xarray-46")}
        import sys
        for task, model, count in [("", "all", 8), ("conan-19", "qwen3.8-max", 1)]:
            with tempfile.TemporaryDirectory() as temp:
                dest = Path(temp) / "out"
                subprocess.run([sys.executable, "-c", script], env=dict(os.environ,
                    TASK_SCOPE=task, MODEL_SCOPE=model, GITHUB_OUTPUT=str(dest)), check=True)
                rows = json.loads(dest.read_text().split("=", 1)[1])["include"]
                self.assertEqual(len(rows), count)
                if not task: self.assertEqual({(r["model"], r["task"]) for r in rows}, expected)
        run = next(s["run"] for s in workflow["jobs"]["trace"]["steps"] if s.get("name", "").startswith("Run five"))
        self.assertIn("--wire-trace", run)
        self.assertIn("--embedding local", run)

    def test_configuration_changes_only_transport_urls(self):
        for model in ("glm-5.2", "qwen3.8-max"):
            original = build_opencode_config(resolve_opencode_model(model).configuration, profile="zvec-grep")
            traced, proxy = traced_config(original)
            restored = json.loads(json.dumps(traced))
            restored["provider"]["custom-openai"]["options"]["baseURL"] = original["provider"]["custom-openai"]["options"]["baseURL"]
            restored["mcp"]["zvec_grep"]["url"] = original["mcp"]["zvec_grep"]["url"]
            self.assertEqual(restored, original)
            self.assertEqual(proxy["model_port"], MODEL_PORT)
            self.assertEqual(proxy["rpc_port"], RPC_PORT)
            self.assertEqual(traced["mcp"]["zvec_grep"]["timeout"], 600000)
            self.assertNotIn("timeout", traced["provider"]["custom-openai"]["options"])

    def test_transparent_stream_rpc_retry_status_and_stall_evidence(self):
        node = os.environ.get("WIRE_TRACE_NODE") or shutil.which("node")
        self.assertTrue(node, "Node is required for the real protocol test")
        records = []
        release = threading.Event()
        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"
            def log_message(self, *args): pass
            def do_POST(self):
                # Node may send a fixed content-length or HTTP chunked body.
                if self.headers.get("Transfer-Encoding") == "chunked":
                    chunks = []
                    while size := int(self.rfile.readline().strip(), 16):
                        chunks.append(self.rfile.read(size)); self.rfile.read(2)
                    self.rfile.readline(); body = b"".join(chunks)
                else: body = self.rfile.read(int(self.headers["Content-Length"]))
                records.append((self.path, self.headers.get("Authorization"), body))
                if self.path == "/v1/stall":
                    release.wait(10); return
                if self.path == "/v1/retry":
                    self.send_response(503); self.send_header("Retry-After", "120")
                    self.send_header("Content-Length", "0"); self.end_headers(); return
                if self.path == "/mcp":
                    payload = json.loads(body)
                    data = json.dumps(dict(jsonrpc="2.0", id=payload["id"], result={"content": [{"type": "text", "text": "ok"}]})).encode()
                    self.send_response(200); self.send_header("Content-Type", "application/json")
                    self.send_header("Mcp-Session-Id", "fixture-session")
                    self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data); return
                data = b'data: {"id":"fixture-response","choices":[{"delta":{"tool_calls":[{"index":0,"id":"call-1","function":{"name":"zvec_grep_search","arguments":"{}"}}]}}]}\n\ndata: [DONE]\n\n'
                compressed = self.path == "/v1/gzip"
                if compressed: data = gzip.compress(data)
                self.send_response(200); self.send_header("Content-Type", "text/event-stream")
                self.send_header("X-Request-Id", "fixture-request-id")
                if compressed: self.send_header("Content-Encoding", "gzip")
                self.send_header("Content-Length", str(len(data))); self.end_headers()
                self.wfile.write(data[:12]); self.wfile.flush(); time.sleep(.05)
                self.wfile.write(data[12:]); self.wfile.flush()
        server = Server(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            def port():
                with socket.socket() as sock: sock.bind(("127.0.0.1", 0)); return sock.getsockname()[1]
            model_port, rpc_port = port(), port()
            config = dict(model_port=model_port, rpc_port=rpc_port, output=str(root / "logs"),
                model_origin=f"http://127.0.0.1:{server.server_port}", rpc_origin=f"http://127.0.0.1:{server.server_port}")
            (root / "config.json").write_text(json.dumps(config))
            script = Path(__file__).parents[1] / "zg_bench/agents/wire_trace.mjs"
            console = (root / "console.log").open("w")
            proc = subprocess.Popen([node, str(script), str(root / "config.json")], stdout=console, stderr=console)
            try:
                for _ in range(100):
                    if (root / "logs/events.jsonl").exists(): break
                    if proc.poll() is not None: self.fail((root / "console.log").read_text())
                    time.sleep(.02)
                def request(path, body=b"{}", rpc=False):
                    conn = http.client.HTTPConnection("127.0.0.1", rpc_port if rpc else model_port, timeout=3)
                    conn.request("POST", path, body=body, headers={"Authorization": "Bearer fixture-sensitive-key", "Content-Type": "application/json"})
                    response = conn.getresponse(); data = response.read(); conn.close()
                    return response.status, response.headers, data
                for path in ("/v1/chat/completions", "/v1/gzip"):
                    status, headers, data = request(path, b'{"model":"glm-5.2","api_key":"fixture-sensitive-key"}')
                    self.assertEqual(status, 200)
                    if path.endswith("gzip"): data = gzip.decompress(data)
                    self.assertIn(b"[DONE]", data)
                    self.assertEqual(headers["X-Request-Id"], "fixture-request-id")
                status, headers, data = request("/mcp", b'{"jsonrpc":"2.0","id":7,"method":"tools/call","params":{"name":"zvec_grep_search","arguments":{"query":"fixture"}}}', True)
                self.assertEqual(json.loads(data)["id"], 7)
                self.assertEqual(headers["Mcp-Session-Id"], "fixture-session")
                status, headers, _ = request("/v1/retry")
                self.assertEqual((status, headers["Retry-After"]), (503, "120"))
                stalled = http.client.HTTPConnection("127.0.0.1", model_port, timeout=3)
                stalled.request("POST", "/v1/stall", body=b"{}")
                for _ in range(100):
                    if any(r[0] == "/v1/stall" for r in records): break
                    time.sleep(.01)
                control = http.client.HTTPConnection("127.0.0.1", model_port, timeout=3)
                control.request("GET", "/__trace/snapshot"); control.getresponse().read(); control.close()
                time.sleep(.1)
                self.assertIsNone(proc.poll())
                events = [json.loads(line) for line in (root / "logs/events.jsonl").read_text().splitlines()]
                self.assertTrue(any(e["type"] == "tool_argument_delta" and e.get("name") == "zvec_grep_search" for e in events))
                self.assertTrue(any(e["type"] == "rpc_response" and e.get("rpc_id") == 7 for e in events))
                self.assertTrue(any(e["type"] == "snapshot" and any(a["phase"] == "upstream_headers" for a in e["active"]) for e in events))
                self.assertEqual(len(records), 5, "Proxy must not retry the 503 or stalled request")
                self.assertTrue(all(auth == "Bearer fixture-sensitive-key" for _, auth, _ in records[:-1]))
                for file in (root / "logs").rglob("*"):
                    if file.is_file(): self.assertNotIn(b"fixture-sensitive-key", file.read_bytes())
                stalled.close()
            finally:
                proc.terminate(); proc.wait(timeout=5); console.close(); release.set()
                server.shutdown(); server.server_close()


if __name__ == "__main__": unittest.main()
