"""Boundary tests only; fake transports and saved fixtures make no model calls."""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from types import SimpleNamespace
import unittest

from repair_v2.evidence import EvidenceError, capture_json, download_json
from repair_v2.qa_agents import (INVENTORY_SCRIPT, _RUNTIME_PAYLOAD_SCRIPT,
    IntegrityAuditMixin, snapshot_tree, validate_export)


FIXTURE = Path(__file__).resolve().parents[1] / "fixtures/native-qwen"


class FileTransport:
    """Models merged exec output but independent file downloads."""

    def __init__(self, stdout=b'{"ok":true}', stderr=b"Exporting session: example\n",
                 status=b"0\n", error=None, download_error=None, partial=False):
        self.payload = {"stdout.json": stdout, "stderr.txt": stderr, "status.txt": status}
        self.error = error
        self.download_error = download_error
        self.partial = partial
        self.files = {}
        self.downloads = []
        self.commands = []

    async def exec(self, **kwargs):
        self.commands.append(kwargs)
        remote = re.search(r"/root/qa-evidence-v2-[a-f0-9]+", kwargs["command"])[0]
        self.files.update({remote + "/" + name: value for name, value in self.payload.items()
                           if value is not None})
        if self.error:
            raise self.error
        # Deliberately invalid JSON on transport stdout: helper must ignore it.
        return SimpleNamespace(return_code=0, stdout="compose diagnostic\n{not JSON}", stderr=None)

    async def download_file(self, source, target):
        self.downloads.append(source)
        value = self.files[source]
        if self.download_error and source.endswith(self.download_error):
            if self.partial:
                Path(target).write_bytes(value[:4])
            raise OSError("test download failure")
        Path(target).write_bytes(value)


class EvidenceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)

    async def asyncTearDown(self):
        self.temp.cleanup()

    async def capture(self, environment, label="test"):
        return await capture_json(environment, self.directory, label, "printf '{}'", 2)

    async def test_stderr_and_transport_diagnostics_do_not_pollute_json(self):
        env = FileTransport()
        result = await self.capture(env)
        self.assertEqual(result.data, {"ok": True})
        self.assertEqual(result.path.read_bytes(), env.payload["stdout.json"])
        self.assertEqual((result.path.parent / "stderr.txt").read_bytes(), env.payload["stderr.txt"])
        self.assertEqual(result.metadata["transport"]["stdout"], "compose diagnostic\n{not JSON}")
        self.assertEqual(len(env.downloads), 3)
        self.assertEqual(env.commands[0]["user"], "root")
        self.assertNotIn("/workspace", env.commands[0]["command"])

    async def test_nonzero_status_rejects_valid_json_and_retains_every_file(self):
        env = FileTransport(status=b"3\n")
        with self.assertRaisesRegex(EvidenceError, "command_status_not_zero"):
            await self.capture(env)
        self.assertEqual(len(env.downloads), 3)
        self.assertEqual((self.directory / "test/stdout.json").read_bytes(), env.payload["stdout.json"])

    async def test_malformed_json_retained(self):
        with self.assertRaisesRegex(EvidenceError, "malformed_json"):
            await self.capture(FileTransport(stdout=b'{"partial":'))
        self.assertEqual((self.directory / "test/stdout.json").read_bytes(), b'{"partial":')

    async def test_missing_status_does_not_imply_success(self):
        env = FileTransport(status=None)
        with self.assertRaisesRegex(EvidenceError, "file_capture_incomplete"):
            await self.capture(env)
        self.assertEqual(len(env.downloads), 3)
        manifest = json.loads((self.directory / "test/capture.json").read_text())
        self.assertFalse(manifest["files"]["status.txt"]["complete"])

    async def test_download_failure_keeps_partial_file_and_other_downloads(self):
        env = FileTransport(download_error="stdout.json", partial=True)
        with self.assertRaisesRegex(EvidenceError, "file_capture_incomplete"):
            await self.capture(env)
        self.assertEqual(len(env.downloads), 3)
        self.assertEqual((self.directory / "test/stdout.json").read_bytes(), b'{"ok')
        manifest = json.loads((self.directory / "test/capture.json").read_text())
        self.assertEqual(manifest["files"]["stdout.json"]["bytes"], 4)
        self.assertFalse(manifest["files"]["stdout.json"]["complete"])

    async def test_timeout_preserves_partial_files_even_with_status(self):
        env = FileTransport(error=asyncio.TimeoutError())
        with self.assertRaisesRegex(EvidenceError, "command_transport_failed"):
            await self.capture(env)
        self.assertEqual(len(env.downloads), 3)
        self.assertTrue((self.directory / "test/stderr.txt").is_file())
        self.assertEqual(json.loads((self.directory / "test/transport.json").read_text()),
                         {"error_type": "TimeoutError", "error_message": ""})

    async def test_cancellation_preserves_files_and_propagates(self):
        env = FileTransport(error=asyncio.CancelledError(), status=None)
        with self.assertRaises(asyncio.CancelledError):
            await self.capture(env)
        self.assertEqual(len(env.downloads), 3)
        self.assertTrue((self.directory / "test/capture.json").is_file())

    async def test_existing_attempt_and_unsafe_label_are_rejected(self):
        env = FileTransport()
        await self.capture(env)
        with self.assertRaises(FileExistsError):
            await self.capture(env)
        with self.assertRaises(ValueError):
            await self.capture(env, "../../escape")
        self.assertEqual(len(env.commands), 1)

    async def test_native_file_download_never_calls_exec(self):
        env = FileTransport()
        env.files["/logs/agent/opencode-session-snapshot.json"] = b"[]"
        data = await download_json(env, self.directory, "usage", "/logs/agent/opencode-session-snapshot.json")
        self.assertEqual(data.data, [])
        self.assertEqual(env.commands, [])

    async def test_real_shell_redirects_stderr_and_preserves_nonzero_status(self):
        # Exercise generated shell syntax without requiring local root or Docker.
        outer = self

        class ShellEnvironment:
            async def exec(self, command, **kwargs):
                command = command.replace("/root/qa-evidence-v2-", str(outer.directory / "remote-"))
                process = await asyncio.create_subprocess_exec(
                    "bash", "-c", command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
                stdout, _ = await process.communicate()
                return SimpleNamespace(return_code=process.returncode, stdout=stdout.decode(), stderr=None)

            async def download_file(self, source, target):
                source = source.replace("/root/qa-evidence-v2-", str(outer.directory / "remote-"))
                Path(target).write_bytes(Path(source).read_bytes())

        command = "printf '{\"ok\":true}'; printf 'warning\\n' >&2; exit 7"
        with self.assertRaisesRegex(EvidenceError, "command_status_not_zero"):
            await capture_json(ShellEnvironment(), self.directory, "shell", command, 2)
        self.assertEqual((self.directory / "shell/stdout.json").read_bytes(), b'{"ok":true}')
        self.assertEqual((self.directory / "shell/stderr.txt").read_bytes(), b"warning\n")
        self.assertEqual((self.directory / "shell/status.txt").read_bytes(), b"7\n")
        remote = next(self.directory.glob("remote-*"))
        self.assertEqual(os.stat(remote).st_mode & 0o777, 0o700)
        self.assertEqual(os.stat(remote / "stdout.json").st_mode & 0o777, 0o600)


class IdentityTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = json.loads((FIXTURE / "agent/opencode-session-snapshot.json").read_text())
        self.root, self.rows = snapshot_tree(self.snapshot)
        self.exports = {path.name.removesuffix(".export.json"): json.loads(path.read_text())
                        for path in FIXTURE.glob("*.export.json")}

    def test_native_fixture_exact_tree_and_exports(self):
        self.assertEqual(len(self.rows["session"]), 3)
        for sid, export in self.exports.items():
            validate_export(export, sid, self.rows)

    def test_missing_child_session_rejected(self):
        child = next(sid for sid in self.rows["session"] if sid != self.root)
        without = [row for row in self.snapshot
                   if not (row["kind"] == "session" and json.loads(row["data"])["id"] == child)]
        with self.assertRaises(ValueError):
            snapshot_tree(without)

    def test_export_missing_counted_message_rejected(self):
        export = copy.deepcopy(self.exports[self.root])
        export["messages"] = [m for m in export["messages"] if m["info"]["role"] != "assistant"]
        with self.assertRaisesRegex(ValueError, "counted_message_or_tool"):
            validate_export(export, self.root, self.rows)

    def test_export_parent_identity_rejected(self):
        export = copy.deepcopy(self.exports[self.root])
        export["info"]["parentID"] = "ses_other"
        with self.assertRaisesRegex(ValueError, "session_export_identity"):
            validate_export(export, self.root, self.rows)

    def test_export_tool_state_mismatch_rejected(self):
        export = copy.deepcopy(self.exports[self.root])
        tool = next(p for m in export["messages"] for p in m["parts"] if p["type"] == "tool")
        tool["state"]["status"] = "running"
        with self.assertRaisesRegex(ValueError, "tool_identity_or_state"):
            validate_export(export, self.root, self.rows)

    def test_export_duplicate_part_rejected(self):
        export = copy.deepcopy(self.exports[self.root])
        message = next(m for m in export["messages"] if m["parts"])
        message["parts"].append(copy.deepcopy(message["parts"][0]))
        with self.assertRaisesRegex(ValueError, "part_identity"):
            validate_export(export, self.root, self.rows)


class RuntimePayloadTests(unittest.TestCase):
    def test_inventory_extension_is_valid_python_and_contains_no_credential_paths(self):
        compile(INVENTORY_SCRIPT, "v2-inventory", "exec")
        self.assertNotIn("credentials", _RUNTIME_PAYLOAD_SCRIPT)
        self.assertNotIn(".config", _RUNTIME_PAYLOAD_SCRIPT)

    def test_only_four_installed_payloads_are_hashed_and_change_is_visible(self):
        with tempfile.TemporaryDirectory() as temp:
            package = Path(temp).resolve()
            files = ["bin/zg", "bin/libzvec_c_api.so", "bin/data/jieba_dict/hmm_model.utf8",
                     "bin/data/jieba_dict/jieba.dict.utf8"]
            for name in files:
                path = package / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(name.encode())
            (package / "secret-unselected").write_text("not collected")
            runtime = {"zg": {"binary_path": str(package / "bin/zg")}}
            namespace = {"runtime": runtime, "pathlib": __import__("pathlib"), "hashlib": hashlib}
            exec(_RUNTIME_PAYLOAD_SCRIPT, namespace)
            self.assertEqual(set(runtime["zg"]["payloads"]), set(files))
            before = runtime["zg"]["payloads"]["bin/libzvec_c_api.so"]["sha256"]
            (package / "bin/libzvec_c_api.so").write_bytes(b"changed")
            exec(_RUNTIME_PAYLOAD_SCRIPT, namespace)
            self.assertNotEqual(before, runtime["zg"]["payloads"]["bin/libzvec_c_api.so"]["sha256"])

    def test_installed_payload_symlink_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            package = Path(temp).resolve()
            (package / "bin").mkdir()
            (package / "other").write_bytes(b"payload")
            (package / "bin/zg").symlink_to(package / "other")
            with self.assertRaisesRegex(RuntimeError, "not_regular"):
                exec(_RUNTIME_PAYLOAD_SCRIPT, {"runtime": {"zg": {"binary_path": str(package / "bin/zg")}},
                     "pathlib": __import__("pathlib"), "hashlib": hashlib})


class AdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_child_export_retains_other_sessions_and_invalidates_audit(self):
        snapshot = (FIXTURE / "agent/opencode-session-snapshot.json").read_bytes()
        root, rows = snapshot_tree(json.loads(snapshot))
        child = next(sid for sid in rows["session"] if sid != root)

        class Native:
            def __init__(self, logs_dir):
                self.logs_dir = logs_dir
                self._usage_capture_complete = True

        class Adapter(IntegrityAuditMixin, Native):
            pass

        class SessionTransport(FileTransport):
            async def exec(self, **kwargs):
                session = re.search(r"opencode export ([A-Za-z0-9_-]+)", kwargs["command"])[1]
                self.payload["stdout.json"] = (FIXTURE / (session + ".export.json")).read_bytes()
                self.payload["status.txt"] = b"1\n" if session == child else b"0\n"
                return await super().exec(**kwargs)

        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            (directory / "manifest.json").write_text('{"files":[]}')
            adapter = Adapter(logs_dir=directory / "trial/agent", qa_profile="baseline",
                              qa_source_manifest=str(directory / "manifest.json"))
            env = SessionTransport()
            env.files["/logs/agent/opencode-session-snapshot.json"] = snapshot
            await adapter._export_sessions(env)
            self.assertFalse(adapter._qa_audit["session_exports_complete"])
            self.assertEqual(len(adapter._qa_audit["exported_sessions"]), 2)
            self.assertTrue((directory / "trial/qa-session-exports" / (root + ".json")).is_file())
            self.assertTrue((directory / "trial/qa-evidence-v2" / ("export-" + child) / "stdout.json").is_file())
            self.assertEqual(len(env.commands), 3)


if __name__ == "__main__":
    unittest.main()
