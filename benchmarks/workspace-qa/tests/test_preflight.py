import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from subprocess import CompletedProcess

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location("workspace_run_task", ROOT / "run_task.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class PreflightTests(unittest.TestCase):
    def test_preflight_uses_standard_native_entry_and_no_direct_sdk(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "probe"
            with patch("qoder_probe.standalone_native_probe") as native, \
                    patch("runner.run_named", side_effect=AssertionError("No direct SDK route")):
                module.sdk_preflight(target)
            native.assert_called_once_with(target)

    def test_native_preflight_failure_propagates_without_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "probe"
            with patch("qoder_probe.standalone_native_probe", side_effect=RuntimeError("native install failed")) as native, \
                    self.assertRaisesRegex(RuntimeError, "native install"):
                module.sdk_preflight(target)
            native.assert_called_once_with(target)

    def test_remote_probe_requests_exact_model_and_never_records_credentials(self):
        body = {"model": "qwen3.7-text-embedding", "data": [{"embedding": [0.25] * 1024}], "usage": {"prompt_tokens": 12}}
        with tempfile.TemporaryDirectory() as tmp, patch.dict("os.environ", {"QWEN_API_KEY": "fixture-embedding-secret"}):
            target = Path(tmp) / "preflight.json"
            with patch.object(module.request, "urlopen", return_value=io.BytesIO(json.dumps(body).encode())) as fetch:
                module.embedding_preflight(target)
            req = fetch.call_args.args[0]
            self.assertEqual(json.loads(req.data)["model"], "qwen3.7-text-embedding")
            self.assertEqual(req.headers["Authorization"], "Bearer fixture-embedding-secret")
            record = json.loads(target.read_text())
            self.assertEqual(record["status"], "completed")
            self.assertFalse(record["included_in_agent_tokens"])
            self.assertNotIn("fixture-embedding-secret", target.read_text())

    def test_wrong_model_shape_or_nonfinite_vectors_stop_before_dataset(self):
        bodies = [
            {"model": "different-model", "data": [{"embedding": [0] * 1024}]},
            {"data": [{"embedding": [0] * 256}]},
            {"data": [{"embedding": [float('nan')] * 1024}]},
        ]
        for body in bodies:
            with self.subTest(body=list(body)), tempfile.TemporaryDirectory() as tmp, patch.dict("os.environ", {"QWEN_API_KEY": "fixture-secret"}):
                target = Path(tmp) / "preflight.json"
                with patch.object(module.request, "urlopen", return_value=io.BytesIO(json.dumps(body).encode())), self.assertRaises(ValueError):
                    module.embedding_preflight(target)
                self.assertEqual(json.loads(target.read_text())["status"], "failed")


if __name__ == '__main__':
    unittest.main()
