import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from zg_bench.swe_qa.pipeline_artifacts import assemble, scan_credentials, GROUPS


class ArtifactBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.downloaded = self.root / "downloaded"
        self.downloaded.mkdir()
        for (agent, model), group in GROUPS.items():
            directory = self.downloaded / group
            directory.mkdir()
            (directory / "manifest.json").write_text(json.dumps({
                "agent": agent, "model": model,
                "ci_identity": {"GITHUB_RUN_ID": "123", "GITHUB_SHA": "abc"},
            }))
            (directory / "plan.json").write_text(json.dumps({"trials": [
                {"profile": profile, "status": "failed" if n == 0 else "planned"}
                for profile in ("baseline", "zvec-grep") for n in range(5)
            ]}))

    def test_retains_failed_and_not_started_trials(self):
        result = assemble(self.downloaded, self.root / "assembled", run_id="123", commit="abc")
        self.assertEqual(result["planned_e2e_trials"], 30)
        copied = json.loads((self.root / "assembled/opencode-glm52/plan.json").read_text())
        self.assertEqual(len(copied["trials"]), 10)
        self.assertEqual(copied["trials"][0]["status"], "failed")

    def test_stale_run_is_rejected_before_copy(self):
        with self.assertRaisesRegex(ValueError, "another run"):
            assemble(self.downloaded, self.root / "assembled", run_id="122", commit="abc")
        self.assertFalse((self.root / "assembled").exists())

    def test_duplicate_group_is_not_silently_replaced(self):
        import shutil
        shutil.copytree(self.downloaded / "opencode-glm52", self.downloaded / "another-attempt")
        with self.assertRaisesRegex(ValueError, "Multiple E2E attempts"):
            assemble(self.downloaded, self.root / "assembled", run_id="123", commit="abc")

    def test_secret_across_read_boundary_blocks_upload(self):
        (self.root / "raw.txt").write_bytes(b"x" * (1024 * 1024 - 3) + b"test-secret-value")
        with patch.dict(os.environ, {"GLM_API_KEY": "test-secret-value"}):
            with self.assertRaisesRegex(ValueError, "upload blocked") as error:
                scan_credentials(self.root)
        self.assertNotIn("test-secret-value", str(error.exception))


if __name__ == "__main__":
    unittest.main()
