from contextlib import redirect_stdout
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import smoke_reuse


class RetiredBridgeSmokeTests(unittest.TestCase):
    def test_prior_smoke_never_unlocks_native_install_run(self):
        with tempfile.TemporaryDirectory() as folder, redirect_stdout(io.StringIO()):
            root = Path(folder)
            artifact = root / "prior-success"
            artifact.mkdir()
            (artifact / "smoke_validation.json").write_text('{"status":"valid"}')
            output = root / "output"
            with patch("subprocess.Popen", side_effect=AssertionError("must not run")):
                self.assertEqual(smoke_reuse.main(["--artifact-root", str(artifact), "--output", str(output)]), 1)
            self.assertFalse(output.exists())
            self.assertFalse(smoke_reuse.compatibility()["reuse_eligible"])

    def test_compatibility_outputs_explicit_false(self):
        with tempfile.TemporaryDirectory() as folder, redirect_stdout(io.StringIO()):
            output = Path(folder) / "github-output"
            self.assertEqual(smoke_reuse.main(["--check-compatibility", "--github-output", str(output)]), 0)
            self.assertEqual(output.read_text(), "reuse_eligible=false\n")
