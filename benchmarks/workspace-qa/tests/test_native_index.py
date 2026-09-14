from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import native_index


class NativeIndexTests(unittest.TestCase):
    def fixture(self, root, model=native_index.MODEL):
        source = root / "workspace"
        (source / ".zvec-grep").mkdir(parents=True)
        (source / ".zvec-grep/manifest.json").write_text(json.dumps({"embedding": {
            "provider": "qwen", "model": model, "dimension": 1024}}))
        return source, root / "logs/native-index.json"

    def run_index(self, source, output, *, check_only=False, side_effect=None, credentials=True):
        commands = []

        def execute(command, **kwargs):
            commands.append((command, kwargs))
            if side_effect:
                return side_effect(command, **kwargs)
            return subprocess.CompletedProcess(command, 0, "ready", "")

        args = ["--root", str(source), "--output", str(output)] + (["--check-only"] if check_only else [])
        with patch.dict("os.environ", {"QWEN_API_KEY": "fixture-embedding-secret"} if credentials else {}, clear=True), \
                patch.object(native_index.subprocess, "run", side_effect=execute), redirect_stdout(io.StringIO()):
            status = native_index.main(args)
        return status, json.loads(output.read_text()), commands

    def test_build_runs_released_auth_index_and_readiness_in_order_with_frozen_options(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, output = self.fixture(Path(tmp))
            status, record, commands = self.run_index(source, output)
            self.assertEqual(status, 0)
            self.assertEqual([cmd[1] for cmd, _ in commands], ["auth", "index", "status"])
            self.assertEqual(commands[0][0], ["zg", "auth", "grant", str(source), "--capability", "embedding",
                                            "--scope", "workspace", "--embedding", native_index.MODEL])
            self.assertEqual(commands[1][0], ["zg", "index", str(source), "--mode", "direct", "--embedding",
                                            native_index.MODEL, "--max-filesize", "1048576"])
            self.assertEqual(commands[2][0], ["zg", "status", str(source), "--mode", "direct", "--check-ready"])
            self.assertEqual(commands[1][1]["timeout"], 1750)
            self.assertEqual(record["method"], "released_zg_cli")
            self.assertEqual(record["protocol"], "workspace-qa-qoder-native-install-v3")
            self.assertEqual(record["status"], "completed")
            self.assertIn("index_manifest_sha256", record)
            self.assertNotIn("fixture-embedding-secret", output.read_text())

    def test_check_only_validates_existing_native_seed_without_reindexing(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, output = self.fixture(Path(tmp))

            def grant(command, **kwargs):
                if command[1] == "auth":
                    (source / ".zvec-grep/grants.json").write_text('{"embedding": true}')
                return subprocess.CompletedProcess(command, 0, "ready", "")

            status, record, commands = self.run_index(source, output, check_only=True, side_effect=grant)
            self.assertEqual(status, 0)
            self.assertTrue(record["check_only"])
            self.assertEqual([cmd[1] for cmd, _ in commands], ["auth", "status"])
            self.assertTrue((source / ".zvec-grep/grants.json").is_file())

    def test_nonzero_index_stops_before_readiness_and_retains_redacted_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, output = self.fixture(Path(tmp))

            def fail(command, **kwargs):
                return subprocess.CompletedProcess(command, 7 if command[1] == "index" else 0,
                    "partial fixture-embedding-secret", "error fixture-embedding-secret")

            status, record, commands = self.run_index(source, output, side_effect=fail)
            self.assertEqual(status, 1)
            self.assertEqual([cmd[1] for cmd, _ in commands], ["auth", "index"])
            self.assertEqual(record["commands"][-1]["returncode"], 7)
            self.assertEqual((output.parent / "native-index-2-index.stdout.txt").read_text(), "partial [REDACTED]")
            self.assertEqual((output.parent / "native-index-2-index.stderr.txt").read_text(), "error [REDACTED]")
            self.assertEqual(record["status"], "failed")

    def test_readiness_failure_is_not_reported_as_a_completed_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, output = self.fixture(Path(tmp))
            status, record, commands = self.run_index(source, output,
                side_effect=lambda command, **kwargs: subprocess.CompletedProcess(command, int(command[1] == "status"), "", "stale"))
            self.assertEqual(status, 1)
            self.assertEqual(len(commands), 3)
            self.assertIn("status exited 1", record["error"])

    def test_wrong_model_or_missing_manifest_cannot_pass(self):
        for model in ("local/potion-retrieval-32m", None):
            with self.subTest(model=model), tempfile.TemporaryDirectory() as tmp:
                source, output = self.fixture(Path(tmp), model)
                if model is None:
                    (source / ".zvec-grep/manifest.json").unlink()
                status, record, _ = self.run_index(source, output)
                self.assertEqual(status, 1)
                self.assertEqual(record["status"], "failed")

    def test_missing_embedding_key_fails_before_any_cli_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, output = self.fixture(Path(tmp))
            status, record, commands = self.run_index(source, output, credentials=False)
            self.assertEqual(status, 1)
            self.assertEqual(commands, [])
            self.assertIn("QWEN_API_KEY", record["error"])

    def test_provider_dimension_and_persisted_credentials_are_validated(self):
        variants = [
            {"embedding": {"provider": "local", "model": native_index.MODEL, "dimension": 1024}},
            {"embedding": {"provider": "qwen", "model": native_index.MODEL, "dimension": 768}},
            {"embedding": {"provider": "qwen", "model": native_index.MODEL, "dimension": 1024},
             "embeddingRuntime": {"apiKey": "fixture-embedding-secret"}},
        ]
        for value in variants:
            with self.subTest(manifest=value), tempfile.TemporaryDirectory() as tmp:
                source, output = self.fixture(Path(tmp))
                (source / ".zvec-grep/manifest.json").write_text(json.dumps(value))
                status, record, _ = self.run_index(source, output)
                self.assertEqual(status, 1)
                self.assertEqual(record["status"], "failed")
                self.assertNotIn("fixture-embedding-secret", output.read_text())

    def test_timeout_is_retained_and_never_retries_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, output = self.fixture(Path(tmp))

            def timed_out(command, **kwargs):
                if command[1] == "index":
                    raise subprocess.TimeoutExpired(command, 1750, output="partial")
                return subprocess.CompletedProcess(command, 0, "granted", "")

            status, record, commands = self.run_index(source, output, side_effect=timed_out)
            self.assertEqual(status, 1)
            self.assertEqual(record["error_type"], "TimeoutExpired")
            self.assertEqual([cmd[1] for cmd, _ in commands], ["auth", "index"])


if __name__ == "__main__":
    unittest.main()
