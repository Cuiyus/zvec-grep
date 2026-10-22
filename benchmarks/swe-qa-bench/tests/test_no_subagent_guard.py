#!/usr/bin/env python3
"""Exercise a real pinned OpenCode binary against a loopback-only fake provider.

No paid model calls or host credentials are used. A positive control delegates
to general/explore. Two deny cases receive the same deliberately invalid task
calls while using Harbor's skip-permissions flag or OpenCode's --auto flag.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import threading
import tempfile
import unittest
from unittest.mock import patch
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from zg_bench.engines.opencode.config import build_opencode_config
from zg_bench.engines.registry import resolve_opencode_model


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n")


def run_case(binary: str, root: Path, *, deny: bool, flag: str) -> dict:
    root.mkdir(parents=True, exist_ok=False)
    corpus = root / "corpus"
    corpus.mkdir()
    (corpus / "README.md").write_text("Fixture 42.\n")
    requests: list[dict] = []

    class FakeProvider(BaseHTTPRequestHandler):
        def log_message(self, *_: object) -> None:
            pass

        def do_POST(self) -> None:
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            child = bool(self.headers.get("x-parent-session-id"))
            requests.append({"body": body, "child": child})
            has_tools = bool(body.get("tools"))
            after_tool = any(m.get("role") == "tool" for m in body.get("messages", []))
            # This deliberately ignores the advertised tool list so the deny
            # cases also prove unsolicited tool calls cannot create children.
            if has_tools and not after_tool and not child:
                delta = {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "index": index,
                            "id": "delegate-" + agent,
                            "type": "function",
                            "function": {
                                "name": "task",
                                "arguments": json.dumps({
                                    "description": "Read fixture",
                                    "prompt": "Read README.md and report 42.",
                                    "subagent_type": agent,
                                }),
                            },
                        }
                        for index, agent in enumerate(("general", "explore"))
                    ],
                }
                finish = "tool_calls"
            else:
                delta = {"role": "assistant", "content": "The fixture is 42."}
                finish = "stop"
            envelope = {
                "id": "fixture", "object": "chat.completion.chunk",
                "created": 1, "model": body["model"],
            }
            chunks = [
                {**envelope, "choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
                {
                    **envelope,
                    "choices": [{"index": 0, "delta": {}, "finish_reason": finish}],
                    "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
                },
            ]
            payload = ("".join("data: " + json.dumps(c) + "\n\n" for c in chunks)
                       + "data: [DONE]\n\n").encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeProvider)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    # Render the actual benchmark config under the experiment's production
    # switch. Keep model and agent settings; override only the network endpoint
    # and fixture runtime controls. Host credentials never reach the child.
    with patch.dict(os.environ, {"ZG_BENCH_DISABLE_SUBAGENTS": "1" if deny else "0"}, clear=True):
        model = resolve_opencode_model("custom-openai/glm-5.2").configuration
        config = build_opencode_config(model, profile="baseline")
    write_json(root / "rendered-benchmark-config.json", config)
    config.update(autoupdate=False, share="disabled", lsp=False)
    provider = config["provider"]["custom-openai"]
    provider["options"]["baseURL"] = f"http://127.0.0.1:{server.server_port}/v1"
    for name in ("build", "general", "explore"):
        config["agent"][name]["steps"] = 3
    config_path = root / "opencode.json"
    write_json(config_path, config)
    env = {
        "PATH": os.environ.get("PATH", os.defpath), "HOME": str(root),
        "LANG": "en_US.UTF-8", "OPENCODE_CONFIG": str(config_path),
        "OPENAI_API_KEY": "offline-fixture-not-a-secret",
        "OPENCODE_DISABLE_MODELS_FETCH": "true",
        "OPENCODE_DISABLE_PROJECT_CONFIG": "true",
        "OPENCODE_DISABLE_EXTERNAL_SKILLS": "true",
        **{"XDG_" + name + "_HOME": str(root / name.lower())
           for name in ("CONFIG", "DATA", "STATE", "CACHE")},
    }
    command = [binary, "--model", "custom-openai/glm-5.2", "run", "--format", "json",
               flag, "--", "Use general and explore subagents to find the fixture."]
    write_json(root / "command.json", command)
    try:
        completed = subprocess.run(command, env=env, cwd=corpus,
                                   capture_output=True, text=True, timeout=60)
        (root / "opencode.txt").write_text(completed.stdout)
        (root / "stderr.txt").write_text(completed.stderr)
        write_json(root / "requests.json", requests)
        assert completed.returncode == 0, completed.stderr
        sessions = json.loads(subprocess.check_output(
            [binary, "db", "select id,parent_id from session", "--format", "json"],
            env=env, cwd=corpus, text=True, timeout=30,
        ))
        write_json(root / "sessions.json", sessions)
        events = [json.loads(line) for line in completed.stdout.splitlines() if line.startswith("{")]
        root_requests = [r["body"] for r in requests if r["body"].get("tools") and not r["child"]]
        root_tools = [sorted(tool["function"]["name"] for tool in req["tools"]) for req in root_requests]
        child_sessions = [s for s in sessions if s["parent_id"] is not None]
        tools = [e["part"] for e in events if e["type"] == "tool_use"]
        summary = {
            "deny": deny, "flag": flag, "exit_code": completed.returncode,
            "root_tool_requests": len(root_requests),
            "advertised_tool_names": root_tools,
            "child_requests": sum(r["child"] for r in requests),
            "child_sessions": len(child_sessions),
            "session_count": len(sessions),
            "tool_events": [{"tool": p["tool"], "status": p["state"]["status"],
                             "output": p["state"].get("output"), "error": p["state"].get("error")}
                            for p in tools],
        }
        write_json(root / "summary.json", summary)
        assert len(root_requests) == 2, summary
        assert all(("task" not in names) == deny for names in root_tools), summary
        assert len(child_sessions) == (0 if deny else 2), summary
        assert summary["child_requests"] == (0 if deny else 2), summary
        assert [p["tool"] for p in tools] == (["invalid"] * 2 if deny else ["task"] * 2), summary
        return summary
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)



class NoSubagentConfigTests(unittest.TestCase):
    def test_disable_switch_changes_only_task_permissions_in_both_profiles(self) -> None:
        for profile in ("baseline", "zvec-grep"):
            with self.subTest(profile=profile):
                with patch.dict(os.environ, {"ZG_BENCH_DISABLE_SUBAGENTS": "0"}, clear=True):
                    model = resolve_opencode_model("custom-openai/glm-5.2").configuration
                    enabled = build_opencode_config(model, profile=profile)
                with patch.dict(os.environ, {"ZG_BENCH_DISABLE_SUBAGENTS": "1"}, clear=True):
                    disabled = build_opencode_config(model, profile=profile)
                self.assertEqual(disabled["permission"].pop("task"), "deny")
                self.assertEqual(len(disabled["agent"]), 7)
                for agent in disabled["agent"].values():
                    self.assertEqual(agent["permission"].pop("task"), "deny")
                self.assertEqual(enabled, disabled)


@unittest.skipUnless(
    os.environ.get("OPENCODE_TEST_BINARY"),
    "requires pinned OpenCode binary; all provider calls use a local fake server",
)
class NoSubagentRuntimeTests(unittest.TestCase):
    def test_forced_task_calls_cannot_create_children_with_auto_approval(self) -> None:
        binary = Path(os.environ["OPENCODE_TEST_BINARY"]).resolve()
        version = subprocess.check_output([str(binary), "--version"], text=True, timeout=30).strip()
        self.assertEqual(version, "1.18.4")
        evidence_parent = os.environ.get("OPENCODE_TEST_EVIDENCE_DIR")
        if evidence_parent:
            Path(evidence_parent).mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="no-subagent-contract-") as temp:
            output = (Path(tempfile.mkdtemp(prefix="contract-", dir=evidence_parent)).resolve()
                      if evidence_parent else Path(temp).resolve())
            summaries = {}
            for name, deny, flag in (
                ("allowed-control", False, "--dangerously-skip-permissions"),
                ("denied-skip-permissions", True, "--dangerously-skip-permissions"),
                ("denied-auto", True, "--auto"),
            ):
                with self.subTest(case=name):
                    summaries[name] = run_case(str(binary), output / name, deny=deny, flag=flag)
            self.assertEqual(len(summaries), 3)
            write_json(output / "summary.json", {
                "version": version,
                "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
                "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                "cases": summaries, "passed": True,
            })
            if evidence_parent:
                print(f"No-subagent guard evidence: {output}", flush=True)


if __name__ == "__main__":
    unittest.main()
