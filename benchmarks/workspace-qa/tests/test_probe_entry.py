import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import run_probe


class ProbeEntryTests(unittest.TestCase):
    credentials = {"QODER_PERSONAL_ACCESS_TOKEN": "fixture-qoder-secret",
                   "QWEN_API_KEY": "fixture-embedding-secret"}

    def invoke(self, root):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            status = run_probe.main(["--output", str(root)])
        return status, json.loads((root / "result.json").read_text()), output.getvalue()

    def test_runs_only_ordered_preflights_without_judge_key_or_benchmark_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict("os.environ", self.credentials, clear=True):
            root = Path(tmp) / "probe"
            calls = []

            def embedding(path):
                calls.append(("embedding", path))
                path.write_text(json.dumps({"resolved_model": "qwen3.7-text-embedding"}))

            def sdk(path):
                calls.append(("sdk", path))
                (path / "qoder").mkdir(parents=True)
                (path / "qoder/result.json").write_text(json.dumps({
                    "model_identity": {"valid": True, "observed_models": [run_probe.runner.MODEL]}}))

            with patch.object(run_probe.run_task, "embedding_preflight", side_effect=embedding), \
                    patch.object(run_probe.run_task, "sdk_preflight", side_effect=sdk), \
                    patch.object(run_probe.runner, "make_plan", side_effect=AssertionError("No QA plan")), \
                    patch.object(run_probe.runner, "collect_results", side_effect=AssertionError("No QA ledger")), \
                    patch("subprocess.run", side_effect=AssertionError("No dataset or judge subprocess")), \
                    patch("urllib.request.urlopen", side_effect=AssertionError("No dataset download")):
                status, result, _ = self.invoke(root)
            self.assertEqual(status, 0)
            self.assertEqual(calls, [("embedding", root.resolve() / "embedding-preflight.json"),
                                    ("sdk", root.resolve() / "sdk-preflight")])
            self.assertEqual(result["phase"], "setup_only")
            self.assertEqual(result["protocol"], "workspace-qa-qoder-native-install-v3")
            self.assertEqual(result["status"], "completed")
            self.assertFalse(result["included_in_benchmark"])
            self.assertEqual(result["model"], run_probe.runner.MODEL)
            self.assertEqual(result["embedding_model"], "qwen/qwen3.7-text-embedding")
            self.assertEqual(result["resolved_embedding_model"], "qwen3.7-text-embedding")
            self.assertTrue(result["model_identity"]["valid"])
            self.assertGreaterEqual(result["wall_seconds"], 0)
            self.assertEqual({p.name for p in root.iterdir()},
                             {"result.json", "embedding-preflight.json", "sdk-preflight"})

    def test_embedding_failure_skips_sdk_and_retains_diagnostic(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict("os.environ", self.credentials, clear=True):
            root = Path(tmp) / "probe"

            def fail(path):
                path.write_text('{"status":"failed","http_status":403}')
                raise RuntimeError("Embedding rejected")

            with patch.object(run_probe.run_task, "embedding_preflight", side_effect=fail), \
                    patch.object(run_probe.run_task, "sdk_preflight") as sdk:
                status, result, _ = self.invoke(root)
            sdk.assert_not_called()
            self.assertEqual(status, 1)
            self.assertEqual(result["error_type"], "RuntimeError")
            self.assertEqual(result["error"], "Embedding rejected")
            self.assertEqual(json.loads((root / "embedding-preflight.json").read_text())["http_status"], 403)
            self.assertFalse((root / "sdk-preflight").exists())

    def test_sdk_failure_retains_nested_diagnostics_and_redacts_error(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict("os.environ", self.credentials, clear=True):
            root = Path(tmp) / "probe"

            def fail(path):
                (path / "qoder/agent").mkdir(parents=True)
                (path / "qoder/result.json").write_text('{"status":"failed"}')
                (path / "qoder/agent/launcher-failure.json").write_text('{"exit_code":1}')
                raise RuntimeError("Rejected fixture-qoder-secret and fixture-embedding-secret")

            with patch.object(run_probe.run_task, "embedding_preflight"), \
                    patch.object(run_probe.run_task, "sdk_preflight", side_effect=fail):
                status, result, stdout = self.invoke(root)
            self.assertEqual(status, 1)
            self.assertEqual(result["status"], "failed")
            for secret in self.credentials.values():
                self.assertNotIn(secret, (root / "result.json").read_text())
                self.assertNotIn(secret, stdout)
            self.assertTrue((root / "sdk-preflight/qoder/agent/launcher-failure.json").is_file())

    def test_each_missing_key_fails_before_preflight_and_writes_result(self):
        for missing in self.credentials:
            with self.subTest(missing=missing), tempfile.TemporaryDirectory() as tmp, \
                    patch.dict("os.environ", {k: v for k, v in self.credentials.items() if k != missing}, clear=True), \
                    patch.object(run_probe.run_task, "embedding_preflight") as embedding, \
                    patch.object(run_probe.run_task, "sdk_preflight") as sdk:
                root = Path(tmp) / "probe"
                status, result, _ = self.invoke(root)
                embedding.assert_not_called()
                sdk.assert_not_called()
                self.assertEqual(status, 1)
                self.assertIn(missing, result["error"])
                self.assertEqual([p.name for p in root.iterdir()], ["result.json"])

    def test_incomplete_diagnostic_cannot_mask_original_failure(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict("os.environ", self.credentials, clear=True):
            root = Path(tmp) / "probe"

            def fail(path):
                path.write_text('{')
                raise RuntimeError("Original failure")

            with patch.object(run_probe.run_task, "embedding_preflight", side_effect=fail):
                status, result, _ = self.invoke(root)
            self.assertEqual(status, 1)
            self.assertEqual(result["error"], "Original failure")


if __name__ == "__main__":
    unittest.main()
