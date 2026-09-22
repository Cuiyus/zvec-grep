"""Contract checks for the original-task A/B runner configuration."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import native_session
import runner
from zg_bench.swe_qa.readonly_agents import qoder_contract


class OfficialPairContractTests(unittest.TestCase):
    def test_high_reasoning_and_equal_builtin_tools(self):
        base = {"protocol": native_session.PROTOCOL, "prompt": "Same original task",
                "model": "Qwen3.8-Max", "embedding_model": native_session.EMBEDDING_MODEL,
                "root": "/app", "task_mode": "official_writable", "reasoning_effort": "high",
                "limits": {"model_requests": 60, "tool_calls": 120,
                           "input_tokens": 6000000, "wall_seconds": 1800}}
        with tempfile.TemporaryDirectory() as folder:
            baseline = native_session.session_spec({**base, "profile": "baseline"}, Path(folder))
            treatment = native_session.session_spec({**base, "profile": "with-zg"}, Path(folder))
        self.assertEqual(baseline["command"], treatment["command"])
        command = baseline["command"]
        self.assertEqual(command[command.index("--reasoning-effort") + 1], "high")
        self.assertEqual(command[command.index("--tools") + 1], "default")
        self.assertNotIn("--disallowed-tools", command)
        self.assertNotIn("--allowed-tools", command)

    def test_original_prompt_is_identical_between_arms(self):
        with patch.dict("os.environ", {"WORKSPACE_QA_CORPUS_VARIANT": "pdf-text-v1"}):
            baseline = runner.official_instruction("Original task", "result.md", task_id="334")
            treatment = runner.official_instruction("Original task", "result.md", task_id="334")
        self.assertEqual(baseline, treatment)
        self.assertNotIn("zvec_grep_search", baseline)

    def test_frozen_two_task_lock(self):
        path = Path(__file__).resolve().parents[1] / "data/official-tasks-334-363-one-pair-lock.json"
        lock = json.loads(path.read_text())
        self.assertEqual([task["task_id"] for task in lock["tasks"]], ["334", "363"])
        self.assertEqual(lock["repetitions"], 1)
        self.assertEqual(lock["experiment"]["zg_version"], "0.2.2")
        self.assertEqual(lock["experiment"]["embedding"]["model"], "qwen/qwen3.7-text-embedding")
        self.assertEqual(lock["experiment"]["integration"]["command"],
                         ["zg", "install", "--target", "qoder", "--yes"])
        self.assertEqual(lock["experiment"]["protocol"], runner.PROTOCOL)

    def test_writable_contract_accepts_default_tools_in_both_arms(self):
        builtins = ["Read", "Grep", "Glob", "Bash", "Write", "TaskCreate"]
        for zg in (False, True):
            tools = builtins + (["mcp__zvec_grep__zvec_grep_search",
                                 "mcp__zvec_grep__zvec_grep_rg"] if zg else [])
            events = [{"type": "system", "subtype": "init", "tools": tools,
                       "permissionMode": "bypassPermissions",
                       "mcp_servers": [{"name": "zvec_grep", "status": "connected"}] if zg else []},
                      {"type": "assistant", "message": {"content": [
                          {"type": "tool_use", "name": "Write"}]}}]
            self.assertTrue(qoder_contract(events, zg=zg, official_writable=True)["valid"])
            self.assertFalse(qoder_contract(events, zg=zg)["valid"])


if __name__ == "__main__":
    unittest.main()
