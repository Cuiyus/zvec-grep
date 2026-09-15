from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "native-agent-session.py"
MODULE_SPEC = importlib.util.spec_from_file_location("native_agent_session_test", SCRIPT)
native = importlib.util.module_from_spec(MODULE_SPEC)
MODULE_SPEC.loader.exec_module(native)


class NativeInstallTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.calls = []

    def spec(self, *, agent="opencode", arm="zg", variant="P00"):
        return {"agent": agent, "model": "glm-5.2" if agent == "opencode" else "qwen3.8-max",
                "arm": arm, "prompt_variant": variant, "instruction": "Explain the code.",
                "workspace": str(self.root), "log_dir": str(self.root / "logs"),
                "config_root": str(self.root / "config"), "model_cache": str(self.root / "models"),
                "limits": {"model_requests": 30, "tool_calls": 60, "input_tokens": 300000, "wall_seconds": 900},
                "base_config": {"permission": {"*": "deny", "read": "allow"}} if agent == "opencode" else {
                    "permissions": {"allow": ["Read", "Grep", "Glob"], "deny": ["Write"]}, "mcpServers": {}}}

    def fake_command(self, argv, *, name, root, env, cwd, timeout=1200):
        self.calls.append(argv)
        if argv[-1] == "--version":
            return native.VERSIONS[argv[0]] + "\n", {"returncode": 0}
        if name == "zg-install":
            if argv[argv.index("--target") + 1] == "opencode":
                path = Path(env["OPENCODE_CONFIG"])
                content = {"mcp": {"zvec_grep": {"type": "local", "enabled": True, "timeout": 600000,
                                                   "command": ["zg", "mcp", "--mcp-toolset", "agent"]}}}
            else:
                path = Path(env["QODER_CONFIG_DIR"]) / "settings.json"
                content = {"mcpServers": {"zvec_grep": {"command": "zg", "args": ["mcp", "--mcp-toolset", "agent"],
                                                         "trust": True, "timeout": 600000}},
                           "permissions": {"allow": [*native.native_names("qodercli"), "mcp__zvec_grep__zvec_grep_rg"]}}
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(content))
            path.with_name("AGENTS.md").write_text("<!-- installed -->\nReleased native guidance\n<!-- /installed -->\n")
        return "ok", {"command": argv, "returncode": 0, "wall_seconds": 1}

    def prepare(self, spec):
        with patch.object(native, "checked_command", side_effect=self.fake_command), patch.object(native, "probe_catalog"):
            return native.prepare(spec)

    def test_real_install_command_model_default_and_released_agent_toolset_are_preserved(self):
        spec = self.spec()
        session, manifest = self.prepare(spec)
        self.assertIn(["zg", "config", "model", "set", "local/potion-code-16m-v2", "--default", "--device", "cpu"], self.calls)
        install = next(command for command in self.calls if command[0:2] == ["zg", "install"])
        self.assertIn("--target", install)
        self.assertNotIn("--agent", install)
        self.assertEqual(install[install.index("--mcp-toolset") + 1], "agent")
        self.assertEqual(manifest["expected_native_tools"], ["zvec_grep_zvec_grep_search"])
        config = json.loads(Path(session["config_path"]).read_text())
        server = config["mcp"]["zvec_grep"]
        self.assertNotIn("includeTools", server)
        self.assertEqual(server["command"][-4:], ["zg", "mcp", "--mcp-toolset", "agent"])
        self.assertEqual(config["instructions"], [manifest["guidance_path"]])
        self.assertEqual(config["permission"]["*"], "deny")
        self.assertNotIn("readonly-search", json.dumps(config))

    def test_baseline_has_no_zg_commands_guidance_or_mcp(self):
        session, manifest = self.prepare(self.spec(arm="baseline"))
        self.assertEqual(self.calls, [["opencode", "--version"]])
        self.assertEqual(manifest["expected_native_tools"], [])
        self.assertIsNone(manifest["mcp_command"])
        self.assertIsNone(manifest["embedding"])
        config = json.loads(Path(session["config_path"]).read_text())
        self.assertNotIn("instructions", config)
        self.assertNotIn("mcp", config)

    def test_qoder_keeps_native_rg_permission_and_installed_guidance(self):
        session, manifest = self.prepare(self.spec(agent="qodercli"))
        config = json.loads(Path(session["config_path"]).read_text())
        self.assertTrue(config["mcpServers"]["zvec_grep"]["trust"])
        self.assertIn("mcp__zvec_grep__zvec_grep_rg", config["permissions"]["allow"])
        self.assertNotIn("includeTools", config["mcpServers"]["zvec_grep"])
        command = session["command"]
        self.assertEqual(command[command.index("--append-system-prompt") + 1], manifest["installed_guidance_text"])
        self.assertNotIn("mcp__zvec_grep__zvec_grep_rg", command[command.index("--allowed-tools") + 1])

    def test_prompt_factors_are_explicit_and_original_artifacts_are_kept(self):
        spec = self.spec(variant="P11")
        spec.update(guidance_override="Use {search_tool}; exact lookup uses {rg_tool}.",
                    description_overrides={"zvec_grep_search": "New search description."})
        session, manifest = self.prepare(spec)
        self.assertIn("zvec_grep_zvec_grep_search", manifest["guidance_text"])
        self.assertIn("Released native guidance", manifest["installed_guidance_text"])
        self.assertNotEqual(manifest["guidance_sha256"], manifest["installed_guidance_sha256"])
        self.assertIn("--descriptions", manifest["mcp_command"])
        self.assertEqual(json.loads((self.root / "logs" / "description-overrides.json").read_text()), spec["description_overrides"])

    def test_unknown_or_mismatched_prompt_changes_are_rejected_before_install(self):
        for update in ({"guidance_override": "extra"}, {"prompt_variant": "P10"},
                       {"prompt_variant": "P01", "description_overrides": {"new_tool": "bad"}},
                       {"arm": "baseline", "prompt_variant": "P11"}):
            spec = self.spec()
            spec.update(update)
            with self.subTest(update=update), self.assertRaises(ValueError):
                native.validate_spec(spec)

    def test_existing_native_configuration_cannot_leak_into_another_trial(self):
        spec = self.spec()
        self.prepare(spec)
        with self.assertRaisesRegex(ValueError, "fresh config"):
            self.prepare(spec)

    def test_credentials_are_not_archived_in_runtime_spec(self):
        spec = self.spec(arm="baseline")
        spec["env"] = {"OPENAI_API_KEY": "secret-test-value"}
        session, _ = self.prepare(spec)
        self.assertEqual(session["env"]["OPENAI_API_KEY"], "secret-test-value")
        self.assertNotIn("secret-test-value", (self.root / "logs" / "native-runtime-spec.json").read_text())

    def test_guidance_verification_uses_first_task_not_title_or_raw_duplicate(self):
        folder = self.root / "wire-requests"
        folder.mkdir()
        title = {"messages": [{"role": "system", "content": "Generate a title."}], "tools": []}
        actual = {"messages": [{"role": "system", "content": [{"type": "text", "text": "Guidance\ncontents"}]}],
                  "tools": [{"function": {"name": "read"}}]}
        (folder / "request-001.json").write_text(json.dumps(title))
        (folder / "request-002.json").write_text(json.dumps(actual))
        (folder / "request-002.raw.json").write_text("not the parsed artifact")
        (self.root / "wire.jsonl").write_text("\n".join(map(json.dumps, [
            {"event": "request", "request_id": 1, "tool_names": []},
            {"event": "response", "request_id": 1},
            {"event": "request", "request_id": 2, "tool_names": ["read"]},
        ])))
        selected = native.first_task_request(self.root)
        self.assertEqual(selected, actual)
        self.assertEqual(native.instruction_texts(selected), ["Guidance\ncontents"])

    def test_replay_mode_prepares_native_install_without_starting_model_session(self):
        spec = self.spec()
        spec["replay_plan"] = str(self.root / "plan.json")
        (self.root / "logs").mkdir()
        with patch.object(native, "prepare", return_value=({"env": {}}, {})), \
             patch.object(native, "checked_command", return_value=("ok", {})) as command, \
             patch.object(native.importlib.util, "spec_from_file_location", side_effect=AssertionError("must not load QA runner")):
            self.assertEqual(native.run(spec), 0)
        invoked = command.call_args.args[0]
        self.assertEqual(invoked[0], "node")
        self.assertTrue(invoked[1].endswith("native-replay.mjs"))
        self.assertEqual(invoked[invoked.index("--plan") + 1], spec["replay_plan"])
        result = json.loads((self.root / "logs" / "preparation.json").read_text())
        self.assertTrue(result["not_an_e2e_sample"])
        self.assertEqual(result["paid_model_requests"], 0)


class NativeMcpTapTests(unittest.TestCase):
    def test_original_frames_and_native_errors_survive_description_only_override(self):
        node = shutil.which("node") or "/Users/cc/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node"
        if not Path(node).exists():
            self.skipTest("Node is required for the native transport contract test")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake = root / "native.mjs"
            fake.write_text('''import readline from "node:readline";
const input = readline.createInterface({input:process.stdin});
input.on("line", line => {
 const message = JSON.parse(line);
 if (message.method === "tools/list") process.stdout.write(JSON.stringify({jsonrpc:"2.0",id:message.id,result:{tools:[
   {name:"zvec_grep_search",description:"original",inputSchema:{type:"object",required:["root"],properties:{root:{type:"string"}}}},
   {name:"zvec_grep_rg",description:"exact",inputSchema:{type:"object"}}]}})+"\\n");
 else process.stdout.write(JSON.stringify({jsonrpc:"2.0",id:message.id,result:{isError:true,content:[{type:"text",text:JSON.stringify(message.params)}]}})+"\\n");
});
''')
            override = root / "override.json"
            override.write_text(json.dumps({"zvec_grep_search": "candidate"}))
            requests = [
                {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "zvec_grep_search", "arguments": {"root": "/app", "query": "A & B", "unknown": 7}}},
            ]
            result = subprocess.run([node, str(SCRIPT.with_name("native-mcp-tap.mjs")), "--log-dir", str(root),
                                     "--descriptions", str(override), "--", node, str(fake)],
                                    input="\n".join(json.dumps(value) for value in requests) + "\n", text=True,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            responses = [json.loads(line) for line in result.stdout.splitlines()]
            tools = responses[0]["result"]["tools"]
            self.assertEqual(tools[0]["description"], "candidate")
            self.assertEqual(tools[1]["description"], "exact")
            self.assertEqual(tools[0]["inputSchema"]["required"], ["root"])
            self.assertTrue(responses[1]["result"]["isError"])
            self.assertEqual(json.loads(responses[1]["result"]["content"][0]["text"]), requests[1]["params"])
            original = json.loads((root / "native-mcp-catalog.json").read_text())
            effective = json.loads((root / "effective-mcp-catalog.json").read_text())
            self.assertEqual(original["tools"][0]["description"], "original")
            self.assertEqual(effective["tools"][0]["description"], "candidate")
            events = [json.loads(line) for line in (root / "native-mcp.jsonl").read_text().splitlines()]
            self.assertEqual([event["message"] for event in events if event["direction"] == "agent_to_zg"], requests)
            self.assertEqual(sum("effective_message" in event for event in events), 1)


if __name__ == "__main__":
    unittest.main()
