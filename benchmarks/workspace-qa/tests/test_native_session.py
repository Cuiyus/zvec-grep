from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

MODULE = Path(__file__).resolve().parents[1] / "native_session.py"
spec = importlib.util.spec_from_file_location("workspace_qa_native_session", MODULE)
native = importlib.util.module_from_spec(spec)
spec.loader.exec_module(native)

# Routing text itself is pinned to the published installer hash in production;
# this fixture substitutes a hash only to exercise artifact validation offline.
GUIDANCE_FIXTURE = b"<!-- ZVEC_GREP_START -->\nstandard installed guidance\n<!-- ZVEC_GREP_END -->\n"


class NativeSessionTests(unittest.TestCase):
    def test_native_output_cap_is_optional_validated_and_before_prompt_separator(self):
        with tempfile.TemporaryDirectory() as temp:
            _, _, logs, _, value = self.fixture(temp)
            self.assertNotIn("--max-output-tokens", native.session_spec(value, logs)["command"])
            for profile in ("baseline", "with-zg"):
                value.update(profile=profile, max_output_tokens=16384)
                command = native.session_spec(value, logs)["command"]
                self.assertEqual(command[command.index("--max-output-tokens") + 1], "16384")
                self.assertLess(command.index("--max-output-tokens"), command.index("--"))
                self.assertEqual(command[-1], value["prompt"])
            for invalid in (True, 0, -1, 32769, "16384", 16384.0):
                value["max_output_tokens"] = invalid
                with self.assertRaises(ValueError):
                    native.session_spec(value, logs)

    def test_native_request_retry_setting_is_explicit_and_validated(self):
        with tempfile.TemporaryDirectory() as temp:
            _, _, logs, _, value = self.fixture(temp)
            for retries in (0, 2):
                value["model_request_retries"] = retries
                command = native.session_spec(value, logs)["command"]
                self.assertEqual(command[command.index("--max-model-request-retries") + 1], str(retries))
            for invalid in (True, -1, 4, "2"):
                value["model_request_retries"] = invalid
                with self.assertRaises(ValueError):
                    native.session_spec(value, logs)

    def test_both_pinned_models_use_the_requested_cli_model_without_sampling_flags(self):
        with tempfile.TemporaryDirectory() as temp:
            _, _, logs, _, value = self.fixture(temp)
            for model in ("GLM-5.2", "Qwen3.8-Max"):
                value["model"] = model
                command = native.session_spec(value, logs)["command"]
                self.assertEqual(command[command.index("--model") + 1], model)
                self.assertNotIn("--temperature", command)
                self.assertNotIn("--seed", command)
            value["model"] = "auto"
            with self.assertRaises(ValueError):
                native.session_spec(value, logs)

    def fixture(self, temp, profile="with-zg"):
        base = Path(temp)
        home, root, logs = base / "home", base / "app", base / "logs"
        home.mkdir(); root.mkdir(); logs.mkdir()
        (root / ".zvec-grep").mkdir()
        env = {"HOME": str(home), "PATH": "/bin:/usr/bin", "QODER_PERSONAL_ACCESS_TOKEN": "qoder-secret-token",
               "QWEN_API_KEY": "embedding-secret-token", "ZVEC_GREP_ENDPOINT": "https://proxy.example/v1/embeddings"}
        value = {"protocol": native.PROTOCOL, "profile": profile, "root": str(root),
                 "prompt": "请阅读原始资料，输出报告。", "model": native.MODEL, "embedding_model": native.EMBEDDING_MODEL,
                 "limits": {"model_requests": 30, "tool_calls": 80, "input_tokens": 1000000, "wall_seconds": 900}}
        return home, root, logs, env, value

    def fake_runtime(self, home, root, logs, calls, *, fail_phase=None, mutate=False, credential_in_config=False,
                     settings_change=None):
        def execute(command, **kwargs):
            calls.append((command, kwargs))
            stdout, stderr, returncode = "", "", 0
            if command == ["qodercli", "--version"]:
                stdout = native.QODER_VERSION
            elif command == ["zg", "--version"]:
                stdout = native.ZG_VERSION
            elif command == native.INSTALL_COMMAND:
                qoder = home / ".qoder"
                qoder.mkdir()
                server = {"command": "zg", "args": ["server", "--stdio"], "timeout": 600000, "trust": True,
                          "description": "Managed by zg install; managed permissions=zvec_grep_search,zvec_grep_rg",
                          "alwaysAllow": ["zvec_grep_search", "zvec_grep_rg"]}
                settings = {"mcpServers": {"zvec_grep": server}, "permissions": {"allow": [
                    native.QODER_SEARCH_TOOL, "mcp__zvec_grep__zvec_grep_rg"]}}
                if credential_in_config:
                    server["env"] = {"QWEN_API_KEY": "embedding-secret-token"}
                (qoder / "settings.json").write_text(json.dumps(settings))
                (qoder / "AGENTS.md").write_bytes(GUIDANCE_FIXTURE)
                (qoder / "mcp.json").write_text(json.dumps({"mcpServers": {"zvec_grep": {
                    "command": "/usr/bin/node", "args": ["/opt/qa/node_modules/.bin/zg", "server", "--stdio"],
                    "timeout": 600000, "description": "Managed by zg install"}}}))
                stdout = "installed; embedding-secret-token qoder-secret-token"
            elif command[:3] == ["zg", "auth", "grant"]:
                (root / ".zvec-grep/authorization.json").write_text('{"grants":["signed-native-grant"]}')
                (home / ".zvec-grep").mkdir(exist_ok=True)
                (home / ".zvec-grep/authorization-signing.key").write_text("PRIVATE-SIGNING-KEY-NEVER-EXPORT")
            elif command == native.READY_COMMAND:
                stdout = "Server: ready\nPID: 123\nURL: http://127.0.0.1:7999/mcp\nMCP toolset: agent"
            elif len(command) > 1 and command[1] == "/opt/qa/qa-session.py":
                session = json.loads((logs / "session-spec.json").read_text())
                self.assertEqual(session["command"][-1], "请阅读原始资料，输出报告。")
                self.assertFalse(any(secret in json.dumps(session) for secret in ("embedding-secret-token", "qoder-secret-token")))
                (logs / "session.json").write_text('{"status":"completed","wall_seconds":3.5}')
                if mutate:
                    with (home / ".qoder/AGENTS.md").open("a") as out:
                        out.write("changed by agent")
                if settings_change:
                    path = home / ".qoder/settings.json"
                    data = json.loads(path.read_text())
                    settings_change(data)
                    path.write_text(json.dumps(data, indent=2))
            elif command != ["zg", "server", "off"]:
                self.fail(f"unexpected command {command}")
            if fail_phase and command[:len(fail_phase)] == fail_phase:
                returncode, stderr = 7, "failed with embedding-secret-token"
            return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)
        return execute

    def run_fake(self, home, root, logs, env, value, **kwargs):
        calls = []
        with patch.object(native.subprocess, "run", side_effect=self.fake_runtime(home, root, logs, calls, **kwargs)), \
                patch.object(native, "STANDARD_GUIDANCE_SHA256", hashlib.sha256(GUIDANCE_FIXTURE).hexdigest()), \
                redirect_stdout(io.StringIO()):
            result = native.run(value, log_dir=logs, environment=env)
            if result == 0:
                native.validate_installation(logs, profile=value["profile"])
        return result, calls, json.loads((logs / "install-manifest.json").read_text())

    def test_native_install_grant_ready_before_agent_with_unmodified_files(self):
        with tempfile.TemporaryDirectory() as temp:
            home, root, logs, env, value = self.fixture(temp)
            result, calls, manifest = self.run_fake(home, root, logs, env, value)
            self.assertEqual(result, 0)
            commands = [item[0] for item in calls]
            self.assertEqual(commands[2:5], [native.INSTALL_COMMAND, native.auth_command(str(root)), native.READY_COMMAND])
            self.assertEqual(commands[-1], ["zg", "server", "off"])
            self.assertEqual(manifest["status"], "completed")
            self.assertTrue(manifest["files_unchanged_after_session"])
            self.assertGreaterEqual(manifest["setup_wall_seconds"], 0)
            self.assertEqual(json.loads((logs / "session.json").read_text())["wall_seconds"], 3.5)
            for name in native.INSTALL_FILES:
                self.assertEqual((home / ".qoder" / name).read_bytes(), (logs / "installation" / name).read_bytes())
            for _, options in calls:
                self.assertEqual(options["env"]["QWEN_API_KEY"], env["QWEN_API_KEY"])
            all_evidence = "\n".join(p.read_text() for p in logs.rglob("*") if p.is_file())
            for secret in ("embedding-secret-token", "qoder-secret-token", "PRIVATE-SIGNING-KEY-NEVER-EXPORT"):
                self.assertNotIn(secret, all_evidence)
                self.assertNotIn(secret, repr(commands))
            self.assertIn("[REDACTED]", all_evidence)
            self.assertFalse((logs / "authorization-signing.key").exists())

    def test_baseline_has_no_install_daemon_or_embedding_credentials(self):
        with tempfile.TemporaryDirectory() as temp:
            home, root, logs, env, value = self.fixture(temp, "baseline")
            env["ZVEC_GREP_API_KEY"] = "alternative-secret"
            result, calls, manifest = self.run_fake(home, root, logs, env, value)
            self.assertEqual(result, 0)
            self.assertEqual(len(calls), 2)
            self.assertFalse(any(command[0] == "zg" for command, _ in calls))
            self.assertFalse((home / ".qoder").exists())
            self.assertFalse(manifest["standard_install"])
            self.assertEqual(manifest["files"], {})
            for _, options in calls:
                self.assertTrue(all(name not in options["env"] for name in native.EMBEDDING_ENV_NAMES))
                self.assertEqual(options["env"]["QODER_PERSONAL_ACCESS_TOKEN"], env["QODER_PERSONAL_ACCESS_TOKEN"])

    def test_qoder_command_loads_standard_settings_and_guidance(self):
        with tempfile.TemporaryDirectory() as temp:
            _, _, logs, _, value = self.fixture(temp)
            session = native.session_spec(value, logs)
            command = session["command"]
            for blocked in ("--setting-sources", "--settings", "--mcp-config", "--strict-mcp-config", "--config-dir", "--disable-builtin-skills"):
                self.assertNotIn(blocked, command)
            self.assertNotIn("QODER_CONFIG_DIR", session["env"])
            self.assertEqual(command[command.index("--tools") + 1], "Read,Grep,Glob")
            self.assertEqual(command[command.index("--model") + 1], native.MODEL)
            with_zg = command[command.index("--allowed-tools") + 1]
            value["profile"] = "baseline"
            baseline = native.session_spec(value, logs)["command"]
            self.assertEqual(with_zg, baseline[baseline.index("--allowed-tools") + 1] + "," + native.QODER_SEARCH_TOOL)
            self.assertEqual(command[-1], baseline[-1])

    def test_install_failure_preserves_diagnostics_and_never_runs_agent(self):
        with tempfile.TemporaryDirectory() as temp:
            values = self.fixture(temp)
            result, calls, manifest = self.run_fake(*values, fail_phase=["zg", "auth", "grant"])
            self.assertEqual(result, 2)
            self.assertFalse(manifest["qa_session_launched"])
            self.assertEqual(manifest["status"], "failed")
            self.assertFalse(any(len(command) > 1 and command[1] == "/opt/qa/qa-session.py" for command, _ in calls))
            self.assertEqual(calls[-1][0], ["zg", "server", "off"])
            self.assertIn("[REDACTED]", (values[2] / "installation/grant.stderr.txt").read_text())

    def test_credentials_in_install_output_config_are_not_copied(self):
        with tempfile.TemporaryDirectory() as temp:
            values = self.fixture(temp)
            result, _, manifest = self.run_fake(*values, credential_in_config=True)
            self.assertEqual(result, 2)
            self.assertFalse(manifest["qa_session_launched"])
            self.assertFalse((values[2] / "installation/settings.json").exists())

    def test_changed_standard_guidance_is_invalid_even_if_qa_completed(self):
        with tempfile.TemporaryDirectory() as temp:
            values = self.fixture(temp)
            result, _, manifest = self.run_fake(*values, mutate=True)
            self.assertEqual(result, 2)
            self.assertEqual(manifest["qa_session_returncode"], 0)
            self.assertFalse(manifest["files_unchanged_after_session"])
            self.assertIn("AGENTS.md:$", manifest["error"]["message"])
            self.assertTrue((values[2] / "installation/after/AGENTS.md").is_file())

    def test_observed_native_security_defaults_preserve_managed_installation(self):
        with tempfile.TemporaryDirectory() as temp:
            values = self.fixture(temp)
            result, _, manifest = self.run_fake(*values, settings_change=lambda data: data.update(
                securityScan={"l1StaticCheck": True, "l2LightweightScan": True, "l3DeepScan": True}))
            self.assertEqual(result, 0)
            self.assertEqual(manifest["schema_version"], 2)
            self.assertFalse(manifest["files_unchanged_after_session"])
            comparison = manifest["installation_comparison"]
            self.assertTrue(comparison["valid"])
            self.assertTrue(comparison["managed_contents_unchanged"])
            self.assertEqual(comparison["unexpected_changes"], [])
            self.assertEqual(comparison["allowed_changes"], [{"file": "settings.json", "field": "$.securityScan",
                              "reason": "qoder_1.1.45_native_security_scan_defaults"}])
            logs = values[2]
            before = json.loads((logs / "installation/settings.json").read_text())
            after = json.loads((logs / "installation/after/settings.json").read_text())
            self.assertNotIn("securityScan", before)
            self.assertEqual(after.pop("securityScan"), native.QODER_SECURITY_SCAN_DEFAULTS)
            self.assertEqual(before, after)
            self.assertEqual(set(manifest["after_files"]), set(native.INSTALL_FILES))

    def test_unknown_settings_or_nondefault_security_changes_remain_failures(self):
        changes = [
            (lambda data: data.update(hooks={"SessionStart": ["unexpected command"]}), "settings.json:$.hooks"),
            (lambda data: data["mcpServers"]["zvec_grep"].update(args=["server", "--http"]),
             "settings.json:$.mcpServers.zvec_grep.args"),
            (lambda data: data.update(securityScan={"l1StaticCheck": False, "l2LightweightScan": True, "l3DeepScan": True}),
             "settings.json:$.securityScan"),
            (lambda data: data.update(securityScan={"l1StaticCheck": 1, "l2LightweightScan": True, "l3DeepScan": True}),
             "settings.json:$.securityScan"),
            (lambda data: data.pop("permissions"), "settings.json:$.permissions"),
        ]
        for change, expected in changes:
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as temp:
                values = self.fixture(temp)
                result, _, manifest = self.run_fake(*values, settings_change=change)
                self.assertEqual(result, 2)
                self.assertFalse(manifest["installation_comparison"]["valid"])
                self.assertIn(expected, manifest["error"]["message"])
                self.assertTrue((values[2] / "installation/after/settings.json").is_file())
                with self.assertRaisesRegex(ValueError, re.escape(expected)):
                    native.validate_installation(values[2])

    def test_final_validation_requires_after_files_and_recomputes_comparison(self):
        with tempfile.TemporaryDirectory() as temp:
            values = self.fixture(temp)
            _, _, manifest = self.run_fake(*values)
            logs = values[2]
            with patch.object(native, "STANDARD_GUIDANCE_SHA256", hashlib.sha256(GUIDANCE_FIXTURE).hexdigest()):
                for key in ("after_files", "qa_session_returncode", "installation_comparison"):
                    changed = dict(manifest)
                    changed.pop(key)
                    (logs / "install-manifest.json").write_text(json.dumps(changed))
                    with self.assertRaises(ValueError):
                        native.validate_installation(logs)
                    self.assertTrue(native.validate_installation(logs, stage="setup")["valid"])
                (logs / "install-manifest.json").write_text(json.dumps(manifest))
                path = logs / "installation/after/settings.json"
                path.write_text('{"mcpServers":{}}')
                with self.assertRaisesRegex(ValueError, "after-session.*hash/path"):
                    native.validate_installation(logs)
                manifest["after_files"]["settings.json"]["sha256"] = native.sha256(path)
                (logs / "install-manifest.json").write_text(json.dumps(manifest))
                with self.assertRaisesRegex(ValueError, "unapproved.*settings.json"):
                    native.validate_installation(logs)

    def test_evidence_validation_never_executes_artifact_commands_and_rejects_tampering(self):
        with tempfile.TemporaryDirectory() as temp:
            values = self.fixture(temp)
            _, _, manifest = self.run_fake(*values)
            logs = values[2]
            with patch.object(native.subprocess, "run") as execute, \
                    patch.object(native, "STANDARD_GUIDANCE_SHA256", hashlib.sha256(GUIDANCE_FIXTURE).hexdigest()):
                self.assertTrue(native.validate_installation(logs)["valid"])
                execute.assert_not_called()
                (logs / "installation/AGENTS.md").write_text("custom prompting")
                with self.assertRaisesRegex(ValueError, "hash/path"):
                    native.validate_installation(logs)
                manifest["files"]["AGENTS.md"]["sha256"] = native.sha256(logs / "installation/AGENTS.md")
                (logs / "install-manifest.json").write_text(json.dumps(manifest))
                with self.assertRaisesRegex(ValueError, "published"):
                    native.validate_installation(logs)
                manifest["files"]["AGENTS.md"]["path"] = "../../secret"
                (logs / "install-manifest.json").write_text(json.dumps(manifest))
                with self.assertRaisesRegex(ValueError, "hash/path"):
                    native.validate_installation(logs)
                execute.assert_not_called()

    def test_runtime_rejects_hidden_overrides_missing_auth_and_preexisting_install(self):
        with tempfile.TemporaryDirectory() as temp:
            home, root, logs, env, value = self.fixture(temp)
            for key in native.CONFIG_OVERRIDE_NAMES:
                with self.assertRaisesRegex(ValueError, "overrides"):
                    native.runtime_environment(value, {**env, key: "unexpected"})
            with self.assertRaisesRegex(ValueError, "QWEN_API_KEY"):
                native.runtime_environment(value, {k: v for k, v in env.items() if k != "QWEN_API_KEY"})
            for endpoint in ("", "file:///tmp/key", "https://key@example.com/api", "https://example.com/?key=x"):
                with self.assertRaisesRegex(ValueError, "endpoint"):
                    native.runtime_environment(value, {**env, "ZVEC_GREP_ENDPOINT": endpoint})
            (home / ".qoder").mkdir(); (home / ".qoder/AGENTS.md").write_text("stale guidance")
            with patch.object(native.subprocess, "run") as execute, redirect_stdout(io.StringIO()):
                self.assertEqual(native.run(value, log_dir=logs, environment=env), 2)
                execute.assert_not_called()

    def test_timeout_log_is_scrubbed_and_no_model_call_follows(self):
        with tempfile.TemporaryDirectory() as temp:
            home, root, logs, env, value = self.fixture(temp)
            with patch.object(native.subprocess, "run", side_effect=subprocess.TimeoutExpired(
                    ["qodercli", "--version"], 120, output=b"embedding-secret-token", stderr=b"qoder-secret-token")), \
                    redirect_stdout(io.StringIO()):
                self.assertEqual(native.run(value, log_dir=logs, environment=env), 2)
            self.assertEqual((logs / "installation/qoder-version.stdout.txt").read_text(), "[REDACTED]")
            self.assertEqual((logs / "installation/qoder-version.stderr.txt").read_text(), "[REDACTED]")
            manifest = json.loads((logs / "install-manifest.json").read_text())
            self.assertEqual(manifest["commands"][0]["returncode"], None)
            self.assertFalse(manifest["qa_session_launched"])

    def test_spec_cannot_replace_model_or_settings_and_counts_are_integral(self):
        with tempfile.TemporaryDirectory() as temp:
            *_, value = self.fixture(temp)
            for replacement in ({"model": "another-model"}, {"embedding_model": "local/potion-base-2M"},
                                {"env": {"QODER_CONFIG_DIR": "/tmp/custom"}}, {"command": ["echo", "unsafe"]}):
                with self.assertRaises(ValueError):
                    native.validate_spec({**value, **replacement})
            for invalid in (0, -1, True, 1.5, float("inf")):
                with self.assertRaises(ValueError):
                    native.validate_spec({**value, "limits": {**value["limits"], "model_requests": invalid}})

    @unittest.skipUnless(os.environ.get("ZG_QA_QODER_TEST_BUNDLE"), "published Qoder bundle path not configured")
    def test_published_qoder_user_memory_discovery_loads_default_installer_agents(self):
        """Run the published discovery functions, without executing CLI startup.

        This establishes native file discovery, not a claim about a model's
        adherence. Provider calls and Qoder's CLI entrypoint are never run.
        When CI supplies the pinned bundle, missing/changed APIs fail the test.
        """
        bundle = Path(os.environ["ZG_QA_QODER_TEST_BUNDLE"]).read_text()
        self.assertIn('oPA="1.1.45"', bundle)
        self.assertIn('DiA=WD="AGENTS.md"', bundle)
        self.assertIn('allowedAgentSources:t.settingSources?_oo(t.settingSources):void 0', bundle)
        self.assertIn('this.globalMemory=t.global||""', bundle)
        self.assertIn('getUserContext(),t=A.shouldSuppressNativeAutoMemoryForSdk', bundle)
        self.assertIn('m=PrA(this.config.config)', bundle)

        def between(start, end):
            first = bundle.index(start)
            return bundle[first:bundle.index(end, first + len(start))]

        source = "\n".join((between("function vl(){", "function x0("),
                              between("function Gq(){", "function Yq("),
                              between("async function Kcn(){", "async function Zcn(")))
        discover = between("async discoverMemoryPaths(){", "async loadMemoryContents(")
        self.assertTrue(discover.endswith("}"))
        node = shutil.which("node")
        self.assertIsNotNone(node, "Node is required for the pinned bundle discovery test")
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            qoder = home / ".qoder"
            qoder.mkdir()
            (qoder / "AGENTS.md").write_bytes(GUIDANCE_FIXTURE)
            payload = {"home": str(home), "source": source, "discover": discover}
            program = r'''
const fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
const input = JSON.parse(fs.readFileSync(0, "utf8"));
const context = {
  Hu: path, zu: path, FDt: {homedir: () => input.home}, rDe: undefined,
  ePA: () => undefined, _a: () => undefined, g7A: "QODER_CONFIG_DIR",
  d7A: "QODER_CLI_HOME", u7A: "GEMINI_CLI_HOME", Vp: ".qoder", DiA: "AGENTS.md",
  jE: fs.promises, aoA: fs, ja: x => x, C: {debug: () => {}},
  zcn: async () => [], kre: () => path.join(input.home, ".qoder/rules"),
  Zcn: async () => ({project: [], local: []})
};
vm.createContext(context);
vm.runInContext(input.source + "\nthis.discovery = ({" + input.discover + "});", context);
context.discovery.config = {getAllowedAgentSources: () => undefined, isTrustedFolder: () => false};
(async () => {
  const normal = await context.discovery.discoverMemoryPaths();
  context.discovery.config.getAllowedAgentSources = () => [];
  const blocked = await context.discovery.discoverMemoryPaths();
  process.stdout.write(JSON.stringify({normal, blocked, content: fs.readFileSync(normal.global[0], "utf8")}));
})().catch(error => { process.stderr.write(String(error)); process.exitCode = 1; });
'''
            completed = subprocess.run([node, "-e", program], input=json.dumps(payload), text=True,
                                       capture_output=True, check=True, timeout=15)
            observed = json.loads(completed.stdout)
            self.assertEqual(observed["normal"]["global"], [str(qoder / "AGENTS.md")])
            self.assertEqual(observed["content"], GUIDANCE_FIXTURE.decode())
            self.assertEqual(observed["blocked"]["global"], [])

    @unittest.skipUnless(os.environ.get("ZG_QA_QODER_TEST_BUNDLE"), "published Qoder bundle path not configured")
    def test_published_qoder_startup_writes_only_observed_security_defaults(self):
        bundle = Path(os.environ["ZG_QA_QODER_TEST_BUNDLE"]).read_text()
        self.assertIn('!l&&!eu()&&EFn(i.loadedSettings)', bundle)
        self.assertIn('oPA="1.1.45"', bundle)
        first = bundle.index("function pFn(")
        helper = bundle[first:bundle.index("function A0e(", first)]
        first = bundle.index("function EFn(")
        migration = bundle[first:bundle.index("function mFn(", first)]
        constants = re.search(r'foo=(\["l1StaticCheck"[^;]+?),zFl=', bundle)
        self.assertIsNotNone(constants)
        source = "var foo=" + constants[1] + ";\n" + helper + migration
        node = shutil.which("node")
        self.assertIsNotNone(node)
        program = r'''
const fs = require("node:fs"), vm = require("node:vm");
const input = JSON.parse(fs.readFileSync(0, "utf8"));
const context = {settings: {mcpServers: {zvec_grep: {command: "zg", args: ["server", "--stdio"]}}}, writes: []};
context.loaded = {user: {settings: context.settings}, setValue(scope, field, value) {
  context.writes.push({scope, field, value}); context.settings[field] = value;
}};
vm.createContext(context);
vm.runInContext(input.source + "\nEFn(loaded); EFn(loaded);", context);
process.stdout.write(JSON.stringify({settings: context.settings, writes: context.writes}));
'''
        completed = subprocess.run([node, "-e", program], input=json.dumps({"source": source}),
                                   text=True, capture_output=True, check=True, timeout=15)
        observed = json.loads(completed.stdout)
        self.assertEqual(observed["writes"], [{"scope": "User", "field": "securityScan",
                          "value": native.QODER_SECURITY_SCAN_DEFAULTS}])
        self.assertEqual(set(observed["settings"]), {"mcpServers", "securityScan"})


if __name__ == "__main__":
    unittest.main()
