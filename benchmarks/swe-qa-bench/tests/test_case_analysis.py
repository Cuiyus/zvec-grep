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
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from zg_bench.case_analysis import recording_command, rpc_messages, inventory, main, run_plan, assess_capture, sha256
from zg_bench.agents.case_evidence import FULL_SESSION_SQL, CaseEvidenceBaselineOpenCode, CaseEvidenceOpenCode
from zg_bench.agents.opencode import ResilientOpenCode
from zg_bench.agents.zvec_grep import ZvecGrepMixin
from zg_bench.case_replay import stable, key, control_params
from zg_bench.case_audit import emitted_tool_ids
from zg_bench import runner


class CaseAnalysisTests(unittest.TestCase):
    def write_trial(self, root, profile="baseline", number=0, job_name=None):
        trial = root / "jobs" / (job_name or f"formal-{profile}") / f"pylint-10__{number}"
        agent = trial / "agent"
        wire = agent / "wire/model"
        wire.mkdir(parents=True)
        sid = f"{profile}-{number}"
        adapter = "CaseEvidenceBaselineOpenCode" if profile == "baseline" else "CaseEvidenceOpenCode"
        records = {
            trial / "config.json": {"agent": {"name": "zg_bench.agents.case_evidence:" + adapter}},
            trial / "result.json": {"finished_at": "2026-10-29T14:37:09Z", "exception_info": None},
            agent / "evidence-capture.json": {"complete": True},
            agent / "corpus-before.json": {"commit": "pinned", "files": []},
            agent / "corpus-after.json": {"commit": "pinned", "files": []},
            agent / "full-sessions.json": [
                {"kind": "session", "data": {"id": sid, "parent_id": None}},
                {"kind": "message", "data": {"id": "msg", "session_id": sid, "content": {"role": "assistant"}}},
            ],
            agent / "session-usage.json": {"complete": True, "errors": [], "total": {
                "input_tokens": 100, "output_tokens": 10, "llm_calls": 1, "tool_calls": 0}},
            wire / "1.metadata.json": {"route": "model", "id": "1", "method": "POST"},
            wire / "1.completion.json": {"complete": True},
            wire / "1.request.bin": {"messages": [{"role": "user", "content": "question"}]},
            wire / "1.response.bin": {"choices": []},
        }
        for path, value in records.items():
            path.write_text(json.dumps(value))
        return trial

    def test_context_link_uses_emitted_tool_id_not_quoted_text(self):
        body = 'data: {"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"id":"call_"}]}}]}\n\n'
        body += 'data: {"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"id":"123"}],"content":"call_fake"}}]}\n\n'
        body += 'data: [DONE]\n\n'
        self.assertEqual(emitted_tool_ids(body), {"call_123"})

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

    def test_baseline_records_model_without_zg_provision_or_config(self):
        suite = runner.load_suite("swe-qa-bench", tier="full", task_overrides=["pylint-10"])
        original = runner.build_harbor_command(suite, profile="baseline", agent="opencode",
            model="custom-openai/qwen3.8-max", job_name="test", n_attempts=10, max_retries=0)
        recorded = recording_command(original)
        self.assertEqual(recorded[recorded.index("--agent") + 1],
                         "zg_bench.agents.case_evidence:CaseEvidenceBaselineOpenCode")
        self.assertFalse(any(value.startswith(("zvec_", "embedding_", "mcp_target=")) for value in recorded))
        config = lambda cmd: json.loads(next(x.split("=", 1)[1] for x in cmd if x.startswith("opencode_config=")))
        before, after = config(original), config(recorded)
        self.assertNotIn("zvec_grep", after.get("mcp", {}))
        after["provider"]["custom-openai"]["options"]["baseURL"] = before["provider"]["custom-openai"]["options"]["baseURL"]
        self.assertEqual(before, after)
        self.assertTrue(issubclass(CaseEvidenceBaselineOpenCode, ResilientOpenCode))
        self.assertFalse(issubclass(CaseEvidenceBaselineOpenCode, ZvecGrepMixin))
        for adapter, expected_routes in [(CaseEvidenceBaselineOpenCode, ["model"]), (CaseEvidenceOpenCode, ["model", "mcp"])]:
            instance = object.__new__(adapter)
            instance._evidence_upstream = "http://example.test/v1"
            self.assertEqual([route["name"] for route in instance._proxy_routes()], expected_routes)

    def test_profile_plan_keeps_default_layout_and_counts_each_group(self):
        self.assertEqual(run_plan("formal", "zvec-grep"), [{"profile": "zvec-grep", "job_name": "formal", "attempts": 10}])
        self.assertEqual(run_plan("preflight", "all"), [
            {"profile": "baseline", "job_name": "preflight-baseline", "attempts": 1},
            {"profile": "zvec-grep", "job_name": "preflight-zvec-grep", "attempts": 1},
        ])
        self.assertEqual(sum(item["attempts"] for item in run_plan("formal", "all")), 20)

    def test_paired_main_runs_both_groups_then_collects_twenty_trials(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / "zg.tgz"
            package.write_bytes(b"package")
            output = root / "output"
            executed = []
            def execute(command, **kwargs):
                job = command[command.index("--job-name") + 1]
                profile = "baseline" if job.endswith("-baseline") else "zvec-grep"
                self.assertFalse((output / "capture-summary.json").exists())
                self.assertEqual(command[command.index("--max-retries") + 1], "0")
                executed.append(profile)
                for number in range(10):
                    self.write_trial(output, profile, number, job)
                return 0
            with patch("zg_bench.case_analysis.runner.prepare_setup_cache", return_value=SimpleNamespace(
                zvec_grep_package=str(package), zvec_grep_package_sha256=None)) as prepare, patch(
                "zg_bench.case_analysis.runner.execution_environment", return_value={}), patch(
                "zg_bench.case_analysis.runner.execute", side_effect=execute):
                code = main(["run", "--task", "pylint-10", "--stage", "formal", "--profile", "all",
                             "--package", str(package), "--candidate-commit", "pinned", "--output", str(output)])
            self.assertEqual(code, 0)
            self.assertEqual(executed, ["baseline", "zvec-grep"])
            self.assertEqual([call.args[1] for call in prepare.call_args_list], executed)
            lock = json.loads((output / "run-lock.json").read_text())
            self.assertEqual(lock["expected_trials"], 20)
            self.assertEqual(lock["profiles"], executed)
            report = json.loads((output / "capture-summary.json").read_text())
            self.assertTrue(report["capture_ready"])
            self.assertEqual(report["profile_trial_counts"], {"baseline": 10, "zvec-grep": 10})
            self.assertTrue(all((output / "commands" / f"{profile}.json").exists() for profile in executed))

    def test_inventory_rejects_incomplete_usage_and_trial_exceptions(self):
        for target, update, expected_error in [
            ("agent/session-usage.json", {"complete": False}, "session_usage_incomplete"),
            ("agent/session-usage.json", {"total": {}}, "session_usage_totals_invalid"),
            ("result.json", {"exception_info": {"exception_type": "AgentTimeoutError"}}, "trial_exception"),
            ("result.json", {"finished_at": None}, "trial_incomplete"),
        ]:
            with self.subTest(error=expected_error), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                trial = self.write_trial(root)
                self.assertTrue(inventory(root, 1)["capture_ready"])
                path = trial / target
                path.write_text(json.dumps({**json.loads(path.read_text()), **update}))
                report = inventory(root, 1)
                self.assertFalse(report["capture_ready"])
                self.assertIn(expected_error, report["trials"][0]["errors"])

    def test_inventory_enforces_profile_counts_not_only_total(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_trial(root, "zvec-grep", 0)
            self.write_trial(root, "zvec-grep", 1)
            (root / "run-lock.json").write_text(json.dumps({"profiles": ["baseline", "zvec-grep"], "attempts": 1}))
            report = inventory(root, 2)
            self.assertEqual(report["observed_trials"], 2)
            self.assertFalse(report["capture_ready"])
            self.assertIn("profile_trial_count:baseline", report["errors"])
            self.assertIn("profile_trial_count:zvec-grep", report["errors"])

    def test_partial_title_is_qualified_without_rewriting_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            trial = self.write_trial(root)
            wire = trial / "agent/wire/model"
            records = {
                "title.metadata.json": {"route": "model", "id": "title", "method": "POST"},
                "title.completion.json": {"complete": False, "status": 200},
                "title.request.bin": {"messages": [
                    {"role": "system", "content": "You are a title generator. You output ONLY a thread title. Nothing else."},
                    {"role": "user", "content": "Generate a title for this conversation:\n"},
                    {"role": "user", "content": "question"}]},
                "title.response.bin": {"choices": []},
            }
            for name, value in records.items():
                (wire / name).write_text(json.dumps(value))
            report = inventory(root, 1)
            self.assertTrue(report["capture_ready"])
            self.assertEqual(report["trials"][0]["incomplete_auxiliary_wires"], ["title"])
            self.assertEqual(report["trials"][0]["task_model_calls"], 1)
            before = {str(p.relative_to(root)): sha256(p) for p in root.rglob("*") if p.is_file()}
            self.assertTrue(assess_capture(root, 1)["report"]["capture_ready"])
            self.assertEqual(before, {str(p.relative_to(root)): sha256(p) for p in root.rglob("*") if p.is_file()})
            (wire / "title.response.bin").write_text("changed")
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                assess_capture(root, 1)

    def test_partial_task_or_unknown_auxiliary_and_missing_title_bytes_fail(self):
        for variant in ("task", "unknown", "missing_response", "tools"):
            with self.subTest(variant=variant), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                trial = self.write_trial(root)
                wire = trial / "agent/wire/model"
                body = {"messages": [
                    {"role": "system", "content": "You are a title generator. You output ONLY a thread title. Nothing else."},
                    {"role": "user", "content": "Generate a title for this conversation:\nquestion"}]}
                if variant == "task":
                    body = {"messages": [{"role": "user", "content": "answer the task"}]}
                elif variant == "unknown":
                    body["messages"][0]["content"] = "Other auxiliary service"
                elif variant == "tools":
                    body["tools"] = [{"type": "function"}]
                for name, value in {
                    "aux.metadata.json": {"route": "model", "id": "aux", "method": "POST"},
                    "aux.completion.json": {"complete": False, "status": 200},
                    "aux.request.bin": body, "aux.response.bin": {"choices": []},
                }.items():
                    (wire / name).write_text(json.dumps(value))
                if variant == "missing_response":
                    (wire / "aux.response.bin").unlink()
                report = inventory(root, 1)
                self.assertFalse(report["capture_ready"])
                self.assertTrue(report["trials"][0]["errors"])

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
