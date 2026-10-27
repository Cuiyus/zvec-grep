from __future__ import annotations

import copy
import json
import importlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from zg_bench.engines.opencode.config import build_opencode_config
from zg_bench.engines.registry import resolve_opencode_model
from zg_bench.rg_environment_probe import (
    agent_spec, fake_config, fixture_command, provider_source, validate_evidence,
)


def evidence(*, allowed: bool):
    snapshot = []
    session_ids = ["root", "general", "explore"] if allowed else ["root"]
    for session_id in session_ids:
        snapshot.append({"kind": "session", "data": json.dumps({
            "id": session_id, "parent_id": None if session_id == "root" else "root",
        })})
        snapshot.append({"kind": "tool", "data": json.dumps({
            "id": session_id + "-bash", "name": "bash", "session_id": session_id,
            "status": "completed", "output": "\n".join([
                "RG_PROBE_UID=0", "RG_PROBE_USER=root", "RG_PROBE_CWD=/app",
                "RG_PROBE_PATH=/usr/local/bin:/usr/bin", "RG_PROBE_BINARY=/usr/local/bin/rg",
                "RG_PROBE_SHA256=" + "a" * 64,
                "ripgrep 14.1.1", "1:RG_PROBE_NEEDLE", "1:Conan README", "RG_PROBE_SUCCESS",
            ]),
        })})
    if allowed:
        for child in ("general", "explore"):
            snapshot.append({"kind": "tool", "data": json.dumps({
                "id": child + "-task", "name": "task", "session_id": "root",
                "status": "completed", "input": {"subagent_type": child},
                "child_session_id": child,
            })})
    requests = [{"child": False, "body": {"model": "qwen3.8-max", "tools": [
        {"function": {"name": name}} for name in (["bash", "task"] if allowed else ["bash"])
    ]}}]
    return snapshot, requests


class ProbeTests(unittest.TestCase):
    def validate(self, snapshot, requests, allowed):
        return validate_evidence(snapshot, requests, allow_children=allowed,
                                 model="qwen3.8-max", version="14.1.1", binary_sha256="a" * 64)

    def test_accepts_real_tool_evidence_for_root_and_both_child_types(self):
        for allowed in (False, True):
            with self.subTest(allowed=allowed):
                result = self.validate(*evidence(allowed=allowed), allowed)
                self.assertTrue(result["passed"])
                self.assertEqual(result["successful_bash_checks"], 3 if allowed else 1)
                self.assertEqual(result["delegated_agents"], ["explore", "general"] if allowed else [])
                self.assertTrue(result["model_usage_is_synthetic"])

    def test_child_environment_failure_cannot_pass_from_root_success(self):
        snapshot, requests = evidence(allowed=True)
        row = json.loads(snapshot[3]["data"])
        row["output"] = "bash: rg: command not found"
        snapshot[3]["data"] = json.dumps(row)
        with self.assertRaisesRegex(ValueError, "bash check failed"):
            self.validate(snapshot, requests, True)

    def test_inconsistent_child_path_fails(self):
        snapshot, requests = evidence(allowed=True)
        snapshot[3]["data"] = snapshot[3]["data"].replace("/usr/local/bin:/usr/bin", "/usr/bin")
        with self.assertRaisesRegex(ValueError, "identities/PATH/workdir differ"):
            self.validate(snapshot, requests, True)

    def test_denied_arm_rejects_advertised_task_tool_and_child_sessions(self):
        snapshot, requests = evidence(allowed=False)
        requests[0]["body"]["tools"].append({"function": {"name": "task"}})
        with self.assertRaisesRegex(ValueError, "delegation tools"):
            self.validate(snapshot, requests, False)
        snapshot, requests = evidence(allowed=True)
        with self.assertRaisesRegex(ValueError, "session tree"):
            self.validate(snapshot, requests, False)

    def test_version_mismatch_and_unfinished_tool_fail(self):
        for change in ("version", "status"):
            snapshot, requests = evidence(allowed=False)
            row = json.loads(snapshot[1]["data"])
            if change == "version":
                row["output"] = row["output"].replace("14.1.1", "13.0.0")
            else:
                row["status"] = "error"
            snapshot[1]["data"] = json.dumps(row)
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, "bash check failed"):
                self.validate(snapshot, requests, False)

    def test_scripted_provider_keeps_exact_model_and_permission_configuration(self):
        for model_name in ("custom-openai/qwen3.8-max", "custom-openai/glm-5.2"):
            for profile in ("baseline", "zvec-grep"):
                for deny in ("0", "1"):
                    with self.subTest(model=model_name, profile=profile, deny=deny):
                        with patch.dict("os.environ", {"ZG_BENCH_DISABLE_SUBAGENTS": deny}):
                            model = resolve_opencode_model(model_name).configuration
                            original = build_opencode_config(model, profile=profile)
                        before = copy.deepcopy(original)
                        configured = fake_config(original)
                        self.assertEqual(original, before)
                        self.assertEqual(configured["permission"], original["permission"])
                        self.assertEqual(configured["provider"]["custom-openai"]["models"],
                                         original["provider"]["custom-openai"]["models"])
                        self.assertEqual(configured.get("mcp"), original.get("mcp"))
                        for name, agent in original["agent"].items():
                            changed = dict(configured["agent"][name])
                            changed.pop("steps", None)
                            self.assertEqual(changed, agent)
                        self.assertEqual(configured["provider"]["custom-openai"]["options"]["baseURL"],
                                         "http://127.0.0.1:18765/v1")

    def test_actual_runner_constructor_values_parse_without_type_changes(self):
        command = ["harbor", "run", "--agent", "zg_bench.agents.opencode:ResilientOpenCode",
            "--model", "custom-openai/glm-5.2", "--agent-kwarg", "version=1.18.4",
            "--agent-kwarg", "collect_session_usage=true", "--agent-kwarg", 'opencode_config={"permission":{"task":"deny"}}']
        adapter, model, kwargs = agent_spec(command)
        self.assertEqual(adapter, "zg_bench.agents.opencode:ResilientOpenCode")
        self.assertEqual(model, "custom-openai/glm-5.2")
        self.assertEqual(kwargs, {"version": "1.18.4", "collect_session_usage": True,
                                 "opencode_config": {"permission": {"task": "deny"}}})

    def test_production_runner_kwargs_instantiate_every_adapter_model_combination(self):
        from zg_bench import runner

        suite = runner.load_suite("swe-qa-bench", tier="full", task_overrides=["conan-19"])
        with tempfile.TemporaryDirectory() as temporary, patch.dict("os.environ", {
            "GLM_API_KEY": "offline-rg-probe-not-a-secret", "ZG_BENCH_DISABLE_SUBAGENTS": "1",
        }):
            for model in ("custom-openai/qwen3.8-max", "custom-openai/glm-5.2"):
                for profile in ("baseline", "zvec-grep"):
                    with self.subTest(model=model, profile=profile):
                        command = runner.build_harbor_command(suite, profile=profile,
                            agent="opencode", model=model, jobs_dir=Path(temporary), job_name="probe",
                            zvec_grep_package="/tmp/zg-bench-zvec-grep.tgz",
                            zvec_grep_package_sha256="a" * 64,
                            embedding_model="local/potion-code-16m-v2")
                        adapter, actual_model, kwargs = agent_spec(command)
                        module, cls = adapter.split(":")
                        agent = getattr(importlib.import_module(module), cls)(
                            logs_dir=Path(temporary), model_name=actual_model, **kwargs)
                        self.assertEqual(agent.model_name, model)
                        self.assertEqual(agent.version(), "1.18.4")
                        self.assertEqual(type(agent).__name__,
                            "ZvecOpenCode" if profile == "zvec-grep" else "ResilientOpenCode")

    def test_fixture_has_command_lookup_strict_version_and_repo_search(self):
        command = fixture_command("14.1.1")
        for expected in ("command -v rg", "rg --version", "ripgrep 14.1.1",
                         "sha256sum", "id -u", "pwd -P", '"$PATH"',
                         "rg -n -x -F RG_PROBE_NEEDLE", "probe_file=README.md", "RG_PROBE_SUCCESS"):
            self.assertIn(expected, command)
        source = provider_source(command, allow_children=True)
        self.assertIn('server.listen(settings.port, "127.0.0.1"', source)
        self.assertIn('"general", "explore"', source)
        self.assertNotIn("https://", source)


if __name__ == "__main__":
    unittest.main()
