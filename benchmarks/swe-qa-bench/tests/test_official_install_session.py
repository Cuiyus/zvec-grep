"""Official-install contracts; no paid model or external provider requests.

The installer fixture models the released 0.2.2 default stdio command, not the
historical benchmark bridge. Optional pinned-client checks use loopback only.
"""
from __future__ import annotations

import copy
from contextlib import ExitStack
import importlib.util
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "official-install-session.py"
MODULE_SPEC = importlib.util.spec_from_file_location("official_install_session_test", SCRIPT)
official = importlib.util.module_from_spec(MODULE_SPEC)
MODULE_SPEC.loader.exec_module(official)

GUIDANCE = "<!-- ZVEC_GREP_START -->\n## zvec-grep\nOFFICIAL_DISCOVERY_SENTINEL\n<!-- ZVEC_GREP_END -->\n"
LIMITS = {"model_requests": 30, "tool_calls": 60, "input_tokens": 300000, "wall_seconds": 900}


class OfficialInstallSessionTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="official-install-test-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        self.home = self.root / "home"
        self.home.mkdir()
        self.workspace = self.root / "corpus"
        self.workspace.mkdir()
        (self.workspace / "source.py").write_text("def evidence():\n    return 1\n")
        self.commands = []
        self.installed_bytes = {}

    def spec(self, *, agent="opencode", zg=True):
        return {"agent": agent, "model": "glm-5.2" if agent == "opencode" else "qwen3.8-max",
                "profile": "zvec-grep" if zg else "baseline", "root": str(self.workspace),
                "log_dir": str(self.root / "logs"), "instruction": "Explain the repository; do not modify source.",
                "base_url": "http://127.0.0.1:8765/v1" if agent == "opencode" else None,
                "limits": dict(LIMITS), "prepare_only": True}

    def test_default_paths_are_the_clients_native_global_locations(self):
        paths = official.default_paths("opencode", home=self.home)
        self.assertEqual(Path(paths["config"]), self.home / ".config" / "opencode" / "opencode.json")
        self.assertEqual(Path(paths["guidance"]), self.home / ".config" / "opencode" / "AGENTS.md")
        qoder = official.default_paths("qodercli", home=self.home)
        self.assertEqual(Path(qoder["config"]), self.home / ".qoder" / "settings.json")
        self.assertEqual(Path(qoder["guidance"]), self.home / ".qoder" / "AGENTS.md")
        self.assertEqual(Path(qoder["ide_config"]), self.home / ".qoder" / "mcp.json")

    def test_common_configuration_does_not_advertise_benchmark_mcp_or_guidance(self):
        for agent in ("opencode", "qodercli"):
            baseline = official.base_config(self.spec(agent=agent, zg=False))
            zg = official.base_config(self.spec(agent=agent, zg=True))
            with self.subTest(agent=agent):
                if agent == "opencode":
                    baseline = copy.deepcopy(baseline)
                    baseline.setdefault("permission", {})["zvec_grep_*"] = "allow"
                self.assertEqual(zg, baseline, "Only permission pre-approval may precede the actual installation")
                self.assertFalse(zg.get("mcpServers"))
                if agent == "opencode":
                    self.assertFalse(zg.get("mcp"))
                self.assertNotIn("instructions", zg)
                self.assertNotIn("systemPrompt", zg)

    def test_agent_argv_uses_native_discovery_without_manual_prompt_delivery(self):
        forbidden = {"--append-system-prompt", "--system-prompt", "--settings", "--mcp-config", "--strict-mcp-config", "--config-dir"}
        for agent in ("opencode", "qodercli"):
            for zg in (False, True):
                spec = self.spec(agent=agent, zg=zg)
                command = official.agent_command(spec)
                with self.subTest(agent=agent, zg=zg):
                    self.assertEqual(command[0], agent)
                    self.assertEqual(command[-1], spec["instruction"])
                    self.assertFalse(set(command) & forbidden)
                    if "--setting-sources" in command:
                        self.assertIn("user", command[command.index("--setting-sources") + 1].split(","))
                    self.assertNotIn(GUIDANCE, command)

    def installed_config(self, before, *, agent="opencode"):
        result = copy.deepcopy(before)
        if agent == "opencode":
            result["mcp"] = {"zvec_grep": {"type": "local", "command": ["zg", "server", "--stdio"],
                                           "enabled": True, "timeout": 600000}}
        else:
            result["mcpServers"] = {"zvec_grep": {"command": "zg", "args": ["server", "--stdio"],
                                                   "trust": True, "timeout": 600000,
                                                   "description": "Managed by zg install; managed permissions=zvec_grep_search,zvec_grep_rg",
                                                   "alwaysAllow": ["zvec_grep_search", "zvec_grep_rg"]}}
            allow = result.setdefault("permissions", {}).setdefault("allow", [])
            for tool in ("zvec_grep_search", "zvec_grep_rg"):
                rule = "mcp__zvec_grep__" + tool
                if rule not in allow:
                    allow.append(rule)
        return result

    def test_installed_default_command_is_preserved_for_both_agents(self):
        for agent in ("opencode", "qodercli"):
            before = official.base_config(self.spec(agent=agent))
            installed = self.installed_config(before, agent=agent)
            original = copy.deepcopy(installed)
            with self.subTest(agent=agent):
                result = official.verify_installed(before, installed, GUIDANCE, agent)
                self.assertTrue(result["valid"])
                self.assertEqual(result["native_mcp_command"], ["zg", "server", "--stdio"])
                self.assertEqual(installed, original, "Validation cannot repair or rewrite the installer output")

    def test_bridge_command_or_custom_tool_subset_cannot_pass_as_official_install(self):
        before = official.base_config(self.spec())
        for change in ({"command": ["node", "/opt/qa/readonly-search.mjs"]},
                       {"command": ["node", "/opt/qa/native-mcp-tap.mjs", "--", "zg", "server", "--stdio"]},
                       {"command": ["zg", "server", "--stdio", "--mcp-toolset", "full"]},
                       {"includeTools": ["zvec_grep_search"]}):
            installed = self.installed_config(before)
            installed["mcp"]["zvec_grep"].update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                official.verify_installed(before, installed, GUIDANCE, "opencode")

    def test_manual_guidance_injection_and_missing_install_guidance_are_rejected(self):
        before = official.base_config(self.spec())
        for extra in ({"instructions": ["/tmp/manual-AGENTS.md"]}, {"systemPrompt": "manual guidance"}):
            installed = self.installed_config(before)
            installed.update(extra)
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                official.verify_installed(before, installed, GUIDANCE, "opencode")
        with self.assertRaises(ValueError):
            official.verify_installed(before, self.installed_config(before), "", "opencode")

    def test_host_config_redirects_are_not_silently_inherited(self):
        redirects = {"OPENCODE_CONFIG": "/host/opencode.json", "OPENCODE_CONFIG_CONTENT": '{"instructions":["/host/rules.md"]}',
                     "OPENCODE_CONFIG_DIR": "/host/opencode", "QODER_CONFIG_DIR": "/host/qoder",
                     "QODER_IDE_MCP_PATH": "/host/qoder/mcp.json", "XDG_CONFIG_HOME": "/host/config"}
        for agent in ("opencode", "qodercli"):
            for name, value in redirects.items():
                with self.subTest(agent=agent, variable=name), patch.dict(os.environ, {name: value}, clear=True):
                    try:
                        environment = official.isolated_environment(self.spec(agent=agent))
                    except ValueError:
                        continue  # A deterministic preflight failure is also safe.
                    self.assertNotEqual(environment.get(name), value)

    def test_installer_cannot_change_the_model_or_common_readonly_permissions(self):
        for agent in ("opencode", "qodercli"):
            before = official.base_config(self.spec(agent=agent))
            for property_name, value in (("model", "unplanned-model"), ("permission" if agent == "opencode" else "permissions", {"*": "allow"})):
                installed = self.installed_config(before, agent=agent)
                installed[property_name] = value
                with self.subTest(agent=agent, property=property_name), self.assertRaises(ValueError):
                    official.verify_installed(before, installed, GUIDANCE, agent)

    def fake_checked(self, command, *, cwd, env, log_prefix, timeout=1200):
        self.commands.append(list(command))
        if command[-1] == "--version":
            return {"zg": "0.2.2", "opencode": "1.18.4", "qodercli": "1.1.45"}[command[0]]
        if command[:2] == ["zg", "index"]:
            path = Path(cwd) / ".zvec-grep"
            path.mkdir(exist_ok=True)
            (path / "manifest.json").write_text('{"fixture":"index built by setup"}\n')
        if command[:2] == ["zg", "install"]:
            target = command[command.index("--target") + 1]
            agent = "opencode" if target == "opencode" else "qodercli"
            paths = official.default_paths(agent, home=self.home)
            config_path = Path(paths["config"])
            before = json.loads(config_path.read_text())
            installed = self.installed_config(before, agent=agent)
            # Deliberate formatting verifies that the runner retains the actual
            # file produced by the installer instead of reconstructing it.
            raw = (json.dumps(installed, ensure_ascii=False, indent=4) + "\n\n").encode()
            config_path.write_bytes(raw)
            Path(paths["guidance"]).write_text(GUIDANCE)
            self.installed_bytes[agent] = raw
            if agent == "qodercli":
                Path(paths["ide_config"]).write_text(json.dumps({"mcpServers": installed["mcpServers"]}))
        return "fixture command completed\n"

    def prepared(self, spec, session_runner=None):
        with ExitStack() as stack:
            stack.enter_context(patch.object(Path, "home", return_value=self.home))
            stack.enter_context(patch.dict(os.environ, {"PATH": "/usr/bin:/bin"}, clear=True))
            stack.enter_context(patch.object(official, "run_checked", side_effect=self.fake_checked))
            stack.enter_context(patch.object(official, "assert_source_readonly"))
            stack.enter_context(patch.object(official, "source_identity", return_value={"source.py": "fixed-source-fixture"}))
            if session_runner is not None:
                stack.enter_context(patch.object(official, "load_qa_session", return_value=SimpleNamespace(run=session_runner)))
            probe = stack.enter_context(patch.object(official, "probe_installed_mcp", return_value={
                "tools": [{"name": "zvec_grep_search", "description": "released fixture", "inputSchema": {"type": "object"}}]}))
            result = official.run(spec)
        return result, probe

    def test_prepare_installs_then_keeps_exact_config_and_guidance_bytes(self):
        for agent in ("opencode", "qodercli"):
            self.home = self.root / ("home-" + agent)
            self.home.mkdir()
            spec = self.spec(agent=agent)
            spec["log_dir"] = str(self.root / (agent + "-logs"))
            with self.subTest(agent=agent):
                result, probe = self.prepared(spec)
                self.assertEqual(result, 0)
                install = [command for command in self.commands if command[:2] == ["zg", "install"]][-1]
                self.assertEqual(install, ["zg", "install", "--target", "opencode" if agent == "opencode" else "qoder", "--yes"])
                paths = official.default_paths(agent, home=self.home)
                self.assertEqual(Path(paths["config"]).read_bytes(), self.installed_bytes[agent])
                self.assertEqual(Path(paths["guidance"]).read_text(), GUIDANCE)
                self.assertEqual(probe.call_count, 1)
                self.assertEqual(probe.call_args.args[0], ["zg", "server", "--stdio"])
            # Each real sample has a fresh corpus/index mount.
            manifest = self.workspace / ".zvec-grep" / "manifest.json"
            if manifest.exists():
                manifest.unlink()

    def test_baseline_never_installs_or_probes_zg(self):
        result, probe = self.prepared(self.spec(zg=False))
        self.assertEqual(result, 0)
        self.assertFalse(any(command[0] == "zg" for command in self.commands))
        probe.assert_not_called()
        manifest = json.loads((self.root / "logs" / "install-manifest.json").read_text())
        self.assertEqual(manifest["new_index_builds"], 0)
        paths = official.default_paths("opencode", home=self.home)
        self.assertFalse(Path(paths["guidance"]).exists())
        config = json.loads(Path(paths["config"]).read_text())
        self.assertFalse(config.get("mcp"))
        self.assertNotIn("instructions", config)

    def test_dirty_global_installation_stops_before_any_setup_or_model_execution(self):
        paths = official.default_paths("opencode", home=self.home)
        path = Path(paths["guidance"])
        path.parent.mkdir(parents=True)
        path.write_text("Host instructions must not leak into a new sample.\n")
        with self.assertRaisesRegex(ValueError, "clean native paths"):
            self.prepared(self.spec())
        self.assertFalse(any(command[:2] == ["zg", "install"] for command in self.commands))
        self.assertEqual(path.read_text(), "Host instructions must not leak into a new sample.\n")

    def observed_guidance(self, copies=1):
        paths = official.default_paths("opencode", home=self.home)
        Path(paths["guidance"]).parent.mkdir(parents=True, exist_ok=True)
        Path(paths["guidance"]).write_text(GUIDANCE)
        logs = self.root / "wire-observation"
        requests = logs / "wire-requests"
        requests.mkdir(parents=True, exist_ok=True)
        (requests / "request-001.json").write_text(json.dumps({"messages": [{"role": "system", "content": "Generate a title."}], "tools": []}))
        (requests / "request-001.raw.json").write_text("raw HTTP artifact; not the parsed model request")
        (requests / "request-002.json").write_text(json.dumps({"messages": [{"role": "system", "content": GUIDANCE * copies},
                                                                            {"role": "user", "content": self.spec()["instruction"]}],
                                                               "tools": [{"type": "function", "function": {"name": "read"}}]}))
        (logs / "wire.jsonl").write_text("\n".join(json.dumps(value) for value in [
            {"event": "request", "request_id": 1, "tool_names": []},
            {"event": "request", "request_id": 2, "tool_names": ["read"]}]) + "\n")
        return official.verify_guidance_delivery(self.spec(), paths, logs)

    def test_guidance_observation_uses_the_first_qa_request_not_background_title(self):
        self.assertIs(self.observed_guidance()["guidance_loaded"], True)

    def test_duplicate_guidance_is_not_reported_as_verified_native_delivery(self):
        self.assertIs(self.observed_guidance(copies=2)["guidance_loaded"], False)

    def test_qoder_unobserved_system_prompt_stays_unknown(self):
        spec = self.spec(agent="qodercli")
        paths = official.default_paths("qodercli", home=self.home)
        Path(paths["guidance"]).parent.mkdir(parents=True)
        Path(paths["guidance"]).write_text(GUIDANCE)
        value = official.verify_guidance_delivery(spec, paths, self.root / "qoder-logs")
        self.assertIsNone(value["guidance_loaded"])

    def test_byte_identity_is_separate_from_verified_native_security_scan_defaults(self):
        before = official.base_config(self.spec(agent="qodercli"))
        raw = (json.dumps(before, indent=2) + "\n").encode()
        after = json.dumps({**before, "securityScan": dict(official.QODER_SECURITY_SCAN_DEFAULTS)}, indent=2).encode()
        result = official.verify_agent_config_integrity("qodercli", "1.1.45", raw, after)
        self.assertFalse(result["agent_config_unchanged"])
        self.assertTrue(result["agent_config_contract_valid"])
        self.assertEqual(result["agent_config_change"], "qoder_1_1_45_security_scan_defaults")
        self.assertEqual(len(result["agent_config_allowed_added_fields"]), 3)
        # This pinned baseline fixture reproduces the recorded final bytes of
        # the five native baseline sessions in CI 34922580478.
        self.assertEqual(official.digest(after), "022c332a0b6f9bf4665c0a9acab67d0018c81600298862ebd41d1ed69c820224")

    def test_qoder_only_format_change_requires_exact_pinned_agent_version(self):
        before = b'{"model":{"name":"Qwen3.8-Max"},"enabled":true}\n'
        formatted = b'{\n  "enabled": true,\n  "model": {"name": "Qwen3.8-Max"}\n}'
        for agent, version, expected in (("qodercli", "1.1.45", True), ("qodercli", "1.1.46", False),
                                          ("qodercli", None, False), ("opencode", "1.18.4", False)):
            with self.subTest(agent=agent, version=version):
                result = official.verify_agent_config_integrity(agent, version, before, formatted)
                self.assertFalse(result["agent_config_unchanged"])
                self.assertIs(result["agent_config_contract_valid"], expected)
                unchanged = official.verify_agent_config_integrity(agent, version, before, before)
                self.assertTrue(unchanged["agent_config_unchanged"])
                self.assertTrue(unchanged["agent_config_contract_valid"])

    def test_security_scan_allowance_never_masks_other_content_changes(self):
        before = self.installed_config(official.base_config(self.spec(agent="qodercli")), agent="qodercli")
        raw = json.dumps(before).encode()
        modifications = [
            lambda value: value["model"].update(name="different-model"),
            lambda value: value["permissions"]["allow"].append("Bash"),
            lambda value: value["permissions"]["deny"].remove("Write"),
            lambda value: value["mcpServers"]["zvec_grep"].update(command="node"),
            lambda value: value.update(instructions=["/tmp/manual-guidance.md"]),
            lambda value: value["tools"].update(core=["Bash"]),
            lambda value: value["security"].update(disableYoloMode=False),
            lambda value: value["securityScan"].update(l1StaticCheck=1),
            lambda value: value["securityScan"].update(l1StaticCheck=False),
            lambda value: value["securityScan"].update(extra=True),
            lambda value: value["securityScan"].pop("l2LightweightScan"),
            lambda value: value.pop("disableAllHooks"),
        ]
        for index, change in enumerate(modifications):
            after = {**copy.deepcopy(before), "securityScan": dict(official.QODER_SECURITY_SCAN_DEFAULTS)}
            change(after)
            with self.subTest(change=index):
                result = official.verify_agent_config_integrity("qodercli", "1.1.45", raw, json.dumps(after).encode())
                self.assertFalse(result["agent_config_contract_valid"])

    def test_existing_security_scan_values_cannot_be_changed_by_default_exception(self):
        for existing in ({"l1StaticCheck": False}, {}, {**official.QODER_SECURITY_SCAN_DEFAULTS, "l3DeepScan": False}):
            before = {"model": {"name": "Qwen3.8-Max"}, "securityScan": existing}
            after = {**before, "securityScan": dict(official.QODER_SECURITY_SCAN_DEFAULTS)}
            with self.subTest(existing=existing):
                result = official.verify_agent_config_integrity("qodercli", "1.1.45", json.dumps(before).encode(), json.dumps(after).encode())
                self.assertFalse(result["agent_config_contract_valid"])

    def test_missing_invalid_or_duplicate_final_json_fails_closed(self):
        before = b'{"model":{"name":"Qwen3.8-Max"}}'
        for after in (None, b'[]', b'{} trailing', b'{"model":{},"model":{"name":"Qwen3.8-Max"}}',
                      b'{"value":NaN}', b'{"value":1e999}', b'\xff'):
            with self.subTest(after=after):
                result = official.verify_agent_config_integrity("qodercli", "1.1.45", before, after)
                self.assertFalse(result["agent_config_contract_valid"])

    def test_final_config_capture_preserves_bytes_and_separates_redacted_evidence_digest(self):
        path = self.root / "settings.json"
        raw = b'{"model":{"name":"Qwen3.8-Max"}}\n'
        path.write_bytes(raw)
        observed, evidence = official.capture_final_config(path, self.root, {})
        self.assertEqual(observed, raw)
        self.assertEqual((self.root / "agent-config-final.json").read_bytes(), raw)
        self.assertFalse(evidence["final_agent_config_evidence"]["redacted"])
        for raw, env in ((b'{"note":"fixture-pat","apiKey":"fixture-secret"}', {"QODER_PERSONAL_ACCESS_TOKEN": "fixture-pat"}),
                         (b'{"token":"other-sensitive-value","apiKey":"{env:OPENAI_API_KEY}"}', {}),
                         (b'bad JSON with unknown sensitive fields', {})):
            path.write_bytes(raw)
            observed, evidence = official.capture_final_config(path, self.root, env)
            snapshot = (self.root / "agent-config-final.json").read_bytes()
            with self.subTest(raw=raw):
                self.assertEqual(observed, raw)
                self.assertEqual(evidence["final_agent_config_sha256"], official.digest(raw))
                self.assertEqual(evidence["final_agent_config_evidence"]["sha256"], official.digest(snapshot))
                self.assertTrue(evidence["final_agent_config_evidence"]["redacted"])
                for secret in (b'fixture-pat', b'fixture-secret', b'other-sensitive-value', b'unknown sensitive fields'):
                    self.assertNotIn(secret, snapshot)

    def fake_native_session(self, mutation):
        def run(session):
            mutation()
            official.save(Path(session["log_dir"]) / "session.json", {"status": "completed", "returncode": 0})
            return 0
        return run

    def test_native_qoder_default_write_completes_without_rewriting_installed_evidence(self):
        spec = self.spec(agent="qodercli")
        spec["prepare_only"] = False
        def mutation():
            path = official.default_paths("qodercli", home=self.home)["config"]
            after = json.loads(path.read_text())
            after["securityScan"] = dict(official.QODER_SECURITY_SCAN_DEFAULTS)
            path.write_text(json.dumps(after, indent=2))
        code, _ = self.prepared(spec, self.fake_native_session(mutation))
        self.assertEqual(code, 0)
        logs = Path(spec["log_dir"])
        manifest = json.loads((logs / "install-manifest.json").read_text())
        self.assertEqual(manifest["status"], "completed")
        self.assertFalse(manifest["agent_config_unchanged"])
        self.assertTrue(manifest["agent_config_contract_valid"])
        self.assertTrue(manifest["guidance_unchanged"])
        self.assertEqual((logs / "agent-config-installed.json").read_bytes(), self.installed_bytes["qodercli"])
        self.assertEqual(official.digest((logs / "agent-config-final.json").read_bytes()), manifest["final_agent_config_sha256"])
        self.assertEqual(json.loads((logs / "session.json").read_text())["status"], "completed")

    def test_native_model_change_is_contract_failure_with_final_evidence(self):
        spec = self.spec(agent="qodercli")
        spec["prepare_only"] = False
        def mutation():
            path = official.default_paths("qodercli", home=self.home)["config"]
            after = json.loads(path.read_text())
            after.update(securityScan=dict(official.QODER_SECURITY_SCAN_DEFAULTS))
            after["model"]["name"] = "different-model"
            path.write_text(json.dumps(after, indent=2))
        code, _ = self.prepared(spec, self.fake_native_session(mutation))
        logs = Path(spec["log_dir"])
        manifest = json.loads((logs / "install-manifest.json").read_text())
        self.assertEqual(code, 4)
        self.assertEqual(manifest["status"], "contract_failure")
        self.assertFalse(manifest["agent_config_contract_valid"])
        self.assertTrue((logs / "agent-config-final.json").is_file())
        self.assertEqual(json.loads((logs / "session.json").read_text())["status"], "completed")

    def test_removed_guidance_is_contract_failure_and_still_captures_final_config(self):
        spec = self.spec(agent="qodercli")
        spec["prepare_only"] = False
        def mutation():
            official.default_paths("qodercli", home=self.home)["guidance"].unlink()
        code, _ = self.prepared(spec, self.fake_native_session(mutation))
        logs = Path(spec["log_dir"])
        manifest = json.loads((logs / "install-manifest.json").read_text())
        self.assertEqual(code, 4)
        self.assertEqual(manifest["status"], "contract_failure")
        self.assertFalse(manifest["guidance_unchanged"])
        self.assertTrue(manifest["agent_config_unchanged"])
        self.assertTrue((logs / "agent-config-final.json").is_file())


@unittest.skipUnless(os.environ.get("OPENCODE_READONLY_TEST_BINARY"),
                     "requires pinned OpenCode; only a loopback fake provider is used")
class OpenCodeOfficialDiscoveryContractTests(unittest.TestCase):
    def test_native_global_agents_discovery_reaches_the_model_once_without_prompt_injection(self):
        """Exercise client discovery, rather than asserting a guidance file exists.

        This is a discovery contract with a local installer fixture, not a zg
        quality result or a claim that Qoder's private request is observable.
        """
        binary = os.environ["OPENCODE_READONLY_TEST_BINARY"]
        self.assertEqual(subprocess.check_output([binary, "--version"], text=True, timeout=10).strip(), "1.18.4")
        with tempfile.TemporaryDirectory(prefix="official-discovery-contract-") as directory:
            root = Path(directory).resolve()
            corpus = root / "corpus"
            corpus.mkdir()
            requests = []

            class FakeProvider(BaseHTTPRequestHandler):
                def log_message(self, *_args):
                    pass

                def do_POST(self):
                    body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                    requests.append(body)
                    chunks = [{"id": "fixture", "object": "chat.completion.chunk", "created": 1, "model": body["model"],
                               "choices": [{"index": 0, "delta": {"role": "assistant", "content": "Fixture completed."}, "finish_reason": None}]},
                              {"id": "fixture", "object": "chat.completion.chunk", "created": 1, "model": body["model"],
                               "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                               "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}]
                    payload = "".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks) + "data: [DONE]\n\n"
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Content-Length", str(len(payload.encode())))
                    self.end_headers()
                    self.wfile.write(payload.encode())

            server = ThreadingHTTPServer(("127.0.0.1", 0), FakeProvider)
            server.daemon_threads = True
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                spec = {"agent": "opencode", "model": "glm-5.2", "profile": "baseline",
                        "instruction": "Return the fixture response.", "limits": dict(LIMITS),
                        "base_url": f"http://127.0.0.1:{server.server_port}/v1"}
                paths = official.default_paths("opencode", home=root)
                config_path = Path(paths["config"])
                config_path.parent.mkdir(parents=True)
                config = official.base_config(spec)
                self.assertNotIn("instructions", config)
                config_path.write_text(json.dumps(config))
                Path(paths["guidance"]).write_text(GUIDANCE)
                env = {"PATH": "/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin", "LANG": "en_US.UTF-8",
                       "OPENAI_API_KEY": "offline-fixture-not-a-real-key", "OPENCODE_DISABLE_MODELS_FETCH": "true",
                       "OPENCODE_DISABLE_AUTOUPDATE": "true", "OPENCODE_DISABLE_PROJECT_CONFIG": "true",
                       "OPENCODE_DISABLE_EXTERNAL_SKILLS": "true", "OPENCODE_DISABLE_LSP_DOWNLOAD": "true",
                       "XDG_CONFIG_HOME": str(root / ".config"), "XDG_DATA_HOME": str(root / "data"),
                       "XDG_STATE_HOME": str(root / "state"), "XDG_CACHE_HOME": str(root / "cache")}
                command = official.agent_command(spec)
                command[0] = binary
                result = subprocess.run(command, env=env, cwd=corpus, capture_output=True, text=True, timeout=45)
                self.assertEqual(result.returncode, 0, result.stderr)
                task_requests = [body for body in requests if body.get("tools")]
                self.assertTrue(task_requests, "The native QA request must be observed, not just a title request")
                instruction_parts = []
                for message in task_requests[0].get("messages", []):
                    if message.get("role") not in {"system", "developer"}:
                        continue
                    content = message.get("content")
                    if isinstance(content, str):
                        instruction_parts.append(content)
                    elif isinstance(content, list):
                        instruction_parts.extend(part["text"] for part in content if isinstance(part, dict) and isinstance(part.get("text"), str))
                self.assertEqual(sum(text.count(GUIDANCE.strip()) for text in instruction_parts), 1)
                self.assertNotIn("--append-system-prompt", command)
                self.assertNotIn("OPENCODE_CONFIG", env)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
