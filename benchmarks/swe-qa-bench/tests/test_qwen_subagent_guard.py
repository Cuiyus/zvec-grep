"""Prove Qwen delegation permissions with the real pinned OpenCode runtime."""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import test_no_subagent_guard as guard
from zg_bench.engines.opencode.config import build_opencode_config
from zg_bench.engines.registry import resolve_opencode_model

MODEL = "custom-openai/qwen3.8-max"


class QwenSubagentConfigTests(unittest.TestCase):
    def test_historical_qwen_settings_and_only_task_permission_changes(self):
        with patch.dict(os.environ, {}, clear=True):
            model = resolve_opencode_model(MODEL).configuration
        self.assertEqual(model.harbor_model, MODEL)
        self.assertEqual(model.temperature, 0)
        self.assertTrue(model.enable_thinking)
        self.assertEqual(model.reasoning_effort, "high")
        self.assertEqual(model.output_limit, 32_000)
        for profile in ("baseline", "zvec-grep"):
            with self.subTest(profile=profile):
                with patch.dict(os.environ, {"ZG_BENCH_DISABLE_SUBAGENTS": "0"}, clear=True):
                    allowed = build_opencode_config(model, profile=profile)
                with patch.dict(os.environ, {"ZG_BENCH_DISABLE_SUBAGENTS": "1"}, clear=True):
                    denied = build_opencode_config(model, profile=profile)
                config = allowed["provider"]["custom-openai"]["models"]["qwen3.8-max"]
                self.assertEqual(config["interleaved"], {"field": "reasoning_content"})
                self.assertEqual(config["options"], {"enable_thinking": True, "reasoningEffort": "high"})
                self.assertEqual(config["limit"], {"context": 0, "output": 32_000})
                restored = copy.deepcopy(denied)
                self.assertEqual(restored["permission"].pop("task"), "deny")
                self.assertEqual(len(restored["agent"]), 7)
                for agent in restored["agent"].values():
                    self.assertEqual(agent["permission"].pop("task"), "deny")
                    self.assertEqual(agent["temperature"], 0)
                    self.assertEqual(agent["options"], {"seed": 42})
                self.assertEqual(allowed, restored)
                self.assertEqual("mcp" in allowed, profile == "zvec-grep")


@unittest.skipUnless(os.environ.get("OPENCODE_TEST_BINARY"),
                     "requires pinned OpenCode binary; provider calls are loopback-only")
class QwenSubagentRuntimeTests(unittest.TestCase):
    def test_actual_qwen_config_allows_and_denies_forced_delegation(self):
        binary = Path(os.environ["OPENCODE_TEST_BINARY"]).resolve()
        version = subprocess.check_output([str(binary), "--version"], text=True, timeout=30).strip()
        self.assertEqual(version, "1.18.4")
        evidence_parent = os.environ.get("OPENCODE_TEST_EVIDENCE_DIR")
        if evidence_parent:
            Path(evidence_parent).mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="qwen-subagent-contract-") as temp:
            output = (Path(tempfile.mkdtemp(prefix="qwen-contract-", dir=evidence_parent)).resolve()
                      if evidence_parent else Path(temp).resolve())
            summaries = {}
            for name, deny, flag in (
                ("allowed-control", False, "--dangerously-skip-permissions"),
                ("denied-skip-permissions", True, "--dangerously-skip-permissions"),
                ("denied-auto", True, "--auto"),
            ):
                with self.subTest(case=name):
                    case_root = output / name
                    summaries[name] = guard.run_case(str(binary), case_root, deny=deny,
                                                     flag=flag, model_name=MODEL)
                    requests = json.loads((case_root / "requests.json").read_text())
                    model_requests = [row["body"] for row in requests if row["body"].get("tools")]
                    self.assertTrue(model_requests)
                    for body in model_requests:
                        self.assertEqual(body["model"], "qwen3.8-max")
                        self.assertEqual(body["temperature"], 0)
                        self.assertEqual(body["seed"], 42)
                        self.assertEqual(body["max_tokens"], 32_000)
                        self.assertEqual(body["reasoning_effort"], "high")
                        self.assertIs(body["enable_thinking"], True)
            self.assertEqual(len(summaries), 3)
            guard.write_json(output / "summary.json", {
                "version": version, "model": MODEL,
                "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
                "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                "fixture_sha256": hashlib.sha256(Path(guard.__file__).read_bytes()).hexdigest(),
                "cases": summaries, "passed": True,
            })
            if evidence_parent:
                print(f"Qwen subagent guard evidence: {output}", flush=True)


if __name__ == "__main__":
    unittest.main()
