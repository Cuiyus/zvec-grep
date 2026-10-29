from __future__ import annotations

import http.client
import http.server
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess
import tempfile
import threading
import time
import unittest

from zg_bench.case_analysis import recording_command, rpc_messages, inventory
from zg_bench.agents.case_evidence import FULL_SESSION_SQL
from zg_bench.case_replay import stable, key, control_params
from zg_bench import runner


class CaseAnalysisTests(unittest.TestCase):
    def test_route_ablation_omits_query_instead_of_sending_null(self):
        original = {"name": "zvec_grep_search", "arguments": {"query": "q", "root": "/app", "limit": 7}}
        changed = control_params(original, {"query": None, "fts": "q"})
        self.assertEqual(changed["arguments"], {"fts": "q", "root": "/app", "limit": 7})
        self.assertEqual(original["arguments"]["query"], "q")

    def test_parity_ignores_only_transient_status_not_order_or_source(self):
        a = {"content": [{"text": "freshness: fresh\nbackground_refresh: scheduled\n#1 foo.py\nsource:\n1\toriginal"}]}
        b = {"content": [{"text": "freshness: fresh\n#1 foo.py\nsource:\n1\toriginal"}]}
        self.assertEqual(stable(a), stable(b))
        self.assertNotEqual(stable(a), stable({"content": [{"text": b["content"][0]["text"].replace("original", "changed")}]}))
        self.assertNotEqual(key({"query": "x", "limit": 7}), key({"query": "x", "limit": 10}))

    def test_transport_change_retains_all_model_options(self):
        suite = runner.load_suite("swe-qa-bench", tier="full", task_overrides=["pylint-10"])
        original = runner.build_harbor_command(suite, profile="zvec-grep", agent="opencode",
            model="custom-openai/qwen3.8-max", job_name="test", n_attempts=10, max_retries=0)
        recorded = recording_command(original)
        config = lambda cmd: json.loads(next(x.split("=", 1)[1] for x in cmd if x.startswith("opencode_config=")))
        before, after = config(original), config(recorded)
        upstream = before["provider"]["custom-openai"]["options"]["baseURL"]
        after["provider"]["custom-openai"]["options"]["baseURL"] = upstream
        after["mcp"]["zvec_grep"]["url"] = before["mcp"]["zvec_grep"]["url"]
        self.assertEqual(before, after)
        self.assertIn("evidence_upstream=" + upstream, recorded)
        self.assertEqual(recorded[recorded.index("--n-attempts") + 1], "10")
        self.assertEqual(recorded[recorded.index("--max-retries") + 1], "0")

    def test_sql_preserves_nested_full_bodies(self):
        db = sqlite3.connect(":memory:")
        db.executescript("CREATE TABLE session(id TEXT,parent_id TEXT); CREATE TABLE message(id TEXT,session_id TEXT,data TEXT); CREATE TABLE part(id TEXT,session_id TEXT,message_id TEXT,data TEXT);")
        db.executemany("INSERT INTO session VALUES (?,?)", [("root", None), ("child", "root")])
        payload = {"type": "tool", "state": {"input": {"query": "a\nb"}, "output": "full\nbody"}}
        db.execute("INSERT INTO message VALUES ('m','child',?)", (json.dumps({"role": "assistant"}),))
        db.execute("INSERT INTO part VALUES ('p','child','m',?)", (json.dumps(payload),))
        rows = [(kind, json.loads(data)) for kind, data in db.execute(FULL_SESSION_SQL)]
        self.assertEqual(next(data["content"] for kind, data in rows if kind == "part"), payload)
        self.assertEqual(sum(kind == "session" for kind, _ in rows), 2)

    def test_sse_and_failed_inventory(self):
        self.assertEqual(rpc_messages('event: message\r\ndata: {"id":1,\r\ndata: "result":{}}\r\n\r\n'), [{"id": 1, "result": {}}])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertFalse(inventory(root, 10)["capture_ready"])
            trial = root / "jobs/formal/failed"
            trial.mkdir(parents=True)
            (trial / "config.json").write_text("{}")
            result = inventory(root, 1)
            self.assertEqual(result["observed_trials"], 1)
            self.assertFalse(result["capture_ready"])

    @unittest.skipUnless(shutil.which("node"), "Node required for transport parity test")
    def test_proxy_streams_exact_bytes_and_excludes_credentials(self):
        observed = {}
        payload = b'data: {"delta":"one"}\n\ndata: [DONE]\n\n'
        class Upstream(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                observed.update(path=self.path, authorization=self.headers.get("Authorization"), body=self.rfile.read(int(self.headers["Content-Length"])))
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Set-Cookie", "private-cookie")
                self.end_headers()
                for chunk in [payload[:12], payload[12:]]:
                    self.wfile.write(chunk)
                    self.wfile.flush()
            def log_message(self, *args):
                pass
        upstream = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
        threading.Thread(target=upstream.serve_forever, daemon=True).start()
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = {"output": str(root / "wire"), "ready": str(root / "ready"), "routes": [{"name": "model", "port": port, "upstream": f"http://127.0.0.1:{upstream.server_port}/v1"}]}
            (root / "config.json").write_text(json.dumps(config))
            proxy = Path(__file__).parents[1] / "zg_bench/agents/evidence_proxy.mjs"
            process = subprocess.Popen(["node", str(proxy), str(root / "config.json")], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                for _ in range(100):
                    if (root / "ready").exists() or process.poll() is not None:
                        break
                    time.sleep(.02)
                self.assertIsNone(process.poll(), process.stderr.read() if process.poll() is not None else "")
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                body = b'{"messages":[{"content":"original"}]}'
                conn.request("POST", "/chat/completions?test=1", body, {"Authorization": "Bearer secret-value", "Content-Type": "application/json"})
                response = conn.getresponse()
                self.assertEqual(response.read(), payload)
                conn.close()
                self.assertEqual(observed, {"path": "/v1/chat/completions?test=1", "authorization": "Bearer secret-value", "body": body})
                for _ in range(50):
                    if list((root / "wire").glob("*/*.completion.json")):
                        break
                    time.sleep(.02)
                self.assertEqual(next((root / "wire").glob("*/*.request.bin")).read_bytes(), body)
                self.assertEqual(next((root / "wire").glob("*/*.response.bin")).read_bytes(), payload)
                completion = json.loads(next((root / "wire").glob("*/*.completion.json")).read_text())
                self.assertTrue(completion["complete"])
                for path in (root / "wire").rglob("*"):
                    if path.is_file():
                        self.assertNotIn(b"secret-value", path.read_bytes())
                        self.assertNotIn(b"private-cookie", path.read_bytes())
            finally:
                process.terminate()
                process.communicate(timeout=5)
                upstream.shutdown()
                upstream.server_close()


if __name__ == "__main__":
    unittest.main()
