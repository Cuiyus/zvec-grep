"""Exercise code review against real Git objects, index state and raw bytes."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import continuation_code_review as review


class ContinuationCodeReviewTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="workspace-qa-code-review-")
        self.addCleanup(temporary.cleanup)
        self.repo = Path(temporary.name)
        self.git("init", "--quiet")
        self.git("config", "user.email", "offline-test@example.invalid")
        self.git("config", "user.name", "Offline test")
        self.write("allowed.py", b"original\n")
        self.write("deleted.dat", b"\0original binary\xff\r\n")
        self.commit()
        self.base = self.git("rev-parse", "HEAD").decode().strip()

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.repo), *args], check=True, capture_output=True).stdout

    def write(self, path, content):
        output = self.repo / path
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(content)

    def commit(self):
        self.git("add", "--all")
        self.git("commit", "--quiet", "-m", "Offline fixture commit")

    def config(self, allowed=None):
        return {"source_commit": self.base, "source_run_id": 34919707888,
                "source_protocol": review.PROTOCOL,
                "allowed_changed_paths": allowed or ["allowed.py"]}

    def test_add_modify_delete_binary_unicode_and_space_paths_hash_exact_committed_bytes(self):
        changed = b"changed\r\n\0\xfe"
        added = "新增内容\n".encode()
        name = "new files/中文.bin"
        self.write("allowed.py", changed)
        self.write(name, added)
        (self.repo / "deleted.dat").unlink()
        self.commit()
        result = review.build_review(self.repo, self.config(["allowed.py", "deleted.dat", name]))
        sha = lambda data: hashlib.sha256(data).hexdigest()
        self.assertEqual(result, {"base_commit": self.base,
            "head_commit": self.git("rev-parse", "HEAD").decode().strip(), "status": "verified",
            "changed_files": [
                {"path": "allowed.py", "before_sha256": sha(b"original\n"), "after_sha256": sha(changed)},
                {"path": "deleted.dat", "before_sha256": sha(b"\0original binary\xff\r\n"), "after_sha256": None},
                {"path": name, "before_sha256": None, "after_sha256": sha(added)}]})

    def test_rename_requires_both_exact_paths(self):
        self.git("mv", "allowed.py", "renamed.py")
        self.commit()
        with self.assertRaisesRegex(ValueError, "outside the exact allowlist: renamed.py"):
            review.build_review(self.repo, self.config())
        result = review.build_review(self.repo, self.config(["allowed.py", "renamed.py"]))
        self.assertEqual(len(result["changed_files"]), 2)
        self.assertIsNone(result["changed_files"][0]["after_sha256"])
        self.assertIsNone(result["changed_files"][1]["before_sha256"])

    def test_frozen_names_and_every_runtime_file_rejected_even_when_explicitly_allowed(self):
        paths = ["benchmarks/workspace-qa/data/lock.json", "benchmarks/workspace-qa/data/selection.json",
            "benchmarks/swe-qa-bench/runtime/Dockerfile", "benchmarks/workspace-qa/native_session.py",
            "benchmarks/workspace-qa/native_index.py", "benchmarks/workspace-qa/seed_cache.py",
            "uv.lock", "benchmarks/swe-qa-bench/runtime/package-lock.json", "runtime/new-file.txt"]
        for path in paths:
            self.write(path, b"changed frozen bytes")
        self.commit()
        with self.assertRaisesRegex(ValueError, "frozen experiment files") as caught:
            review.build_review(self.repo, self.config(paths))
        for path in paths:
            self.assertIn(path, str(caught.exception))

    def test_untracked_downloaded_artifacts_are_allowed(self):
        self.write("downloaded-artifact/runs/trial-results.json", b"{}")
        result = review.build_review(self.repo, self.config())
        self.assertEqual(result["changed_files"], [])
        self.assertEqual(result["head_commit"], self.base)

    def test_unstaged_and_staged_changes_fail_even_if_they_cancel_against_head(self):
        self.write("allowed.py", b"dirty working tree")
        with self.assertRaisesRegex(ValueError, "unchanged tracked"):
            review.build_review(self.repo, self.config())
        self.git("add", "allowed.py")
        with self.assertRaisesRegex(ValueError, "unchanged tracked"):
            review.build_review(self.repo, self.config())
        self.write("allowed.py", b"original\n")
        self.assertEqual(self.git("diff", "HEAD", "--name-only"), b"")
        with self.assertRaisesRegex(ValueError, "unchanged tracked"):
            review.build_review(self.repo, self.config())

    def test_invalid_configuration_does_not_execute_data_as_commands(self):
        invalid = [{"source_commit": "HEAD"}, {"source_commit": "0" * 40},
            {"source_run_id": True}, {"source_run_id": 0}, {"source_run_id": "$(touch stolen)"},
            {"source_protocol": "workspace-qa-qoder-v1"}, {"protocol": "old"},
            {"allowed_changed_paths": ["../allowed.py"]}, {"allowed_changed_paths": ["/allowed.py"]},
            {"allowed_changed_paths": ["*.py"]}, {"allowed_changed_paths": ["allowed.py", "allowed.py"]},
            {"allowed_changed_paths": ["allowed.py\nnext"]}, {"allowed_changed_paths": []}]
        for replacement in invalid:
            with self.subTest(replacement=replacement), self.assertRaises(ValueError):
                review.build_review(self.repo, {**self.config(), **replacement})
        self.assertFalse((self.repo / "stolen").exists())
        self.assertEqual(review.build_review(self.repo, {**self.config(), "source_run_id": "34919707888"})["status"], "verified")

    def test_symlinks_are_rejected_instead_of_hashing_their_target_name_as_code(self):
        (self.repo / "link.py").symlink_to("allowed.py")
        self.commit()
        with self.assertRaisesRegex(ValueError, "regular committed files"):
            review.build_review(self.repo, self.config(["link.py"]))

    def test_path_prefix_is_not_an_allowlist_match(self):
        self.write("allowed.py.extra", b"unreviewed")
        self.commit()
        with self.assertRaisesRegex(ValueError, "outside the exact allowlist"):
            review.build_review(self.repo, self.config())

    def test_cli_writes_review_and_does_not_overwrite_tracked_code(self):
        config_path = self.repo / "source-config.json"
        config_path.write_text(json.dumps(self.config()))
        output = self.repo / "artifact/code-review.json"
        command = [sys.executable, str(ROOT / "continuation_code_review.py"), "--source-config", str(config_path),
                   "--repo", str(self.repo), "--output"]
        result = subprocess.run(command + [str(output)], capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(json.loads(output.read_text())["status"], "verified")
        result = subprocess.run(command + [str(self.repo / "allowed.py")], capture_output=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn(b"cannot overwrite a tracked file", result.stderr)
        self.assertEqual((self.repo / "allowed.py").read_bytes(), b"original\n")


if __name__ == "__main__":
    unittest.main()
