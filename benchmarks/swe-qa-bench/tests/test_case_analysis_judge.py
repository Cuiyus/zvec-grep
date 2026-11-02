from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from zg_bench.case_judge import recorded_completion


class JudgeEvidenceTests(unittest.TestCase):
    def test_records_prompt_and_response_without_authentication(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            answer = {"choices": [{"message": {"content": '{"correctness":17}'}}]}
            received = []

            def complete(**kwargs):
                received.append(kwargs)
                return answer

            call = recorded_completion(complete, root)
            result = call(api_key="secret-sentinel", model="openai/qwen3.8-max",
                          messages=[{"role": "user", "content": "rubric and answer"}])
            self.assertEqual(result, answer)
            self.assertEqual(received[0]["api_key"], "secret-sentinel")
            self.assertEqual(json.loads(next(root.glob("*.response.json")).read_text()), answer)
            request = json.loads(next(root.glob("*.request.json")).read_text())
            self.assertEqual(request["messages"], received[0]["messages"])
            self.assertNotIn("api_key", request)
            for path in root.iterdir():
                self.assertNotIn("secret-sentinel", path.read_text())

    def test_transport_failure_is_archived_without_sensitive_error_text(self):
        with tempfile.TemporaryDirectory() as directory:
            def fail(**kwargs):
                raise RuntimeError("secret-sentinel")

            root = Path(directory)
            with self.assertRaises(RuntimeError):
                recorded_completion(fail, root)(api_key="secret-sentinel", messages=[])
            completion = json.loads(next(root.glob("*.completion.json")).read_text())
            self.assertFalse(completion["complete"])
            self.assertEqual(completion["error_type"], "RuntimeError")
            self.assertFalse(list(root.glob("*.response.json")))
            for path in root.iterdir():
                self.assertNotIn("secret-sentinel", path.read_text())
