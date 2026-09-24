from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from zg_bench.ci.secret_scan import contains_secret, find_secret_leaks


class SecretScanTests(unittest.TestCase):
    def test_detects_secret_across_chunk_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "artifact.bin"
            path.write_bytes(b"abcSECRETtail")
            self.assertTrue(contains_secret(path, b"SECRET", chunk_size=5))
            self.assertEqual(find_secret_leaks([path], [b"SECRET"]), [path])

    def test_clean_and_missing_roots_are_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "clean.txt").write_text("safe")
            self.assertEqual(
                find_secret_leaks([root, root / "missing"], [b"SECRET"]), []
            )
